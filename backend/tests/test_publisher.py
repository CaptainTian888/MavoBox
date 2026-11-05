from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4
import time

import pytest
from core.config import Settings
from core.db import Database
from core.secrets import MemorySecrets
from modules.publisher.driver import FakeDriver, LoginRequired, NeedsAttention, Transient
from modules.publisher.service import PublisherService
from modules.publisher.scheduler import Scheduler


@pytest.fixture
def setup(tmp_path):
    clock = [datetime(2026, 11, 4, 12, tzinfo=timezone.utc)]
    settings = Settings(tmp_path, publish_min_interval_seconds=1800)
    db = Database(tmp_path / 'test.sqlite3')
    service = PublisherService(db, MemorySecrets(), settings, now=lambda: clock[0], randint=lambda a, b: b)
    account = service.save_account(dict(name='first', group_id=None, proxy_id=None, status='unknown'))
    path = tmp_path / 'test.mp4'
    path.write_bytes(b'local fixture')
    yield service, clock, account, path


def create(setup, **kw):
    service, clock, account, path = setup
    return service.create_jobs(dict(account_ids=[account['id']], video_path=str(path),
        scheduled_at=clock[0], caption='hello {account_name}', **kw))[0]


def wait(scheduler):
    deadline = time.monotonic() + 3
    while scheduler.active_count and time.monotonic() < deadline:
        time.sleep(.01)
    assert scheduler.active_count == 0


