from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.ffmpeg import probe
from uuid import uuid4


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class ProjectInput(Input):
    name: str=Field(min_length=1,max_length=200)
    source_url: str | None=Field(default=None,max_length=2048)
    source_asset_id: str | None=None
    hook_seconds: float=Field(default=5,gt=0,le=3600,allow_inf_nan=False)
    mode: Literal['hook','full']='hook'
    brief: dict=Field(default_factory=dict)

    @field_validator('source_url')
    @classmethod
    def url(cls,value):
        if not value: return None
        p=urlsplit(value)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not (
                p.hostname=='tiktok.com' or (p.hostname or '').endswith('.tiktok.com')):
            raise ValueError('请使用 HTTPS TikTok 链接，其他平台请先导入本地文件')
        return value


class GenerateInput(Input):
    dry_run: bool=True
    confirm_budget: bool=False
    confirm_amount: float | None=Field(default=None,ge=0,allow_inf_nan=False)
    force: bool=False
    auto_assemble_render: bool=False


class RegenerateInput(GenerateInput):
    fallback: bool=False
    replacement_asset_id: str | None=None


class VariantsInput(Input):
    count: int=Field(ge=1,le=20)
    dimensions: dict[str,list[str]]


def router(app):
    routes=APIRouter(prefix='/api/clone')
    def s(): return app.state.clone
    def call(fn,*args,**kwargs):
        try: return fn(*args,**kwargs)
        except LookupError: raise HTTPException(404,'克隆项目不存在') from None
        except ValueError as exc: raise HTTPException(400,str(exc)) from None

    @routes.get('/projects')
    def projects(): return s().list()

    @routes.post('/projects',status_code=201)
    def create(data:ProjectInput): return call(s().save,data.model_dump())

    @routes.get('/projects/{id}')
    def get(id:str): return call(s().get,id)

    @routes.put('/projects/{id}')
    def update(id:str,data:ProjectInput): return call(s().save,data.model_dump(),id)

    @routes.delete('/projects/{id}',status_code=204)
    def delete(id:str): call(s().delete,id)

    @routes.put('/projects/{id}/brief')
    def brief(id:str,data:dict): return call(s().save_brief,id,data)

    @routes.get('/projects/{id}/analysis')
    def analysis(id:str): return call(s().get,id)['analysis']

    @routes.post('/projects/{id}/analyze',status_code=202)
    def analyze(id:str,data:GenerateInput | None=None): return call(s().enqueue,app.state.queue,id,'analyze',(data or GenerateInput()).model_dump())

    @routes.post('/projects/{id}/plan',status_code=202)
    def plan(id:str,data:GenerateInput | None=None): return call(s().enqueue,app.state.queue,id,'plan',(data or GenerateInput()).model_dump())

    @routes.put('/projects/{id}/script')
    def script(id:str,data:dict): return call(s().save_document,id,'script',data)

    @routes.put('/projects/{id}/treatment')
    def treatment(id:str,data:dict): return call(s().save_document,id,'treatment',data)

    @routes.post('/projects/{id}/generate',status_code=202)
    def generate(id:str,data:GenerateInput): return call(s().enqueue,app.state.queue,id,'generate',data.model_dump())

    @routes.post('/projects/{id}/shots/{shot_id}/regenerate',status_code=202)
    def regenerate(id:str,shot_id:str,data:RegenerateInput):
        return call(s().enqueue,app.state.queue,id,'regenerate',dict(data.model_dump(),shot_id=shot_id))

    @routes.post('/projects/{id}/assemble',status_code=202)
    def assemble(id:str): return call(s().enqueue,app.state.queue,id,'assemble')

    @routes.post('/projects/{id}/render',status_code=202)
    def render(id:str): return call(s().enqueue,app.state.queue,id,'render')

    @routes.post('/projects/{id}/variants',status_code=201)
    def variants(id:str,data:VariantsInput): return call(s().variants,id,data.count,data.dimensions)

    @routes.post('/projects/{id}/batch/{operation}',status_code=202)
    def batch(id:str,operation:Literal['plan','generate'],data:GenerateInput):
        return call(s().batch,app.state.queue,id,operation,data.model_dump())

    @routes.get('/projects/{id}/cost')
    def cost(id:str,shot_id:str | None=None,force:bool=False): return call(s().cost,id,shot_id,force)

    @routes.get('/projects/{id}/media')
    def media(id:str): return call(s().media,id)

    @routes.get('/media/{id}')
    def preview(id:str):
        row=app.state.db.one("SELECT path FROM generated_media WHERE id=? AND status='succeeded'",(id,))
        return serve(row)

    def serve(row):
        root=s().directory
        path=Path(row['path']).resolve() if row else root
        if not row or not path.is_relative_to(root) or not path.is_file(): raise HTTPException(404,'生成素材不存在')
        return FileResponse(path)

    @routes.get('/output/{asset_id}')
    def output(asset_id:str):
        return serve(app.state.db.one('SELECT a.path FROM assets a JOIN clone_projects p ON p.output_asset_id=a.id WHERE a.id=?',(asset_id,)))

    @routes.post('/assets/upload',status_code=201)
    async def upload(request:Request,kind:Literal['image','video','audio']='image'):
        # Raw streaming body avoids another multipart dependency and caps disk usage.
        suffix={'image':'.png','video':'.mp4','audio':'.wav'}[kind]
        target=s().directory/(uuid4().hex+suffix)
        size=0
        try:
            with target.open('wb') as dest:
                async for block in request.stream():
                    size+=len(block)
                    if size>512*1024*1024: raise HTTPException(413,'素材超过 512MB')
                    dest.write(block)
            info=probe(target)
            if kind=='image':
                if not info['width'] or info['duration']>0: raise ValueError()
                info['type']='image'
            elif info['type']!=kind: raise ValueError()
            return app.state.db.add_asset(target,info)
        except BaseException as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc,HTTPException): raise
            raise HTTPException(400,'上传文件类型或媒体内容无效') from None

    return routes
