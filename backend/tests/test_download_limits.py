import httpx
import pytest
from modules.clone.adapters.base import Client, Fatal


def client(handler, **extra):
    return Client(dict(base_url='https://provider.test', model='m', api_style='custom_http', extra=extra),
                  'test-key', transport=httpx.MockTransport(handler))


def test_oversize_download_is_stopped_and_partial_file_removed(tmp_path):
    c = client(lambda r: httpx.Response(200, content=b'x' * (2 * 1024 * 1024)), max_download_bytes=1024 * 1024)
    dest = tmp_path / 'out.bin'
    with pytest.raises(Fatal, match='上限'):
        c.download('https://cdn.test/big.mp4', dest, verify=False)
    assert not dest.exists()


@pytest.mark.parametrize('url', ['http://127.0.0.1/x.mp4', 'http://localhost/x.mp4', 'http://10.0.0.5/x.mp4',
                                 'http://192.168.1.2/x.mp4', 'http://169.254.169.254/latest', 'http://[::1]/x.mp4',
                                 'https://user:pw@cdn.test/x.mp4', 'ftp://cdn.test/x.mp4'])
def test_internal_or_unsafe_result_urls_are_refused(tmp_path, url):
    called = []
    c = client(lambda r: called.append(r) or httpx.Response(200, content=b'x'))
    with pytest.raises(Fatal):
        c.download(url, tmp_path / 'o.bin', verify=False)
    assert not called


def test_public_url_and_same_host_local_provider_allowed(tmp_path):
    ok = client(lambda r: httpx.Response(200, content=b'hello'))
    assert ok.download('https://cdn.test/a.bin', tmp_path / 'a.bin', verify=False).read_bytes() == b'hello'
    local = Client(dict(base_url='http://127.0.0.1:9000', model='m', api_style='custom_http', extra={}), 'k',
                   transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'local')))
    assert local.download('http://127.0.0.1:9000/r.bin', tmp_path / 'b.bin', verify=False).read_bytes() == b'local'
