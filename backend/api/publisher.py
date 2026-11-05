from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from modules.publisher.service import utc, safe_result_url


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class GroupInput(Input):
    name: str = Field(min_length=1, max_length=200)


class ProxyInput(GroupInput):
    server: str = Field(max_length=2048)
    username: str = Field(default='', max_length=200)
    password: SecretStr | None = Field(default=None)

    @field_validator('server')
    @classmethod
    def valid_server(cls, value):
        url = urlsplit(value)
        if (url.scheme not in ('http', 'https', 'socks5') or not url.hostname or not url.port
                or url.username is not None or url.password is not None or url.query or url.fragment
                or url.path not in ('', '/')):
            raise ValueError('代理需为不含凭据的协议、主机和端口')
        return value.rstrip('/')

    @field_validator('password')
    @classmethod
    def valid_password(cls, value):
        if value is not None and len(value.get_secret_value()) > 8192:
            raise ValueError('密码过长')
        return value


AccountStatus = Literal['unknown', 'logged_in', 'login_required', 'banned_suspected', 'disabled']
JobStatus = Literal['scheduled', 'running', 'succeeded', 'failed', 'cancelled', 'needs_attention', 'missed']


class AccountInput(GroupInput):
    group_id: str | None = None
    proxy_id: str | None = None
    status: AccountStatus = 'unknown'
    browser_locale: str | None = Field(default=None, min_length=2, max_length=50, pattern=r'^[A-Za-z0-9-]+$')
    browser_timezone: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_/+\-]+$')
    browser_width: int | None = Field(default=None, ge=320, le=7680)
    browser_height: int | None = Field(default=None, ge=320, le=4320)


class JobInput(Input):
    account_id: str | None = None
    account_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)
    asset_id: str | None = None
    video_path: str | None = Field(default=None, max_length=4096)
    caption: str = Field(default='', max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=100)
    scheduled_at: datetime
    privacy: Literal['public', 'friends', 'private'] = 'private'
    ai_generated: bool = False
    allow_comment: bool = True
    allow_duet: bool = False
    allow_stitch: bool = False
    stagger_minutes: float = Field(default=0, ge=0, le=10080, allow_inf_nan=False)
    jitter_window_seconds: int = Field(default=0, ge=0, le=86400)
    max_retries: int = Field(default=1, ge=0, le=10)

    @field_validator('scheduled_at')
    @classmethod
    def aware(cls, value):
        return utc(value)

    @field_validator('hashtags')
    @classmethod
    def valid_tags(cls, values):
        tags = [v.lstrip('#').strip() for v in values]
        if any(not v or len(v) > 100 or any(c.isspace() for c in v) for v in tags):
            raise ValueError('话题需为不含空格的短文本')
        return tags

    @model_validator(mode='after')
    def source(self):
        if bool(self.account_id) == bool(self.account_ids):
            raise ValueError('请选择单账号或多账号之一')
        if bool(self.asset_id) == bool(self.video_path):
            raise ValueError('请选择素材或本地路径之一')
        if len(self.caption) + sum(len(t) + 2 for t in self.hashtags) > 2200:
            raise ValueError('文案和话题总长度超过限制')
        return self


class RescheduleInput(Input):
    scheduled_at: datetime

    @field_validator('scheduled_at')
    @classmethod
    def aware(cls, value):
        return utc(value)


class CompleteInput(Input):
    result_url: str | None = None

    @field_validator('result_url')
    @classmethod
    def valid_url(cls, value):
        if value and not safe_result_url(value):
            raise ValueError('需要不含凭据和查询参数的 TikTok 视频链接')
        return value


