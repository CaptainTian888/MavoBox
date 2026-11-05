import subprocess
import pytest
from fastapi.testclient import TestClient
from api.app import create_app
from core.config import Settings
from core.ffmpeg import locate
from core.secrets import MemorySecrets


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path / 'data', retries=0), MemorySecrets())
    with TestClient(app) as client:
        yield client


@pytest.fixture
def video(tmp_path):
    try:
        ffmpeg = locate('ffmpeg')
        locate('ffprobe')
    except RuntimeError:
        pytest.skip('ffmpeg/ffprobe unavailable')
    path = tmp_path / 'sample.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc=size=160x120:rate=15', '-t', '2', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
    return path
