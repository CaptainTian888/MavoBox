"""Serve the built frontend (frontend/dist) from the backend for packaged use."""
import os
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def find_dist():
    """Locate the frontend build: env override, PyInstaller bundle, or the repo checkout."""
    candidates = []
    if os.environ.get('TIKTOL_STATIC_DIR'):
        candidates.append(Path(os.environ['TIKTOL_STATIC_DIR']))
    if getattr(sys, '_MEIPASS', None):
        candidates.append(Path(sys._MEIPASS) / 'web')
    candidates.append(Path(__file__).resolve().parents[2] / 'frontend' / 'dist')
    for path in candidates:
        if (path / 'index.html').is_file():
            return path.resolve()
    return None


def mount_frontend(app, dist=None):
    dist = Path(dist).resolve() if dist else find_dist()
    if not dist or not (dist / 'index.html').is_file():
        return False

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        if path.startswith('api/') or path == 'api':
            raise HTTPException(404, '接口不存在')
        target = (dist / path).resolve()
        if path and target.is_file() and target.is_relative_to(dist):
            return FileResponse(target)
        return FileResponse(dist / 'index.html')

    return True
