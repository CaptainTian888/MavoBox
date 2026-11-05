from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from modules.remix.service import scan


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ScanInput(Input):
    folder: str = Field(min_length=1, max_length=4096)
    recursive: bool = False


class RemixSettings(Input):
    provider_id: str = ''
    bgm_path: str = Field(default='', max_length=4096)
    captions: bool = False
    transition: Literal['none', 'fade', 'crossfade', 'slide'] = 'none'
    threshold: float = Field(default=27, gt=0, le=255, allow_inf_nan=False)
    segment_seconds: float = Field(default=4, ge=.25, le=300, allow_inf_nan=False)
    allow_repeat: bool = False
    fit: Literal['crop', 'blur'] = 'crop'
    font_path: str = Field(default='', max_length=4096)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=200)
    brief_text: str = Field(default='', max_length=20000)
    target_seconds: float = Field(default=30, ge=.5, le=3600, allow_inf_nan=False)
    width: int = Field(default=1080, ge=16, le=3840)
    height: int = Field(default=1920, ge=16, le=3840)
    settings: RemixSettings = Field(default_factory=RemixSettings)
    asset_ids: list[str] = Field(default_factory=list, max_length=200)


class AnalyzeInput(Input):
    tag: bool = True


def router(app):
    routes = APIRouter()

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError:
            raise HTTPException(404, '混剪项目不存在') from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def service():
        return app.state.remix

    @routes.post('/api/assets/scan')
    def scan_assets(data: ScanInput):
        return call(scan, app.state.db, data.folder, data.recursive)

    @routes.get('/api/remix/frames/{name}')
    def frame(name: str):
        root = (service().settings.data_dir / 'frames').resolve()
        path = (root / name).resolve()
        if (not path.is_relative_to(root) or path.parent != root or path.suffix != '.jpg'
                or not path.is_file() or not app.state.db.one('SELECT id FROM shots WHERE frame_path=?', (str(path),))):
            raise HTTPException(404, '关键帧不存在')
        return FileResponse(path, media_type='image/jpeg')

    @routes.get('/api/remix/output/{asset_id}')
    def output(asset_id: str):
        # Only renderer outputs registered by a project can be served, never arbitrary paths.
        row = app.state.db.one('''SELECT a.* FROM assets a JOIN remix_projects p ON p.output_asset_id=a.id
            WHERE a.id=?''', (asset_id,))
        root = (service().settings.data_dir / 'media').resolve()
        path = Path(row['path']).resolve() if row else root
        if not row or not path.is_relative_to(root) or path.suffix != '.mp4' or not path.is_file():
            raise HTTPException(404, '成片不存在')
        return FileResponse(path, media_type='video/mp4')

    @routes.get('/api/remix/projects')
    def projects():
        return service().list()

    @routes.post('/api/remix/projects', status_code=201)
    def create(data: ProjectInput):
        return call(service().save, data.model_dump())

    @routes.get('/api/remix/projects/{project_id}')
    def get(project_id: str):
        return call(service().get, project_id)

    @routes.put('/api/remix/projects/{project_id}')
    def update(project_id: str, data: ProjectInput):
        return call(service().save, data.model_dump(), project_id)

    @routes.delete('/api/remix/projects/{project_id}', status_code=204)
    def delete(project_id: str):
        call(service().delete, project_id)

    @routes.get('/api/remix/projects/{project_id}/estimate')
    def estimate(project_id: str):
        return call(service().estimate, project_id)

    @routes.get('/api/remix/projects/{project_id}/shots')
    def shots(project_id: str):
        return call(service().shots, project_id)

    @routes.post('/api/remix/projects/{project_id}/analyze', status_code=202)
    def analyze(project_id: str, data: AnalyzeInput):
        return call(service().enqueue, app.state.queue, project_id, 'analyze', data.tag)

    @routes.post('/api/remix/projects/{project_id}/plan', status_code=202)
    def plan(project_id: str):
        return call(service().enqueue, app.state.queue, project_id, 'plan')

    @routes.put('/api/remix/projects/{project_id}/timeline')
    def timeline(project_id: str, data: dict):
        return call(service().save_timeline, project_id, data)

    @routes.post('/api/remix/projects/{project_id}/render', status_code=202)
    def render(project_id: str):
        return call(service().enqueue, app.state.queue, project_id, 'render')

    @routes.get('/api/remix/projects/{project_id}/preview-frames')
    def previews(project_id: str):
        p = call(service().get, project_id)
        shots = {s['id']: s for s in call(service().shots, project_id)}
        return [{'index': i, 'shot_id': c['shot_id'], 'url': shots[c['shot_id']]['frame_url']}
                for i, c in enumerate((p['timeline'] or {}).get('clips', [])) if c['shot_id'] in shots]

    return routes
