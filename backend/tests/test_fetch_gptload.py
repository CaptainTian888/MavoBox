import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'fetch_gptload.py'
spec = importlib.util.spec_from_file_location('fetch_gptload', SCRIPT)
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)

BINARY = b'fake-gpt-load-binary' * 10
SHA = hashlib.sha256(BINARY).hexdigest()


def release(tag='v2.0.0-rc.99', with_sums=True):
    names = ['gpt-load-windows-amd64.exe', 'LICENSE', 'MIT.txt', 'THIRD_PARTY_NOTICES.md'] + (['SHA256SUMS'] if with_sums else [])
    return dict(tag_name=tag, html_url='https://example.test/rel', published_at='2026-01-01T00:00:00Z',
                assets=[dict(name=n, size=len(BINARY) if n.endswith('.exe') else 5, browser_download_url=f'https://example.test/{n}') for n in names])


@pytest.fixture
def run(tmp_path, monkeypatch):
    out = tmp_path / 'gpt-load'
    state = dict(downloads=[], sha=SHA, rel=release())

    def fake_get_json(url):
        if state['rel'] is None:
            raise RuntimeError('offline')
        return state['rel']

    def fake_download(url, target, expected_size=None, retries=8):
        state['downloads'].append(url.rsplit('/', 1)[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        name = url.rsplit('/', 1)[-1]
        if name == 'SHA256SUMS':
            target.write_text(f'{state["sha"]}  gpt-load-windows-amd64.exe\nabc  other\n')
        elif name.endswith('.exe'):
            target.write_bytes(BINARY)
        else:
            target.write_text('licence text')

    monkeypatch.setattr(fetch, 'get_json', fake_get_json)
    monkeypatch.setattr(fetch, 'download', fake_download)

    def go(*extra):
        monkeypatch.setattr('sys.argv', ['fetch', '--out', str(out), *extra])
        return fetch.main()
    go.out, go.state = out, state
    return go


def test_downloads_verifies_and_records_version(run):
    assert run() == 0
    assert (run.out / 'gpt-load.exe').read_bytes() == BINARY
    version = json.loads((run.out / 'VERSION').read_text())
    assert version['tag'] == 'v2.0.0-rc.99' and version['sha256'] == SHA
    assert (run.out / 'licenses' / 'MIT.txt').is_file() and (run.out / 'licenses' / 'THIRD_PARTY_NOTICES.md').is_file()
    assert not (run.out.parent / 'gpt-load.partial').exists()


def test_already_latest_is_not_downloaded_again(run):
    assert run() == 0
    run.state['downloads'].clear()
    assert run() == 0
    assert 'gpt-load-windows-amd64.exe' not in run.state['downloads']


def test_a_new_release_replaces_the_old_binary(run):
    run()
    run.state['rel'] = release('v2.0.0-rc.100')
    assert run() == 0
    assert json.loads((run.out / 'VERSION').read_text())['tag'] == 'v2.0.0-rc.100'


def test_checksum_mismatch_never_replaces_the_existing_binary(run):
    run()
    run.state['rel'] = release('v2.0.0-rc.100')
    run.state['sha'] = '0' * 64
    assert run() == 1
    assert json.loads((run.out / 'VERSION').read_text())['tag'] == 'v2.0.0-rc.99'
    assert (run.out / 'gpt-load.exe').read_bytes() == BINARY


def test_incompatible_major_version_stops_the_build(run):
    run.state['rel'] = release('v3.0.0')
    assert run() == 1 and not (run.out / 'gpt-load.exe').exists()
    run.state['rel'] = release('v1.4.14')
    assert run() == 1


def test_offline_fails_unless_stale_is_explicitly_allowed(run, capsys):
    run()
    run.state['rel'] = None                     # cannot reach GitHub
    assert run() == 1                           # default: refuse to ship something unverified-as-latest
    assert run('--allow-stale') == 0            # explicit opt-in reuses the local copy, loudly
    assert '可能不是最新版' in capsys.readouterr().err
    assert run.out.joinpath('gpt-load.exe').is_file()


def test_offline_with_nothing_local_always_fails(run):
    run.state['rel'] = None
    assert run('--allow-stale') == 1


def test_missing_asset_or_checksums_file_is_an_error(run):
    run.state['rel'] = release(with_sums=False)
    assert run() == 1


def test_from_file_is_still_verified(run, tmp_path):
    good = tmp_path / 'dl.exe'
    good.write_bytes(BINARY)
    assert run('--from-file', str(good)) == 0 and 'gpt-load-windows-amd64.exe' not in run.state['downloads']
    run.state['rel'] = release('v2.0.0-rc.100')
    bad = tmp_path / 'bad.exe'
    bad.write_bytes(b'tampered')
    assert run('--from-file', str(bad)) == 1
    assert json.loads((run.out / 'VERSION').read_text())['tag'] == 'v2.0.0-rc.99'
