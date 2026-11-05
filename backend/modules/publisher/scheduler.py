from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, RLock, Thread
from .driver import LoginRequired, NeedsAttention, Transient
from .service import MESSAGES, safe_result_url, utc


class Scheduler:
    """One scheduler per local app. Claims are transactional, browser leases include login.

    Wall clock, random jitter and driver can all be replaced offline. Retry deadlines
    are persisted separately so original scheduling intent remains visible.
    """
    def __init__(self, service, driver, now=None):
        self.service, self.db, self.driver = service, service.db, driver
        self.settings = service.settings
        self.now = now or service.now
        self.lock = RLock()
        self.stopping = Event()
        self.active = {}  # job/login token -> (account id, proxy id)
        self.executor = ThreadPoolExecutor(max_workers=self.settings.publish_concurrency)
        self.thread = None
        self.last_tick = None
        self.recover()

    @property
    def active_count(self):
        with self.lock:
            return len(self.active)

    def due(self, row):
        original = utc(row['scheduled_at']) + timedelta(seconds=row['jitter_seconds'])
        return max(original, utc(row['next_attempt_at'])) if row['next_attempt_at'] else original

    def recover(self):
        stamp = utc(self.now()).isoformat()
        for row in self.db.all("SELECT * FROM publish_jobs WHERE status IN ('running','scheduled')"):
            if row['status'] == 'running':
                state, code = 'needs_attention', 'restart'
            elif (utc(self.now()) - self.due(row)).total_seconds() > self.settings.publish_missed_grace_seconds:
                state, code = 'missed', 'missed'
            else:
                continue
            self.db.execute('UPDATE publish_jobs SET status=?,error=?,updated_at=?,finished_at=? WHERE id=?',
                (state, MESSAGES[code], stamp, stamp, row['id']))
            self.service.log(row['id'], code, 'warning')

    def expire_missed(self, current, since):
        """Mark scheduled jobs that are past the grace window as missed (used after a pause/sleep)."""
        stamp = current.isoformat()
        for row in self.db.all("SELECT * FROM publish_jobs WHERE status='scheduled'"):
            due = self.due(row)
            # Only jobs that became due during the pause never had a chance to run on time;
            # jobs held back by interval/busy rules were already due before it.
            if due > since and (current - due).total_seconds() > self.settings.publish_missed_grace_seconds:
                changed = self.db.execute_rowcount(
                    "UPDATE publish_jobs SET status='missed',error=?,updated_at=?,finished_at=? WHERE id=? AND status='scheduled'",
                    (MESSAGES['missed'], stamp, stamp, row['id']))
                if changed:
                    self.service.log(row['id'], 'missed', 'warning')

    def available(self, account):
        return (len(self.active) < self.settings.publish_concurrency
                and all(a != account['id'] and (not account['proxy_id'] or p != account['proxy_id'])
                        for a, p in self.active.values()))

    def busy(self, account_id=None, proxy_id=None):
        with self.lock:
            return any((account_id and a == account_id) or (proxy_id and p == proxy_id)
                       for a, p in self.active.values())

    def tick(self):
        with self.lock:
            if self.stopping.is_set():
                return
            current = utc(self.now())
            # A long gap between ticks means the machine slept or the process was paused: do not
            # publish stale jobs late, apply the same missed-grace rule that startup recovery uses.
            if self.last_tick and (current - self.last_tick).total_seconds() > max(self.settings.publish_poll_seconds * 4, 120):
                self.expire_missed(current, self.last_tick)
            self.last_tick = current
            rows = self.db.all("SELECT * FROM publish_jobs WHERE status='scheduled' ORDER BY scheduled_at,created_at,id")
            for row in rows:
                if self.due(row) > current:
                    continue
                account = self.service.account(row['account_id'])
                if account['status'] in ('disabled', 'login_required', 'banned_suspected'):
                    self.db.execute("UPDATE publish_jobs SET status='needs_attention',error=?,updated_at=? WHERE id=? AND status='scheduled'",
                        (MESSAGES['login'] if account['status'] == 'login_required' else MESSAGES['attention'], current.isoformat(), row['id']))
                    self.service.log(row['id'], 'attention', 'warning')
                    continue
                if not self.available(account):
                    continue
                last = self.db.one("SELECT MAX(finished_at) AS ts FROM publish_jobs WHERE account_id=? AND status='succeeded'", (account['id'],))
                if last['ts'] and (current - utc(last['ts'])).total_seconds() < self.settings.publish_min_interval_seconds:
                    continue
                with self.db.connect() as con:
                    claimed = con.execute("UPDATE publish_jobs SET status='running',attempts=attempts+1,started_at=?,updated_at=?,error=NULL WHERE id=? AND status='scheduled'",
                        (current.isoformat(), current.isoformat(), row['id'])).rowcount
                if not claimed:
                    continue
                self.active[row['id']] = (account['id'], account['proxy_id'])
                self.executor.submit(self._run, account, row['id'])

    def _run(self, account, identifier):
        status, code, result, retry_at = 'failed', 'failed', None, None
        try:
            self.service.log(identifier, 'started')
            job = self.service.job(identifier)
            # Re-check existence and specs at execution time, not only at creation.
            try:
                warnings = self.service.media(job['video_path'])
            except ValueError:
                raise NeedsAttention() from None
            for warning in warnings:
                self.service.log(identifier, warning, 'warning')
            result = safe_result_url(self.driver.publish(account, job))
            status, code = 'succeeded', 'success'
            self.db.execute("UPDATE accounts SET status='logged_in',last_error=NULL,last_checked_at=? WHERE id=?",
                (utc(self.now()).isoformat(), account['id']))
        except LoginRequired:
            status, code = 'needs_attention', 'login'
            self.db.execute("UPDATE accounts SET status='login_required',last_error=?,last_checked_at=? WHERE id=?",
                (MESSAGES['login'], utc(self.now()).isoformat(), account['id']))
        except NeedsAttention:
            status, code = 'needs_attention', 'attention'
        except Transient:
            row = self.service.job(identifier)
            code = 'transient'
            if row['attempts'] <= row['max_retries'] and not self.stopping.is_set():
                status = 'scheduled'
                retry_at = (utc(self.now()) + timedelta(seconds=min(3600, 60 * 2 ** (row['attempts'] - 1)))).isoformat()
        except Exception:
            # Unknown failures may occur after submission: never risk duplicate posting.
            status, code = 'needs_attention', 'attention'
        finally:
            with self.lock:
                stamp = utc(self.now()).isoformat()
                try:
                    self.db.execute('''UPDATE publish_jobs SET status=?,error=?,result_url=?,next_attempt_at=?,
                        updated_at=?,finished_at=? WHERE id=?''', (status, None if status == 'succeeded' else MESSAGES[code],
                        result, retry_at, stamp, None if status == 'scheduled' else stamp, identifier))
                    self.service.log(identifier, code, 'info' if status == 'succeeded' else 'warning')
                finally:
                    self.active.pop(identifier, None)

    def account_operation(self, account_id, operation):
        token = 'account:' + account_id
        with self.lock:
            account = self.service.account(account_id)
            if account['status'] == 'disabled' or not self.available(account):
                raise ValueError('账号、代理正在使用或已达到并发上限')
            self.active[token] = (account_id, account['proxy_id'])
        try:
            try:
                logged_in = getattr(self.driver, operation)(account)
                state, error = ('logged_in', None) if logged_in else ('login_required', MESSAGES['login'])
            except LoginRequired:
                state, error = 'login_required', MESSAGES['login']
            except Exception:
                state, error = 'unknown', MESSAGES['attention']
            self.db.execute('UPDATE accounts SET status=?,last_error=?,last_checked_at=? WHERE id=?',
                (state, error, utc(self.now()).isoformat(), account_id))
            return self.service.account(account_id)
        finally:
            with self.lock:
                self.active.pop(token, None)

    def start(self):
        def poll():
            while not self.stopping.is_set():
                try:
                    self.tick()
                except Exception:
                    # No third-party exception text is logged. Next poll retries DB access.
                    pass
                self.stopping.wait(self.settings.publish_poll_seconds)
        self.thread = Thread(target=poll, name='publisher-scheduler', daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
        self.executor.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
