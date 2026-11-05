"""Talk to an OpenAI-compatible gateway (GPT-Load, one-api, a relay, OpenAI itself) to test a provider
and list the models it offers. Used by the "获取模型列表" button; read-only (GET /models)."""
from urllib.parse import urlsplit

import httpx

LOOPBACK = {'127.0.0.1', 'localhost', '::1'}


class GatewayError(Exception):
    """Message is always safe to show to the user (no third-party text, no secrets)."""


def list_models(base_url: str, key: str, transport=None, timeout: float = 15.0) -> list[str]:
    parts = urlsplit(base_url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise GatewayError('接口地址无效')
    local = (parts.hostname or '').lower() in LOOPBACK
    url = base_url.rstrip('/') + '/models'
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=6.0), follow_redirects=False, transport=transport) as client:
            response = client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError):
        raise GatewayError(
            f'连不上 {parts.netloc}，请确认网关已启动、地址和端口正确' if local
            else f'连不上 {parts.netloc}，请检查网络、代理或接口地址') from None
    status = response.status_code
    if status in (401, 403):
        raise GatewayError('Key 无效或没有权限（网关里的 AccessKey 是否已创建并允许对应分组？）')
    if status == 404:
        raise GatewayError('该地址没有提供模型列表（/models），请直接手动填写模型名')
    if status == 429:
        raise GatewayError('请求过于频繁，请稍后再试')
    if status >= 500:
        raise GatewayError('网关或服务商暂时不可用')
    if not 200 <= status < 300:
        raise GatewayError('请求失败，请检查接口地址和 Key')
    try:
        data = response.json().get('data', [])
        ids = sorted({str(item['id']) for item in data if isinstance(item, dict) and item.get('id')})
    except (ValueError, AttributeError, KeyError, TypeError):
        raise GatewayError('返回的内容不是标准的模型列表，请手动填写模型名') from None
    return ids[:500]
