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
    port = pick_port(args.port)
    os.environ['TIKTOL_PORT'] = str(port)

    import uvicorn
    from api.app import create_app

    app = create_app()
    url = f'http://127.0.0.1:{port}'
    print(f'TIKTOL_READY {url}', flush=True)
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


if __name__ == '__main__':
    main()
