"""Desktop entry point: start the local server and open the UI.

Used by the PyInstaller build and by the Tauri shell (sidecar). Flags:
  --no-browser   do not open the system browser (Tauri shows its own window)
  --port N       preferred port (default 8000; falls back to a free port)
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser


def pick_port(preferred):
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError('没有可用端口')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=int(os.environ.get('TIKTOL_PORT', '8000')))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)

    from core.config import Settings
    from core.instance import InstanceLock
    lock = InstanceLock(Settings().data_dir)
    if not lock.acquire():
        # Another backend owns this data directory: point at it instead of starting a second one.
        url = lock.running_url()
        if not url:
            print('TIKTOL_ERROR 另一个实例正在启动或运行，但无法读取其地址', flush=True)
            return 3
        print(f'TIKTOL_READY {url}', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    port = pick_port(args.port)
    os.environ['TIKTOL_PORT'] = str(port)

    import uvicorn
    from api.app import create_app

    app = create_app()
    url = f'http://127.0.0.1:{port}'
    lock.publish(url)
    print(f'TIKTOL_READY {url}', flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    try:
        uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
    finally:
        lock.release()
    return 0


if __name__ == '__main__':
    sys.exit(main())
