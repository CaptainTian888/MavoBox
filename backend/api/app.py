import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from api.static import mount_frontend
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
from api.remix import router as remix_router
from modules.remix.service import RemixService
from api.clone import router as clone_router
from modules.clone.service import CloneService, OPERATIONS


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
    type: Literal['llm', 'video_gen', 'asr', 'image_gen', 'tts']
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_style: Literal['openai_compat','fal_queue','replicate','custom_http'] = 'openai_compat'
    extra: dict = Field(default_factory=dict)

    @field_validator('extra')
    @classmethod
    def extra_valid(cls, value):
        import json
        import math
        import re
        if len(json.dumps(value)) > 32000:
            raise ValueError('extra 配置过大')
        def check(obj):
            if isinstance(obj,dict):
                for k,v in obj.items():
                    if re.search(r'api.?key|secret|token|authorization|password|headers|credential',k,re.I):
                        raise ValueError('extra 不能存储凭据或认证头，请使用密钥管理器')
                    check(v)
            elif isinstance(obj,list):
                for v in obj: check(v)
            elif isinstance(obj,str) and obj.startswith(('http://','https://')):
                parsed=urlsplit(obj)
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('配置 URL 不得含凭据或查询参数')
        check(value)
        for amount in value.get('pricing',{}).values():
            if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0:
                raise ValueError('单价须为有限非负数')
        durations=value.get('supported_durations',[5,10])
        if not isinstance(durations,list) or not durations or any(isinstance(d,bool) or not isinstance(d,(int,float)) or not math.isfinite(d) or d<=0 or d>300 for d in durations):
            raise ValueError('模型支持时长无效')
        for key in ('min_duration','max_duration'):
            if key in value and (isinstance(value[key],bool) or not isinstance(value[key],(int,float)) or not math.isfinite(value[key]) or not 0<value[key]<=300):
                raise ValueError('模型时长范围无效')
        if ('min_duration' in value)!=('max_duration' in value) or value.get('min_duration',0)>value.get('max_duration',300):
            raise ValueError('需要完整且有序的模型时长范围')
        return value

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


def create_app(settings=None, secrets=None, publisher_driver=None, publisher_now=None, remix_client_factory=None, clone_factories=None):
    settings = settings or Settings()
    secrets = secrets or KeyringSecrets()

    @asynccontextmanager
    async def lifespan(app):
        db = Database(settings.data_dir / 'tiktol.sqlite3')
        app.state.db = db
        app.state.providers = Providers(db, secrets)
        app.state.clone = CloneService(db, secrets, settings, clone_factories)
        app.state.remix = RemixService(db, secrets, settings,
            **({'client_factory': remix_client_factory} if remix_client_factory else {}))
        app.state.publisher = PublisherService(db, secrets, settings, now=publisher_now)
        app.state.scheduler = Scheduler(app.state.publisher,
            publisher_driver or PlaywrightDriver(app.state.publisher), now=publisher_now)

        def login_handler(payload, progress, cancelled):
            if not cancelled():
                app.state.scheduler.account_operation(payload['account_id'], 'open_login')

        def task_finished(task):
            app.state.remix.task_finished(task)
            app.state.clone.task_finished(task)

        app.state.queue = TaskQueue(db, {'download': Downloader(db, settings.data_dir),
                                      **{'clone_' + op: app.state.clone.handler(op) for op in OPERATIONS},
                                      **{'remix_' + op: app.state.remix.handler(op) for op in ('analyze', 'plan', 'render')},
                                      'account_login': login_handler},
                                    settings.concurrency, settings.retries, on_finished=task_finished)
        app.state.scheduler.start()
        try:
            yield
        finally:
            app.state.queue.close()
            app.state.scheduler.close()

    app = FastAPI(title='TikTol M3', lifespan=lifespan)
    app.include_router(publisher_router(app))
    app.include_router(remix_router(app))
    app.include_router(clone_router(app))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        port = os.environ.get('TIKTOL_PORT', '8000')
        if origin and origin not in ('http://127.0.0.1:5173', 'http://localhost:5173',
                                      f'http://127.0.0.1:{port}', f'http://localhost:{port}',
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
        row = require_task(task_id)
        try:
            if action == 'retry' and row['kind'].startswith('remix_'):
                return app.state.remix.retry_task(app.state.queue, task_id)
            if action == 'retry' and row['kind'].startswith('clone_'):
                return app.state.clone.retry_task(app.state.queue, task_id)
            return getattr(app.state.queue, action)(task_id)
        except LookupError:
            raise HTTPException(404, '混剪项目不存在') from None
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
            if path.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp') and metadata['width'] and metadata['duration']==0:
                metadata['type']='image'
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

    if os.environ.get('TIKTOL_SERVE_UI', '1') != '0':
        mount_frontend(app)
    return app
