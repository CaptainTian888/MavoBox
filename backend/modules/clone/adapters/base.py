import base64
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from core.queue import Cancelled


class AdapterError(ValueError): pass
class Auth(AdapterError): pass
class RateLimit(AdapterError): pass
class Transient(AdapterError): pass
class Fatal(AdapterError): pass
class ContentPolicy(AdapterError): pass


def json_path(value, path):
    try:
        for part in path.split('.') if path else []:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise Fatal('接口结果字段不符合配置') from None


def data_url(path):
    path = Path(path)
    return 'data:' + (mimetypes.guess_type(path.name)[0] or 'image/png') + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


class Client:
    def __init__(self, provider, key, transport=None, client=None, cancelled=lambda: False, retries=2):
        self.provider, self._key = provider, key
        self.extra = provider.get('extra', {})
        self.transport, self.client, self.cancelled, self.retries = transport, client, cancelled, retries
        self.base = provider['base_url'].rstrip('/')

    def check(self):
        if self.cancelled(): raise Cancelled()

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def request(self, method, url, authenticated=True, retry=True, **kwargs):
        self.check()
        url = url if url.startswith(('https://','http://')) else self.base + '/' + url.lstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
            raise Fatal('接口地址无效')
        if self._key and self._key in url: raise Fatal('接口地址不得包含密钥')
        if authenticated and (parsed.scheme, parsed.netloc) != (urlsplit(self.base).scheme, urlsplit(self.base).netloc):
            raise Fatal('认证请求不能跨 Provider 主机')
        headers = {}
        if authenticated:
            headers['Authorization'] = ('Key ' if self.provider.get('api_style') == 'fal_queue' else 'Bearer ') + self._key
        # POSTs can charge even when their response times out. Never automatically resubmit.
        attempts = self.retries + 1 if retry and method == 'GET' else 1
        for attempt in range(attempts):
            self.check()
            owned = self.client is None
            client = self.client or httpx.Client(timeout=httpx.Timeout(90, connect=10), transport=self.transport, follow_redirects=False)
            try:
                response = client.request(method, url, headers=headers, **kwargs)
                status = response.status_code
                if status in (401,403): raise Auth('Provider 认证失败')
                if status == 429: raise RateLimit('Provider 限流，请稍后重试')
                if status >= 500: raise Transient('Provider 暂时不可用')
                if status in (400,422):
                    try:
                        error=response.json().get('error',{})
                        code=error.get('code','') if isinstance(error,dict) else ''
                    except Exception: code=''
                    if code in ('content_policy_violation','content_filter','safety_violation'):
                        raise ContentPolicy('Provider 内容策略拒绝请求')
                if status == 422: raise ContentPolicy('Provider 拒绝内容或参数，请检查策略及模型约束')
                if not 200 <= status < 300: raise Fatal('Provider 请求失败，请检查配置')
                return response
            except (httpx.TimeoutException, httpx.TransportError):
                error = Transient('Provider 连接失败或超时；提交结果可能未知，请核查账单')
            except (RateLimit, Transient) as exc:
                error = exc
            finally:
                if owned: client.close()
            if attempt + 1 >= attempts: raise error from None
            self.wait(min(2 ** attempt, 8))

    def json(self, response):
        try:
            value = response.json()
            import json
            if self._key and self._key in json.dumps(value): raise ValueError()
            return value
        except Exception:
            raise Fatal('Provider 返回无效或含敏感信息的数据') from None

    def download(self, url, dest, verify=True):
        from core.ffmpeg import probe
        dest = Path(dest)
        try:
            response = self.request('GET', url, authenticated=False)
            self.check()
            dest.write_bytes(response.content)
            if verify: probe(dest)
            self.check()
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return dest

    def image(self, path):
        if str(path).startswith(('https://','http://','data:')): return str(path)
        if self.extra.get('image_upload') == 'upload':
            with Path(path).open('rb') as source:
                value = self.json(self.request('POST', self.extra['upload_path'], files={'file':(Path(path).name, source)}))
            return json_path(value, self.extra.get('upload_result_path', 'url'))
        return data_url(path)
