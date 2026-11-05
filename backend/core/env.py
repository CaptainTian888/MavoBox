"""Environment self-check and one-click Chromium install for the first-run wizard."""
import os
import platform
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from core import ffmpeg


def tool_status(name):
    try:
        path = ffmpeg.locate(name)
    except RuntimeError:
        return dict(ok=False, path=None, source=None)
    bundled = ffmpeg.bundled_dir()
    source = 'bundled' if bundled and Path(path).parent == bundled else 'system'
    return dict(ok=True, path=path, source=source)


def headless_shell_present(chromium_path):
    """Headless publishing uses Playwright's separate chromium-headless-shell download; require it too,
    otherwise a half-finished install would look complete and fail on the first publish."""
    try:
        browsers = Path(chromium_path).parents[2]  # <browsers>/chromium-NNNN/<chrome-dir>/<exe>
    except IndexError:
        return False
    for folder in browsers.glob('chromium_headless_shell-*'):
        if any(folder.rglob('*headless*shell*')) or any(folder.rglob('chrome-headless-shell*')):
            return True
    return False


def chromium_status():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        ok = bool(path) and Path(path).is_file() and headless_shell_present(path)
        return dict(ok=ok, path=path)
    except Exception:
        return dict(ok=False, path=None)


class BrowserInstaller:
    """Runs `playwright install chromium` with the driver bundled in the app (no Python / npm needed)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'idle'  # idle | running | done | failed
        self.log = deque(maxlen=60)
        self.thread = None

    def snapshot(self):
        with self.lock:
            return dict(state=self.state, log=list(self.log))

    def start(self, mirror=None):
        if mirror:
            parts = urlsplit(mirror)
            if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
                raise ValueError('镜像地址必须是 http(s) 链接')
        with self.lock:
            if self.state == 'running':
                return
            self.state, self.thread = 'running', None
            self.log.clear()
        self.thread = threading.Thread(target=self._run, args=(mirror,), daemon=True)
        self.thread.start()

    def _command(self):
        from playwright._impl._driver import compute_driver_executable
        driver = compute_driver_executable()
        return [*driver, 'install', 'chromium'] if isinstance(driver, (tuple, list)) else [str(driver), 'install', 'chromium']

    def _run(self, mirror):
        try:
            from playwright._impl._driver import get_driver_env
            env = {**os.environ, **get_driver_env()}
            if mirror:
                env['PLAYWRIGHT_DOWNLOAD_HOST'] = mirror.rstrip('/')
            flags = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW
            proc = subprocess.Popen(self._command(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', creationflags=flags)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    with self.lock:
                        self.log.append(line[-300:])
            code = proc.wait()
            ok = code == 0 and chromium_status()['ok']
            with self.lock:
                self.state = 'done' if ok else 'failed'
                if not ok:
                    self.log.append(f'安装失败（退出码 {code}）。可换用镜像地址后重试，或检查网络/代理。')
        except Exception as exc:
            with self.lock:
                self.state = 'failed'
                self.log.append(f'安装出错：{type(exc).__name__}')


def environment(data_dir, installer):
    from core.version import APP_NAME, APP_VERSION
    return dict(app=APP_NAME, version=APP_VERSION, platform=sys.platform, machine=platform.machine(),
                data_dir=str(data_dir), ffmpeg=tool_status('ffmpeg'), ffprobe=tool_status('ffprobe'),
                chromium=chromium_status(), install=installer.snapshot())
