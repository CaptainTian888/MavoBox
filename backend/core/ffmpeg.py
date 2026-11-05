import json
import os
import sys
from pathlib import Path
import shutil
import subprocess


def bundled_dir():
    """Directory holding ffmpeg/ffprobe shipped inside the packaged app (PyInstaller bundle), if any."""
    base = getattr(sys, '_MEIPASS', None)
    if base and (Path(base) / 'bin').is_dir():
        return Path(base) / 'bin'
    return None


def locate(name):
    exe = name + ('.exe' if os.name == 'nt' else '')
    bundled = bundled_dir()
    if bundled and (bundled / exe).is_file():
        return str(bundled / exe)
    override = os.environ.get("FFMPEG_PATH")
    if override:
        path = Path(override)
        candidate = path / exe if path.is_dir() else (
            path if name == 'ffmpeg' else path.with_name('ffprobe' + path.suffix))
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} unavailable")
    return found


def probe(path):
    result = subprocess.run([locate('ffprobe'), '-v', 'error', '-show_format',
        '-show_streams', '-of', 'json', str(path)], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("无法读取媒体元数据")
    data = json.loads(result.stdout)
    video = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    audio = any(s['codec_type'] == 'audio' for s in data['streams'])
    if not video and not audio:
        raise ValueError("文件不包含音视频")
    return dict(type='video' if video else 'audio',
        duration=float(data.get('format', {}).get('duration', 0)),
        width=video.get('width') if video else None, height=video.get('height') if video else None)


def trim(source, target, seconds=5, cancelled=lambda: False):
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    from .queue import Cancelled
    with subprocess.Popen([locate('ffmpeg'), '-nostdin', '-v', 'error', '-y',
            '-i', str(source), '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-movflags', '+faststart', str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        while True:
            if cancelled():
                process.kill()
                process.wait()
                raise Cancelled()
            try:
                code = process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    if code:
        raise RuntimeError("视频截取失败")
