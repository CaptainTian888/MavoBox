import socket
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from api.app import create_app
from core.config import Settings
from core.secrets import MemorySecrets
from modules.gateway_manager import GatewayError, GatewayManager, PROVIDER_NAME

FAKE = r'''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
assert os.environ["HOST"] == "127.0.0.1" and os.environ["AUTH_KEY"] and os.environ["DATA_DIR"]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "version": "fake"}).encode())
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / 'fake_gateway.py'
    script.write_text(FAKE)
    return [sys.executable, str(script)]


def manager(tmp_path, fake, secrets=None):
    return GatewayManager(Settings(tmp_path / 'd'), secrets or MemorySecrets(), port=free_port(), command=fake)


def test_start_stop_status_and_persisted_enable_flag(tmp_path, fake):
    m = manager(tmp_path, fake)
    assert m.status()['running'] is False and m.status()['enabled'] is False
    m.set_enabled(True)
    try:
        st = m.status()
        assert st['running'] and st['managed'] and st['enabled'] and st['openai_base_url'].endswith(f"{m.port}/v1")
        # a second manager (e.g. after an app restart) sees the flag and adopts the running gateway
        other = GatewayManager(Settings(tmp_path / 'd'), MemorySecrets(), port=m.port, command=fake)
        assert other.enabled() and other.status()['running']
        other.start()
        assert other.adopted and not other.owned_alive()
    finally:
        m.stop()
    time.sleep(0.3)
    assert m.status()['running'] is False


def test_admin_key_is_generated_once_and_kept_in_secret_store(tmp_path, fake):
    secrets = MemorySecrets()
    m = manager(tmp_path, fake, secrets)
    first = m.admin_key()
    assert len(first) >= 24 and m.admin_key() == first
    assert first not in str(list((tmp_path / 'd').rglob('*.json')) and [p.read_text() for p in (tmp_path / 'd').rglob('*.json')])


def test_port_taken_by_something_else_is_a_clear_error(tmp_path, fake):
    with socket.socket() as blocker:
        blocker.bind(('127.0.0.1', 0))
        blocker.listen()
        m = GatewayManager(Settings(tmp_path / 'd'), MemorySecrets(), port=blocker.getsockname()[1], command=fake)
        with pytest.raises(GatewayError, match='占用'):
            m.start()


def test_gateway_that_dies_on_startup_reports_it(tmp_path):
    m = GatewayManager(Settings(tmp_path / 'd'), MemorySecrets(), port=free_port(), command=[sys.executable, '-c', 'raise SystemExit(3)'])
    with pytest.raises(GatewayError, match='立即退出'):
        m.start(wait_seconds=10)


def test_missing_binary_is_reported_not_crashed(tmp_path, monkeypatch):
    monkeypatch.setattr('modules.gateway_manager.find_binary', lambda: None)
    m = GatewayManager(Settings(tmp_path / 'd'), MemorySecrets(), port=free_port())
    assert m.status()['available'] is False
    with pytest.raises(GatewayError, match='没有内置'):
        m.start()
    m.autostart()   # must not raise


def test_api_enable_connect_and_secret_handling(tmp_path, fake):
    secrets = MemorySecrets()
    app = create_app(Settings(tmp_path / 'd'), secrets, gateway_options=dict(port=free_port(), command=fake))
    with TestClient(app) as c:
        assert c.get('/api/gateway').json()['running'] is False
        st = c.post('/api/gateway/enable', json={'enabled': True}).json()
        assert st['running'] and st['enabled']
        key = c.post('/api/gateway/admin-key').json()['key']
        assert len(key) >= 24
        short = c.post('/api/gateway/connect', json={'access_key': 'abc'})
        assert short.status_code == 422
        r = c.post('/api/gateway/connect', json={'access_key': 'sk-gateway-access-key-123', 'model': 'm1'}).json()
        assert r['created'] and r['provider']['name'] == PROVIDER_NAME and r['provider']['base_url'].endswith('/v1')
        assert 'gateway-access-key' not in str(r)
        assert secrets.get(r['provider']['id']) == 'sk-gateway-access-key-123'
        again = c.post('/api/gateway/connect', json={'access_key': 'sk-another-key-456789', 'model': 'm2'}).json()
        assert again['created'] is False and again['provider']['id'] == r['provider']['id'] and again['provider']['model'] == 'm2'
        assert len([p for p in c.get('/api/providers').json() if p['name'] == PROVIDER_NAME]) == 1
        off = c.post('/api/gateway/enable', json={'enabled': False}).json()
        assert off['running'] is False and off['enabled'] is False
    # the app shutting down also stopped the gateway (nothing left listening)
    assert app.state.gateway.healthy() is False


def test_leftover_gateway_of_ours_is_adopted_and_can_be_stopped(tmp_path, monkeypatch):
    """After a crash the old gateway keeps running. The next start adopts it, and turning the switch off
    stops it - but only because the pid file proves it is a gpt-load of ours."""
    import subprocess
    script = tmp_path / 'gpt-load-fake.py'
    script.write_text(FAKE)
    port = free_port()
    settings = Settings(tmp_path / 'd')
    first = GatewayManager(settings, MemorySecrets(), port=port, command=[sys.executable, str(script)])
    first.start()
    leftover = first.proc.pid
    first.proc = None                      # simulate the backend dying without stopping its child
    monkeypatch.setattr('modules.gateway_manager.pid_is_gateway', lambda pid: pid == leftover)
    second = GatewayManager(settings, MemorySecrets(), port=port, command=[sys.executable, str(script)])
    second.start()
    assert second.adopted and second.adopted_pid == leftover
    second.set_enabled(False)
    time.sleep(0.5)
    assert second.healthy() is False
    assert not (tmp_path / 'd' / 'gateway' / 'gateway.pid').exists()


def test_adopted_gateway_without_proof_is_never_killed(tmp_path, monkeypatch, fake):
    port = free_port()
    settings = Settings(tmp_path / 'd')
    owner = GatewayManager(settings, MemorySecrets(), port=port, command=fake)
    owner.start()
    try:
        monkeypatch.setattr('modules.gateway_manager.pid_is_gateway', lambda pid: False)   # e.g. recycled pid / user's own gateway
        other = GatewayManager(settings, MemorySecrets(), port=port, command=fake)
        other.start()
        assert other.adopted and other.adopted_pid is None
        other.stop()
        assert owner.healthy()              # still running: we only touch what we can prove is ours
    finally:
        owner.stop()
