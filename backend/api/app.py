from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from core.config import Settings
from core.db import Database
from core.ffmpeg import probe
from core.providers import Providers
from core.queue import TaskQueue
from core.secrets import KeyringSecrets
from modules.downloader import Downloader
from modules.publisher.service import PublisherService
from modules.publisher.scheduler import Scheduler
from modules.publisher.playwright_driver import PlaywrightDriver
from api.publisher import router as publisher_router


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DownloadInput(Input):
    url: str = Field(max_length=2048)
    seconds: float = Field(default=5, gt=0, le=3600, allow_inf_nan=False)

    @field_validator('url')
    @classmethod
    def tiktok_url(cls, value):
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
                or not (host == 'tiktok.com' or host.endswith('.tiktok.com'))):
            raise ValueError('需要 HTTPS TikTok 链接')
        return value


class AssetInput(Input):
    path: str = Field(min_length=1, max_length=4096)


class ProviderInput(Input):
    type: Literal['llm', 'video_gen']
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(max_length=2048)
    model: str = Field(min_length=1, max_length=200)

    @field_validator('base_url')
    @classmethod
    def base_url_valid(cls, value):
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Base URL 不可包含凭据、查询参数或片段')
        return value.rstrip('/')


class ProviderPut(ProviderInput):
    id: str | None = None


class SecretInput(Input):
    key: SecretStr


def create_app(settings=None, secrets=None, publisher_driver=None, publisher_now=None):
    settings = settings or Settings()
    secrets = secrets or KeyringSecrets()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.data_dir / 'tiktol.sqlite3')
        app.state.db = db
        app.state.providers = Providers(db, secrets)
        app.state.publisher = PublisherService(db, secrets, settings, now=publisher_now)
        app.state.scheduler = Scheduler(app.state.publisher,
            publisher_driver or PlaywrightDriver(app.state.publisher), now=publisher_now)

        def login_handler(payload, progress, cancelled):
            if not cancelled():
                app.state.scheduler.account_operation(payload['account_id'], 'open_login')

        app.state.queue = TaskQueue(db, {'download': Downloader(db, settings.data_dir),
                                      'account_login': login_handler},
                                    settings.concurrency, settings.retries)
        app.state.scheduler.start()
        try:
            yield
        finally:
            app.state.queue.close()
            app.state.scheduler.close()

    app = FastAPI(title='TikTol M1', lifespan=lifespan)
    app.include_router(publisher_router(app))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in ('http://127.0.0.1:5173', 'http://localhost:5173',
                                      'http://127.0.0.1:8000', 'http://localhost:8000'):
            return JSONResponse(status_code=403, content={'detail': '不允许的来源'})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI defaults include invalid input values; never echo secret request bodies.
        return JSONResponse(status_code=422, content={'detail': '输入无效，请检查字段及格式'})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(status_code=500, content={'detail': '操作失败，请检查本地配置或凭据存储'})

    def require_task(task_id):
        row = app.state.queue.get(task_id)
        if not row:
            raise HTTPException(404, '任务不存在')
        return row

    def require_provider(provider_id):
        if not app.state.db.one('SELECT id FROM providers WHERE id=?', (provider_id,)):
            raise HTTPException(404, 'Provider 不存在')

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.post('/api/downloads', status_code=202)
    def download(data: DownloadInput):
        return app.state.queue.create('download', data.model_dump())

    @app.get('/api/tasks')
    def tasks():
        return app.state.queue.list()

    @app.get('/api/tasks/{task_id}')
    def task(task_id: str):
        return require_task(task_id)

    @app.post('/api/tasks/{task_id}/{action}')
    def task_action(task_id: str, action: Literal['cancel', 'retry']):
        require_task(task_id)
        try:
            return getattr(app.state.queue, action)(task_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/api/assets')
    def assets():
        return app.state.db.all('SELECT * FROM assets ORDER BY created_at DESC')

    @app.post('/api/assets', status_code=201)
    def add_asset(data: AssetInput):
        path = Path(data.path).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise HTTPException(400, '需要存在的本地文件绝对路径')
        try:
            metadata = probe(path)
        except (ValueError, RuntimeError, OSError):
            raise HTTPException(400, '无法探测媒体，请检查文件和 ffprobe') from None
        return app.state.db.add_asset(path.resolve(), metadata)

    @app.get('/api/providers')
    def providers():
        return app.state.providers.list()

    @app.post('/api/providers', status_code=201)
    def create_provider(data: ProviderInput):
        return app.state.providers.save(data)

    @app.put('/api/providers')
    def put_provider(data: ProviderPut):
        if data.id:
            require_provider(data.id)
        return app.state.providers.save(data, data.id)

    @app.put('/api/providers/{provider_id}')
    def update_provider(provider_id: str, data: ProviderInput):
        require_provider(provider_id)
        return app.state.providers.save(data, provider_id)

    @app.delete('/api/providers/{provider_id}', status_code=204)
    def delete_provider(provider_id: str):
        require_provider(provider_id)
        app.state.providers.delete(provider_id)

    @app.delete('/api/providers', status_code=204)
    def delete_provider_query(id: str):
        delete_provider(id)

    @app.put('/api/secrets/{provider_id}')
    def set_secret(provider_id: str, data: SecretInput):
        require_provider(provider_id)
        value = data.key.get_secret_value()
        if not value.strip() or len(value) > 8192:
            raise HTTPException(422, 'Key 长度无效')
        secrets.set(provider_id, value)
        return {'key_status': '已设置'}

    return app
