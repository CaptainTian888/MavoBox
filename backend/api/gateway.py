"""内置本地网关（GPT-Load）的管理接口：状态、启用/停用、管理 Key、打开控制台、一键接入。"""
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from modules.gateway_manager import PROVIDER_NAME, GatewayError


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EnableInput(Input):
    enabled: bool


class ConnectInput(Input):
    access_key: SecretStr
    model: str = Field(default='gpt-4o-mini', min_length=1, max_length=200)


def gateway_router(app):
    routes = APIRouter(prefix='/api/gateway')

    def manager():
        return app.state.gateway

    def guarded(call):
        try:
            return call()
        except GatewayError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.get('')
    def status():
        return manager().status()

    @routes.post('/enable')
    def enable(data: EnableInput):
        guarded(lambda: manager().set_enabled(data.enabled))
        return manager().status()

    @routes.post('/restart')
    def restart():
        def go():
            manager().stop()
            manager().start()
        guarded(go)
        return manager().status()

    @routes.post('/admin-key')
    def admin_key():
        """The key for signing in to the gateway console. Only reachable from the local UI (Origin-checked)."""
        if not manager().available():
            raise HTTPException(409, '这个安装包里没有内置网关程序')
        return {'key': manager().admin_key()}

    @routes.post('/open-console')
    def open_console():
        guarded(manager().open_console)
        return {'opened': True}

    @routes.post('/connect')
    def connect(data: ConnectInput):
        """Create / update the LLM provider that points at the built-in gateway with the given AccessKey."""
        access_key = data.access_key.get_secret_value().strip()
        if len(access_key) < 8:
            raise HTTPException(422, 'AccessKey 太短，请粘贴网关控制台里创建的 AccessKey')
        mgr = manager()
        existing = next((p for p in app.state.providers.list() if p['name'] == PROVIDER_NAME), None)
        spec = SimpleNamespace(type='llm', name=PROVIDER_NAME, base_url=mgr.status()['openai_base_url'],
                               model=data.model, api_style='openai_compat', extra={})
        provider = app.state.providers.save(spec, existing['id'] if existing else None)
        app.state.secrets.set(provider['id'], access_key)
        return {'provider': provider, 'created': existing is None}

    return routes
