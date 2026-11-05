import httpx
import pytest
from modules import gateway


def transport(handler):
    return httpx.MockTransport(handler)


def test_lists_models_sorted_and_sends_bearer_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'object': 'list', 'data': [
            {'id': 'gpt-4o', 'object': 'model'}, {'id': 'claude-x'}, {'id': 'gpt-4o'}, {'object': 'model'}]})

    ids = gateway.list_models('http://127.0.0.1:3001/v1/', 'gw-key-123', transport=transport(handler))
    assert ids == ['claude-x', 'gpt-4o']
    assert str(seen[0].url) == 'http://127.0.0.1:3001/v1/models'
    assert seen[0].headers['Authorization'] == 'Bearer gw-key-123'


@pytest.mark.parametrize('status,needle', [(401, 'AccessKey'), (403, 'AccessKey'), (404, '手动填写'),
                                           (429, '频繁'), (502, '暂时不可用'), (418, '请求失败')])
def test_status_codes_become_friendly_messages(status, needle):
    with pytest.raises(gateway.GatewayError, match=needle):
        gateway.list_models('https://gw.test/v1', 'k', transport=transport(lambda r: httpx.Response(status, text='secret upstream text')))


def test_unreachable_local_gateway_names_the_address():
    def boom(request):
        raise httpx.ConnectError('refused')
    with pytest.raises(gateway.GatewayError, match='127.0.0.1:3001.*网关已启动'):
        gateway.list_models('http://127.0.0.1:3001/v1', 'k', transport=transport(boom))


def test_bad_shapes_and_urls_are_rejected_without_leaking():
    with pytest.raises(gateway.GatewayError, match='标准的模型列表'):
        gateway.list_models('https://gw.test/v1', 'k', transport=transport(lambda r: httpx.Response(200, text='<html>')))
    for url in ('ftp://x.test', 'https://u:p@x.test/v1', 'nonsense'):
        with pytest.raises(gateway.GatewayError, match='接口地址无效'):
            gateway.list_models(url, 'k')


def test_api_endpoint_uses_stored_key_and_never_echoes_it(client, monkeypatch):
    provider = client.post('/api/providers', json={'type': 'llm', 'name': 'gw', 'base_url': 'http://127.0.0.1:3001/v1',
                                                   'model': 'm'}).json()
    client.put(f"/api/secrets/{provider['id']}", json={'key': 'super-secret-gateway-key'})
    captured = {}

    def fake(base, key, transport=None, timeout=15.0):
        captured['key'] = key
        return ['a-model', 'b-model']
    monkeypatch.setattr(gateway, 'list_models', fake)
    r = client.post(f"/api/providers/{provider['id']}/models")
    assert r.status_code == 200 and r.json() == {'models': ['a-model', 'b-model'], 'count': 2}
    assert captured['key'] == 'super-secret-gateway-key' and 'super-secret' not in r.text

    def failing(*a, **k):
        raise gateway.GatewayError('连不上 127.0.0.1:3001')
    monkeypatch.setattr(gateway, 'list_models', failing)
    bad = client.post(f"/api/providers/{provider['id']}/models")
    assert bad.status_code == 422 and '连不上' in bad.json()['detail']
    assert client.post('/api/providers/' + 'f' * 32 + '/models').status_code == 404
    fal = client.post('/api/providers', json={'type': 'video_gen', 'name': 'v', 'base_url': 'https://queue.fal.run',
                                               'model': 'm', 'api_style': 'fal_queue'}).json()
    assert client.post(f"/api/providers/{fal['id']}/models").status_code == 422
