from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.static import mount_frontend


def make(tmp_path):
    dist = tmp_path / 'web'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<html>app</html>')
    (dist / 'assets' / 'a.js').write_text('console.log(1)')
    (tmp_path / 'secret.txt').write_text('nope')
    app = FastAPI()

    @app.get('/api/ping')
    def ping():
        return {'ok': True}

    assert mount_frontend(app, dist)
    return TestClient(app)


def test_serves_index_assets_and_spa_fallback(tmp_path):
    c = make(tmp_path)
    assert c.get('/').text == '<html>app</html>'
    assert c.get('/assets/a.js').text == 'console.log(1)'
    assert c.get('/accounts/page').text == '<html>app</html>'


def test_api_not_shadowed_and_unknown_api_404(tmp_path):
    c = make(tmp_path)
    assert c.get('/api/ping').json() == {'ok': True}
    assert c.get('/api/missing').status_code == 404


def test_path_traversal_does_not_leak(tmp_path):
    c = make(tmp_path)
    for p in ('/../secret.txt', '/%2e%2e/secret.txt', '/assets/../../secret.txt'):
        assert 'nope' not in c.get(p).text


def test_no_dist_means_no_mount(tmp_path):
    assert mount_frontend(FastAPI(), tmp_path / 'missing') is False
