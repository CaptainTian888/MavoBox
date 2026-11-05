"""成片质检接口：对登记的视频素材做一次自动检查，并生成缩略图联系表。"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from modules import qa

ASSET_ID = re.compile(r'^[0-9a-f]{32}$')


class QAInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_seconds: float | None = Field(default=None, gt=0, le=3600, allow_inf_nan=False)


def qa_router(app):
    routes = APIRouter(prefix='/api/qa')

    def sheet_dir():
        directory = app.state.settings.data_dir / 'qa'
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @routes.post('/{asset_id}')
    def run_check(asset_id: str, data: QAInput | None = None):
        if not ASSET_ID.match(asset_id):
            raise HTTPException(404, '素材不存在')
        row = app.state.db.one('SELECT * FROM assets WHERE id=?', (asset_id,))
        if not row or not Path(row['path']).is_file():
            raise HTTPException(404, '素材不存在或文件已被移动')
        if row['type'] != 'video':
            raise HTTPException(422, '只能检查视频素材')
        try:
            report = qa.inspect(row['path'], sheet_dir() / f'{asset_id}.jpg', data.target_seconds if data else None)
        except (ValueError, OSError):
            raise HTTPException(422, '无法检查该文件，请确认它是有效的视频') from None
        except Exception:
            raise HTTPException(500, '质检失败，请查看日志') from None
        report['asset_id'] = asset_id
        report['sheet_url'] = f'/api/qa/sheet/{asset_id}.jpg' if report['sheet'] else None
        return report

    @routes.get('/sheet/{name}')
    def sheet(name: str):
        stem = name[:-4] if name.endswith('.jpg') else ''
        if not ASSET_ID.match(stem):
            raise HTTPException(404, '不存在')
        path = (sheet_dir() / f'{stem}.jpg').resolve()
        if not path.is_file() or path.parent != sheet_dir().resolve():
            raise HTTPException(404, '不存在')
        return FileResponse(path, media_type='image/jpeg')

    return routes