def router(app):
    routes = APIRouter(prefix='/api')

    def service():
        return app.state.publisher

    def require(table, identifier):
        row = app.state.db.one(f'SELECT * FROM {table} WHERE id=?', (identifier,))
        if not row:
            raise HTTPException(404, '记录不存在')
        return row

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.get('/account-groups')
    def groups():
        return app.state.db.all('SELECT * FROM account_groups ORDER BY name,id')

    @routes.post('/account-groups', status_code=201)
    def create_group(data: GroupInput):
        identifier = uuid4().hex
        app.state.db.execute('INSERT INTO account_groups VALUES (?,?)', (identifier, data.name))
        return require('account_groups', identifier)

    @routes.put('/account-groups/{identifier}')
    def update_group(identifier: str, data: GroupInput):
        require('account_groups', identifier)
        app.state.db.execute('UPDATE account_groups SET name=? WHERE id=?', (data.name, identifier))
        return require('account_groups', identifier)

    @routes.delete('/account-groups/{identifier}', status_code=204)
    def delete_group(identifier: str):
        require('account_groups', identifier)
        with app.state.scheduler.lock:
            with app.state.db.connect() as con:
                con.execute('UPDATE accounts SET group_id=NULL WHERE group_id=?', (identifier,))
                con.execute('DELETE FROM account_groups WHERE id=?', (identifier,))

    @routes.get('/proxies')
    def proxies():
        return [service().proxy(r['id']) for r in app.state.db.all('SELECT id FROM proxies ORDER BY name,id')]

    @routes.post('/proxies', status_code=201)
    def create_proxy(data: ProxyInput):
        return service().save_proxy(data.model_dump())

    @routes.get('/proxies/{identifier}')
    def get_proxy(identifier: str):
        require('proxies', identifier)
        return service().proxy(identifier)

    def proxy_idle(identifier):
        if app.state.scheduler.busy(proxy_id=identifier):
            raise HTTPException(409, '代理正在使用，请稍后再修改')

    @routes.put('/proxies/{identifier}')
    def update_proxy(identifier: str, data: ProxyInput):
        require('proxies', identifier)
        with app.state.scheduler.lock:
            proxy_idle(identifier)
            return service().save_proxy(data.model_dump(), identifier)

    @routes.delete('/proxies/{identifier}', status_code=204)
    def delete_proxy(identifier: str):
        require('proxies', identifier)
        with app.state.scheduler.lock:
            proxy_idle(identifier)
            if app.state.db.one('SELECT id FROM accounts WHERE proxy_id=?', (identifier,)):
                raise HTTPException(409, '请先解除账号的代理绑定')
            service().secrets.delete('proxy:' + identifier)
            app.state.db.execute('DELETE FROM proxies WHERE id=?', (identifier,))

    @routes.get('/accounts')
    def accounts(group_id: str | None = None):
        if group_id:
            return app.state.db.all('SELECT * FROM accounts WHERE group_id=? ORDER BY created_at,id', (group_id,))
        return app.state.db.all('SELECT * FROM accounts ORDER BY created_at,id')

    @routes.get('/accounts/{identifier}')
    def account(identifier: str):
        return require('accounts', identifier)

    @routes.post('/accounts', status_code=201)
    def create_account(data: AccountInput):
        with app.state.scheduler.lock:
            return call(service().save_account, data.model_dump())

    @routes.put('/accounts/{identifier}')
    def update_account(identifier: str, data: AccountInput):
        require('accounts', identifier)
        with app.state.scheduler.lock:
            if app.state.scheduler.busy(account_id=identifier):
                raise HTTPException(409, '账号正在使用，请稍后修改')
            return call(service().save_account, data.model_dump(), identifier)

    @routes.delete('/accounts/{identifier}', status_code=204)
    def delete_account(identifier: str):
        require('accounts', identifier)
        with app.state.scheduler.lock:
            if app.state.scheduler.busy(account_id=identifier):
                raise HTTPException(409, '账号正在使用')
            if app.state.db.one('SELECT id FROM publish_jobs WHERE account_id=?', (identifier,)):
                raise HTTPException(409, '账号有发布历史，请改为停用以保留记录')
            # Profiles are deliberately not recursively deleted (contain the user's session).
            app.state.db.execute('DELETE FROM accounts WHERE id=?', (identifier,))

    @routes.post('/accounts/{identifier}/login', status_code=202)
    def login(identifier: str):
        row = require('accounts', identifier)
        with app.state.scheduler.lock:
            if row['status'] == 'disabled' or not app.state.scheduler.available(row):
                raise HTTPException(409, '账号、代理正在使用或已达到并发上限')
            return app.state.queue.create('account_login', {'account_id': identifier})

    @routes.post('/accounts/{identifier}/check')
    def check(identifier: str):
        require('accounts', identifier)
        return call(app.state.scheduler.account_operation, identifier, 'check_login')

    @routes.post('/publish-jobs', status_code=201)
    def create_jobs(data: JobInput):
        with app.state.scheduler.lock:
            jobs = call(service().create_jobs, data.model_dump())
        return jobs if data.account_ids else jobs[0]

    @routes.post('/publish-jobs/batch', status_code=201)
    def batch(data: JobInput):
        if not data.account_ids:
            raise HTTPException(422, '批量创建需要账号列表')
        return create_jobs(data)

    @routes.get('/publish-jobs')
    def jobs(status: JobStatus | None = None, account_id: str | None = None,
             date_from: datetime | None = None, date_to: datetime | None = None):
        clauses, params = [], []
        for key, value in (('status', status), ('account_id', account_id)):
            if value:
                clauses.append(key + '=?'); params.append(value)
        try:
            for operator, value in (('>=', date_from), ('<=', date_to)):
                if value:
                    clauses.append('scheduled_at' + operator + '?'); params.append(utc(value).isoformat())
        except ValueError:
            raise HTTPException(422, '筛选时间必须包含时区') from None
        rows = app.state.db.all('SELECT id FROM publish_jobs' + (' WHERE ' + ' AND '.join(clauses) if clauses else '')
                                + ' ORDER BY scheduled_at DESC,id', tuple(params))
        result = []
        for row in rows:
            item = service().job(row['id'])
            item.pop('logs')
            result.append(item)
        return result

    @routes.get('/publish-jobs/{identifier}')
    def job(identifier: str):
        require('publish_jobs', identifier)
        return service().job(identifier)

    def action(identifier, name, **kw):
        require('publish_jobs', identifier)
        with app.state.scheduler.lock:
            return call(service().action, identifier, name, **kw)

    @routes.post('/publish-jobs/{identifier}/cancel')
    def cancel(identifier: str):
        return action(identifier, 'cancel')

    @routes.post('/publish-jobs/{identifier}/retry')
    def retry(identifier: str):
        return action(identifier, 'retry')

    @routes.post('/publish-jobs/{identifier}/reschedule')
    def reschedule(identifier: str, data: RescheduleInput):
        return action(identifier, 'reschedule', scheduled_at=data.scheduled_at)

    @routes.post('/publish-jobs/{identifier}/complete')
    def complete(identifier: str, data: CompleteInput):
        return action(identifier, 'complete', result_url=data.result_url)

    return routes
