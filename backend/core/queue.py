from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Event, RLock
from uuid import uuid4
from .db import now
from .logs import scrub


class Cancelled(Exception):
    pass


class TaskQueue:
    def __init__(self, db, handlers, concurrency=2, retries=1, on_finished=None):
        self.db, self.handlers, self.retries = db, handlers, retries
        self.on_finished = on_finished
        self.lock = RLock()
        self.stopping = Event()
        self.events = {}
        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        db.execute("UPDATE tasks SET status='failed', error='应用退出时任务中断' WHERE status='running'")
        for row in db.all("SELECT id FROM tasks WHERE status='pending'"):
            self._submit(row['id'])

    def get(self, task_id):
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            row.pop('payload')
        return row

    def list(self):
        return [self.get(r['id']) for r in self.db.all("SELECT id FROM tasks ORDER BY created_at DESC")]

    def create(self, kind, payload, max_retries=None):
        with self.lock:
            task_id = uuid4().hex
            self.db.execute("INSERT INTO tasks (id,kind,payload,status,max_retries,created_at) VALUES (?,?,?,'pending',?,?)",
                            (task_id, kind, json.dumps(payload), self.retries if max_retries is None else max_retries, now()))
            self._submit(task_id)
            return self.get(task_id)

    def _submit(self, task_id):
        event = Event()
        self.events[task_id] = event
        self.executor.submit(self._run_finished, task_id, event)

    def _run_finished(self, task_id, event):
        try:
            self._run(task_id, event)
        finally:
            if self.on_finished:
                self.on_finished(self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,)))

    def _run(self, task_id, event):
        def cancelled():
            return event.is_set() or self.stopping.is_set()

        def progress(value):
            if cancelled():
                raise Cancelled()
            self.db.execute("UPDATE tasks SET progress=? WHERE id=? AND status='running'",
                            (max(0, min(99, value)), task_id))

        while not cancelled():
            with self.lock:
                row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
                if row['status'] != 'pending':
                    return
                self.db.execute("UPDATE tasks SET status='running', attempts=attempts+1, error=NULL WHERE id=?", (task_id,))
            try:
                self.handlers[row['kind']](json.loads(row['payload']), progress, cancelled)
                with self.lock:
                    self.db.execute("UPDATE tasks SET status=?, progress=? WHERE id=? AND status='running'",
                        ('cancelled' if cancelled() else 'succeeded', 0 if cancelled() else 100, task_id))
                return
            except Cancelled:
                self.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
                return
            except Exception as exc:
                # Never persist third-party exception text in the DB: it may contain credentials or URLs.
                # The log file gets the type and a scrubbed, truncated message for support purposes only.
                logging.getLogger('mavobox.queue').warning('task %s (%s) failed: %s: %s', task_id[:8], row['kind'], type(exc).__name__, scrub(str(exc)[:300]))
                with self.lock:
                    current = self.get(task_id)
                    if cancelled() or current['status'] == 'cancelled':
                        self.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
                        return
                    retry = current['attempts'] <= current['max_retries']
                    self.db.execute("UPDATE tasks SET status=?, progress=0, error=? WHERE id=?",
                        ('pending' if retry else 'failed', '任务执行失败，请检查网络、媒体或 ffmpeg', task_id))
                    if not retry:
                        return

    def cancel(self, task_id):
        with self.lock:
            row = self.get(task_id)
            if row['status'] not in ('pending', 'running'):
                raise ValueError("只能取消等待或运行中的任务")
            self.events[task_id].set()
            self.db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
            return self.get(task_id)

    def retry(self, task_id):
        with self.lock:
            if self.get(task_id)['status'] != 'failed':
                raise ValueError("只能重试失败任务")
            self.db.execute("UPDATE tasks SET status='pending',progress=0,error=NULL,attempts=0 WHERE id=?", (task_id,))
            self._submit(task_id)
            return self.get(task_id)

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
