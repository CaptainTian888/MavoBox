import datetime as dt
import pytest
from fastapi.testclient import TestClient
from api.app import create_app
from core import license as lic
from core.config import Settings
from core.secrets import MemorySecrets

PEM, PUB = lic.generate_keypair()
PRIVATE = lic.load_private(PEM)
MACHINE = 'AAAAA-BBBBB-CCCCC-DDDDD'


def token(**kw):
    payload = dict(v=1, name='学员甲', machine=MACHINE, exp=None, iat='2026-11-05')
    payload.update(kw)
    return lic.sign(payload, PRIVATE)


def test_valid_bound_and_wildcard_codes():
    assert lic.verify(token(), PUB, MACHINE)['name'] == '学员甲'
    assert lic.verify(token(machine='*'), PUB, 'ANY-OTHER')['machine'] == '*'


def test_wrong_machine_expired_tampered_and_foreign_key_rejected():
    with pytest.raises(lic.LicenseError, match='其他电脑'):
        lic.verify(token(), PUB, 'ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ')
    with pytest.raises(lic.LicenseError, match='过期'):
        lic.verify(token(exp='2026-01-01'), PUB, MACHINE, today=dt.date(2026, 11, 5))
    assert lic.verify(token(exp='2026-11-05'), PUB, MACHINE, today=dt.date(2026, 11, 5))  # last day still valid
    head, body, sig = token().split('.')
    forged = '.'.join([head, lic.b64e(lic.b64d(body).replace('学员甲'.encode(), '学员乙'.encode())), sig])
    with pytest.raises(lic.LicenseError, match='无效'):
        lic.verify(forged, PUB, MACHINE)
    _, other_pub = lic.generate_keypair()
    with pytest.raises(lic.LicenseError):
        lic.verify(token(), other_pub, MACHINE)
    for junk in ('', 'abc', 'MAVO1.x', 'MAVO1.x.y.z'):
        with pytest.raises(lic.LicenseError):
            lic.verify(junk, PUB, MACHINE)


def test_store_activation_persists(tmp_path):
    store = lic.LicenseStore(tmp_path, PUB, MACHINE)
    assert store.status()['licensed'] is False
    assert store.activate(token(exp='2099-01-01'))['licensed'] is True
    assert lic.LicenseStore(tmp_path, PUB, MACHINE).status()['name'] == '学员甲'
    with pytest.raises(lic.LicenseError):
        store.activate('garbage-code-value')


def test_api_gate_blocks_until_activated(tmp_path, monkeypatch):
    app = create_app(Settings(tmp_path / 'd', license_required=True), MemorySecrets(), license_public_key=PUB)
    app.state.license.machine = MACHINE
    with TestClient(app) as c:
        assert c.get('/api/health').status_code == 200
        blocked = c.get('/api/assets')
        assert blocked.status_code == 402 and blocked.json()['code'] == 'license_required'
        assert c.get('/api/license').json()['licensed'] is False
        bad = c.post('/api/license', json={'code': 'MAVO1.bad.code.value'})
        assert bad.status_code == 422
        ok = c.post('/api/license', json={'code': token()})
        assert ok.status_code == 200 and ok.json()['licensed'] is True
        assert c.get('/api/assets').status_code == 200


def test_gate_off_by_default(tmp_path):
    app = create_app(Settings(tmp_path / 'd'), MemorySecrets(), license_public_key=PUB)
    with TestClient(app) as c:
        assert c.get('/api/assets').status_code == 200


def test_machine_code_is_stable_and_formatted():
    a, b = lic.machine_code(), lic.machine_code()
    assert a == b and len(a) == 23 and a.count('-') == 3


def test_env_endpoint_reports_tools_and_blocks_bad_mirror(tmp_path):
    app = create_app(Settings(tmp_path / 'd', license_required=True), MemorySecrets(), license_public_key=PUB)
    with TestClient(app) as c:
        env = c.get('/api/env')  # open even before activation: the wizard needs it
        assert env.status_code == 200
        body = env.json()
        assert body['app'] == 'MAVO BOX' and set(body) >= {'ffmpeg', 'ffprobe', 'chromium', 'install', 'platform'}
        assert c.post('/api/env/install-browser', json={'mirror': 'ftp://evil'}).status_code == 422
        assert c.post('/api/env/install-browser', json={'mirror': 'https://u:p@x.test'}).status_code == 422


def test_headless_shell_required_for_complete_browser_install(tmp_path):
    from core.env import headless_shell_present
    chrome = tmp_path / 'chromium-1194' / 'chrome-win' / 'chrome.exe'
    chrome.parent.mkdir(parents=True); chrome.write_bytes(b'x')
    assert headless_shell_present(chrome) is False          # chromium alone is a half install
    shell = tmp_path / 'chromium_headless_shell-1194' / 'chrome-win'
    shell.mkdir(parents=True); (shell / 'chrome-headless-shell.exe').write_bytes(b'x')
    assert headless_shell_present(chrome) is True


def test_log_scrubbing_removes_keys_codes_and_signed_urls():
    from core.logs import scrub
    text = ('Authorization: Bearer abcdef123456789 sk-ABCDEFGH12345678 api_key="topsecretvalue" '
            'https://user:pw@host.test/x https://cdn.test/v.mp4?X-Sig=abc123&exp=1 '
            'MAVO1.aaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbb')
    out = scrub(text)
    for secret in ('abcdef123456789', 'ABCDEFGH12345678', 'topsecretvalue', 'user:pw', 'X-Sig=abc123', 'aaaaaaaaaaaa'):
        assert secret not in out, (secret, out)


def test_diagnostics_endpoint_is_open_and_secret_free(tmp_path):
    app = create_app(Settings(tmp_path / 'd', license_required=True), MemorySecrets(), license_public_key=PUB)
    app.state.license.machine = MACHINE
    with TestClient(app) as c:
        before = c.get('/api/diagnostics')                 # reachable before activation: students need it for support
        assert before.status_code == 200 and '未激活' in before.json()['text']
        assert c.get('/api/assets').status_code == 402     # everything else is still gated
        assert c.post('/api/license', json={'code': token()}).status_code == 200
        c.post('/api/providers', json={'type': 'llm', 'name': 'x', 'base_url': 'https://api.test/v1', 'model': 'm'})
        body = c.get('/api/diagnostics').json()
        assert 'MAVO BOX' in body['text'] and 'api.test' in body['text'] and '已激活' in body['text']
        assert body['text'].count(chr(10)) > 5 and 'MAVO1.' not in body['text']