def test_due_jitter_and_interval(setup):
    service, clock, account, _ = setup
    first = create(setup, jitter_window_seconds=20)
    second = create(setup)
    driver = FakeDriver()
    with Scheduler(service, driver, now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
        assert service.job(second['id'])['status'] == 'succeeded'
        clock[0] += timedelta(seconds=20)
        scheduler.tick(); wait(scheduler)
        assert service.job(first['id'])['status'] == 'scheduled'
        clock[0] += timedelta(minutes=30)
        scheduler.tick(); wait(scheduler)
        assert service.job(first['id'])['status'] == 'succeeded'
    assert len(driver.published) == 2


def test_retry_backoff_and_sanitized_error(setup, caplog):
    service, clock, _, _ = setup
    secret = uuid4().hex
    driver = FakeDriver(errors=[Transient(secret)])
    job = create(setup, max_retries=1)
    with Scheduler(service, driver, now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
        assert service.job(job['id'])['status'] == 'scheduled'
        scheduler.tick(); wait(scheduler)
        assert service.job(job['id'])['attempts'] == 1
        clock[0] += timedelta(seconds=60)
        scheduler.tick(); wait(scheduler)
        assert service.job(job['id'])['status'] == 'succeeded'
    assert secret not in str(service.job(job['id'])) + caplog.text
    service.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert secret.encode() not in service.db.path.read_bytes()


def test_login_required_and_recovery(setup):
    service, clock, account, _ = setup
    job = create(setup)
    with Scheduler(service, FakeDriver(errors=[LoginRequired('secret')]), now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
    assert service.job(job['id'])['status'] == 'needs_attention'
    assert service.account(account['id'])['status'] == 'login_required'
    running = create(setup)
    service.db.execute("UPDATE publish_jobs SET status='running' WHERE id=?", (running['id'],))
    missed = create(setup)
    clock[0] += timedelta(minutes=11)
    with Scheduler(service, FakeDriver(), now=lambda: clock[0]):
        pass
    assert service.job(running['id'])['status'] == 'needs_attention'
    assert service.job(missed['id'])['status'] == 'missed'


def test_batch_cancel_reschedule(setup):
    service, clock, account, path = setup
    other = service.save_account(dict(name='second'))
    jobs = service.create_jobs(dict(account_ids=[account['id'], other['id']], video_path=str(path),
        scheduled_at=clock[0], caption='{account_name} {index}', stagger_minutes=5))
    assert jobs[1]['caption'] == 'second 2'
    assert datetime.fromisoformat(jobs[1]['scheduled_at']) - datetime.fromisoformat(jobs[0]['scheduled_at']) == timedelta(minutes=5)
    assert service.action(jobs[0]['id'], 'cancel')['status'] == 'cancelled'
    future = clock[0] + timedelta(hours=1)
    assert service.action(jobs[1]['id'], 'reschedule', future)['scheduled_at'] == future.isoformat()
    assert service.action(jobs[0]['id'], 'retry')['attempts'] == 0


def test_concurrency_account_and_proxy_exclusion(setup):
    service, clock, first, path = setup
    proxy = service.save_proxy(dict(name='p', server='http://localhost:8080', username='', password=None))
    first = service.save_account(dict(name='first', proxy_id=proxy['id']), first['id'])
    second = service.save_account(dict(name='second', proxy_id=proxy['id']))
    third = service.save_account(dict(name='third'))
    gate = Event()
    class Blocking(FakeDriver):
        def publish(self, account, job):
            gate.wait(3)
            return super().publish(account, job)
    for account in (first, first, second, third):
        service.create_jobs(dict(account_ids=[account['id']], video_path=str(path), scheduled_at=clock[0]))
    with Scheduler(service, Blocking(), now=lambda: clock[0]) as scheduler:
        try:
            scheduler.tick()
            rows = service.db.all("SELECT account_id FROM publish_jobs WHERE status='running'")
            assert len(rows) == 2
            assert len({row['account_id'] for row in rows}) == 2
            assert third['id'] in {row['account_id'] for row in rows}
            assert scheduler.active_count == 2
        finally:
            gate.set()
        wait(scheduler)


def test_proxy_password_never_echoes(client, caplog):
    value = uuid4().hex
    data = dict(name='proxy', server='http://127.0.0.1:8080', username='user', password=value)
    response = client.post('/api/proxies', json=data)
    assert response.status_code == 201
    assert response.json()['password_status'] == '已设置'
    listing = client.get('/api/proxies')
    invalid = client.post('/api/proxies', json={**data, 'extra': value})
    assert invalid.status_code == 422
    assert value not in response.text + listing.text + invalid.text + caplog.text
    db = client.app.state.db
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert value.encode() not in db.path.read_bytes()
    assert client.post('/api/proxies', json={**data, 'server': 'http://user:pass@host:80'}).status_code == 422


def test_retry_exhaustion_and_no_retry_for_unknown_result(setup):
    service, clock, _, _ = setup
    first = create(setup, max_retries=1)
    with Scheduler(service, FakeDriver(errors=[Transient(), Transient()]), now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
        clock[0] += timedelta(seconds=59)
        scheduler.tick(); wait(scheduler)
        assert service.job(first['id'])['attempts'] == 1
        clock[0] += timedelta(seconds=1)
        scheduler.tick(); wait(scheduler)
        assert service.job(first['id'])['status'] == 'failed'
        assert service.job(first['id'])['attempts'] == 2
    second = create(setup, max_retries=10)
    with Scheduler(service, FakeDriver(errors=[NeedsAttention(uuid4().hex)]), now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
        clock[0] += timedelta(hours=1)
        scheduler.tick(); wait(scheduler)
        assert service.job(second['id'])['status'] == 'needs_attention'
        assert service.job(second['id'])['attempts'] == 1


def test_recovery_keeps_grace_and_future_jitter(setup):
    service, clock, _, _ = setup
    within = create(setup)
    delayed = create(setup, jitter_window_seconds=1200)
    clock[0] += timedelta(minutes=10)
    with Scheduler(service, FakeDriver(), now=lambda: clock[0]) as scheduler:
        assert service.job(within['id'])['status'] == 'scheduled'
        assert service.job(delayed['id'])['status'] == 'scheduled'
        scheduler.tick(); wait(scheduler)
        assert service.job(within['id'])['status'] == 'succeeded'
        assert service.job(delayed['id'])['status'] == 'scheduled'


def test_running_cannot_cancel_and_account_operations_share_lease(setup):
    service, clock, account, _ = setup
    job = create(setup)
    gate = Event()
    class Blocking(FakeDriver):
        def publish(self, account, job):
            gate.wait(3)
            return None
    with Scheduler(service, Blocking(), now=lambda: clock[0]) as scheduler:
        try:
            scheduler.tick()
            with pytest.raises(ValueError):
                service.action(job['id'], 'cancel')
            with pytest.raises(ValueError):
                scheduler.account_operation(account['id'], 'open_login')
        finally:
            gate.set()
        wait(scheduler)


def test_batch_validation_is_atomic(setup):
    service, clock, account, path = setup
    with pytest.raises(ValueError):
        service.create_jobs(dict(account_ids=[account['id'], 'missing'], video_path=str(path), scheduled_at=clock[0]))
    with pytest.raises(ValueError):
        service.create_jobs(dict(account_ids=[account['id'], account['id']], video_path=str(path), scheduled_at=clock[0]))
    assert service.db.all('SELECT * FROM publish_jobs') == []


def test_manual_confirmation_and_credential_url_discard(setup):
    service, clock, _, _ = setup
    job = create(setup)
    with Scheduler(service, FakeDriver(errors=[NeedsAttention()]), now=lambda: clock[0]) as scheduler:
        scheduler.tick(); wait(scheduler)
    row = service.action(job['id'], 'complete', result_url='https://www.tiktok.com/@local/video/1234')
    assert row['status'] == 'succeeded'
    assert row['result_url'] == 'https://www.tiktok.com/@local/video/1234'
    from modules.publisher.service import safe_result_url
    assert safe_result_url('https://user:password@www.tiktok.com/@local/video/1234') is None
    assert safe_result_url('https://www.tiktok.com/@local/video/1234?cookie=secret') is None


def test_publish_api_crud_and_filters(tmp_path):
    from fastapi.testclient import TestClient
    from api.app import create_app
    clock = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    app = create_app(Settings(tmp_path / 'data'), MemorySecrets(), FakeDriver(), lambda: clock)
    video = tmp_path / 'local.mp4'
    video.write_bytes(b'local fixture')
    with TestClient(app) as client:
        group = client.post('/api/account-groups', json={'name': 'matrix'}).json()
        proxy = client.post('/api/proxies', json={'name': 'local', 'server': 'http://localhost:8080'}).json()
        accounts = [client.post('/api/accounts', json={'name': n, 'group_id': group['id'], 'proxy_id': proxy['id']}).json() for n in ('one', 'two')]
        assert client.post(f"/api/accounts/{accounts[0]['id']}/check").json()['status'] == 'logged_in'
        response = client.post(f"/api/accounts/{accounts[1]['id']}/login")
        assert response.status_code == 202
        task_id = response.json()['id']
        deadline = time.monotonic() + 3
        while client.get(f'/api/tasks/{task_id}').json()['status'] not in ('succeeded', 'failed') and time.monotonic() < deadline:
            time.sleep(.01)
        assert client.get(f'/api/tasks/{task_id}').json()['status'] == 'succeeded'
        body = dict(account_ids=[a['id'] for a in accounts], video_path=str(video), caption='{account_name} {index}', scheduled_at='2026-11-05T01:00:00+08:00', stagger_minutes=5)
        response = client.post('/api/publish-jobs/batch', json=body)
        assert response.status_code == 201
        first, second = response.json()
        assert first['scheduled_at'] == '2026-11-04T17:00:00+00:00'
        assert second['scheduled_at'] == '2026-11-04T17:05:00+00:00'
        assert first['warnings']
        assert client.get('/api/publish-jobs', params={'account_id': accounts[0]['id'], 'status': 'scheduled'}).json()[0]['id'] == first['id']
        assert client.get('/api/publish-jobs', params={'date_from': '2026-11-04T17:04:00Z'}).json()[0]['id'] == second['id']
        assert client.get('/api/publish-jobs', params={'date_from': '2026-11-04T00:00:00'}).status_code == 422
        assert client.post('/api/publish-jobs', json={**body, 'scheduled_at': '2026-11-05T00:00:00'}).status_code == 422
        assert client.post(f"/api/publish-jobs/{first['id']}/cancel").json()['status'] == 'cancelled'
        assert client.post(f"/api/publish-jobs/{first['id']}/retry").json()['status'] == 'scheduled'
        assert client.post(f"/api/publish-jobs/{first['id']}/reschedule", json={'scheduled_at': '2026-11-06T00:00:00Z'}).status_code == 200
        assert client.get(f"/api/publish-jobs/{first['id']}").json()['logs']
        assert client.post(f"/api/publish-jobs/{first['id']}/complete", json={}).status_code == 409
        assert client.delete(f"/api/accounts/{accounts[0]['id']}").status_code == 409
        assert client.delete(f"/api/proxies/{proxy['id']}").status_code == 409
        assert client.delete(f"/api/account-groups/{group['id']}").status_code == 204
        assert all(a['group_id'] is None for a in client.get('/api/accounts').json())


def test_selectors_with_local_html():
    playwright = pytest.importorskip('playwright.sync_api')
    from modules.publisher import selectors as S
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception:
            pytest.skip('Chromium 未安装或沙箱禁止启动')
        try:
            page = browser.new_page()
            page.set_content('''<input type="file" accept="video/mp4">
                <div contenteditable="true" role="textbox"></div>
                <label>Comment<input type="checkbox"></label>
                <label>Duet<input type="checkbox"></label>
                <label>Stitch<input type="checkbox"></label>
                <button role="switch" aria-label="AI-generated content" aria-checked="false"
                  onclick="this.setAttribute('aria-checked', this.getAttribute('aria-checked') === 'true' ? 'false' : 'true')">AI</button>
                <button data-e2e="post_video_button">Post</button>''')
            scope = S.root(page)
            S.first(scope, S.FILE, state='attached')
            S.first(scope, S.CAPTION).fill('hello #local')
            S.set_toggle(scope, S.COMMENT, True)
            S.set_toggle(scope, S.AI, True)
            assert scope.locator(S.COMMENT).is_checked()
            assert scope.locator(S.AI).get_attribute('aria-checked') == 'true'
            assert scope.locator(S.CAPTION).inner_text() == 'hello #local'
            assert S.first(scope, S.POST).is_enabled()
        finally:
            browser.close()


def test_per_account_browser_settings_and_migration(tmp_path):
    import sqlite3
    # Reopen an existing M1 account table from before optional browser overrides.
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as con:
        con.execute('''CREATE TABLE accounts (id TEXT PRIMARY KEY,name TEXT,group_id TEXT,proxy_id TEXT,
            profile_dir TEXT,status TEXT,last_checked_at TEXT,last_error TEXT,created_at TEXT)''')
    db = Database(path)
    service = PublisherService(db, MemorySecrets(), Settings(tmp_path))
    account = service.save_account(dict(name='local', browser_locale='ja-JP', browser_timezone='Asia/Tokyo', browser_width=1440, browser_height=900))
    Database(path)  # Idempotent migration.
    assert service.account(account['id'])['browser_locale'] == 'ja-JP'
    from modules.publisher.playwright_driver import PlaywrightDriver
    options = PlaywrightDriver(service).browser_options(account)
    assert options['locale'] == 'ja-JP'
    assert options['timezone_id'] == 'Asia/Tokyo'
    assert options['viewport'] == {'width': 1440, 'height': 900}
