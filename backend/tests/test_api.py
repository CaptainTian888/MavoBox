import json
from uuid import uuid4


def test_health_and_origin(client):
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert client.get('/api/health', headers={'Origin': 'https://example.org'}).status_code == 403


def test_provider_secret_never_echoed(client, caplog):
    data = dict(type='llm', name='Local', base_url='https://example.org/v1', model='demo')
    response = client.put('/api/providers', json=data)
    assert response.status_code == 200
    provider_id = response.json()['id']
    value = uuid4().hex + uuid4().hex
    response = client.put(f'/api/secrets/{provider_id}', json={'key': value})
    assert response.json() == {'key_status': '已设置'}
    listing = client.get('/api/providers')
    assert listing.json()[0]['key_status'] == '已设置'
    invalid = client.put(f'/api/secrets/{provider_id}', json={'key': value, 'extra': value})
    assert invalid.status_code == 422
    for text in (response.text, listing.text, invalid.text, caplog.text):
        assert value not in text
    # WAL may hold recent rows; checkpoint then inspect the durable database too.
    db = client.app.state.db
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert value.encode() not in db.path.read_bytes()
    data['model'] = 'updated'
    assert client.put(f'/api/providers/{provider_id}', json=data).json()['model'] == 'updated'
    assert client.delete('/api/providers', params={'id': provider_id}).status_code == 204
    assert client.get('/api/providers').json() == []


def test_bad_inputs(client):
    for url in ('https://example.org/video', 'file:///tmp/a', 'https://tiktok.com.evil.org/x'):
        assert client.post('/api/downloads', json={'url': url}).status_code == 422
    assert client.post('/api/downloads', json={'url': 'https://www.tiktok.com/x', 'seconds': -1}).status_code == 422
    assert client.get('/api/tasks/missing').status_code == 404
    assert client.post('/api/assets', json={'path': 'missing.mp4'}).status_code == 400


def test_register_real_video(client, video):
    response = client.post('/api/assets', json={'path': str(video)})
    assert response.status_code == 201
    asset = response.json()
    assert asset['width'] == 160 and asset['height'] == 120
    assert 1.8 <= asset['duration'] <= 2.2
    assert client.get('/api/assets').json()[0]['id'] == asset['id']


def test_origin_allowlist_follows_port_and_dev_flag(client, monkeypatch):
    monkeypatch.setenv('TIKTOL_PORT', '8123')
    monkeypatch.delenv('TIKTOL_DEV', raising=False)
    ok = lambda o: client.get('/api/health', headers={'Origin': o}).status_code
    assert ok('http://127.0.0.1:8123') == 200
    assert ok('http://127.0.0.1:8000') == 403      # another local app on a default port
    assert ok('http://127.0.0.1:5173') == 403      # dev server not trusted unless TIKTOL_DEV=1
    monkeypatch.setenv('TIKTOL_DEV', '1')
    assert ok('http://127.0.0.1:5173') == 200
