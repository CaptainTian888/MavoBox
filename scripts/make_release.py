"""Assemble a downloadable release folder from the Tauri build output.

  uv run python scripts/make_release.py --version 0.1.0 --base-url https://github.com/<owner>/<repo>/releases/download/v0.1.0 [--notes "更新说明"]

Reads  src-tauri/target/release/bundle/nsis/MAVO BOX_<version>_x64-setup.exe(+.sig)  and writes release/v<version>/:
  MAVO-BOX_<version>_x64-setup.exe     (ASCII name without spaces, safe in URLs)
  MAVO-BOX_<version>_x64-setup.exe.sig
  latest.json                           (the feed the app checks; Tauri updater format)
  SHA256SUMS.txt
Upload all of them to the same release; host latest.json at the address configured in
src-tauri/tauri.conf.json -> plugins.updater.endpoints.
"""
import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--base-url', required=True, help='URL prefix under which the installer will be downloadable')
    parser.add_argument('--notes', default='')
    parser.add_argument('--bundle-dir', default=str(ROOT / 'src-tauri' / 'target' / 'release' / 'bundle' / 'nsis'))
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    source = Path(args.bundle_dir) / f'MAVO BOX_{args.version}_x64-setup.exe'
    signature = Path(str(source) + '.sig')
    if not source.is_file() or not signature.is_file():
        sys.exit(f'找不到安装包或签名：{source}（先运行 scripts\\tauri-build.ps1）')
    out = Path(args.out) if args.out else ROOT / 'release' / f'v{args.version}'
    out.mkdir(parents=True, exist_ok=True)
    name = f'MAVO-BOX_{args.version}_x64-setup.exe'
    shutil.copyfile(source, out / name)
    shutil.copyfile(signature, out / (name + '.sig'))
    feed = {
        'version': args.version,
        'notes': args.notes,
        'pub_date': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'platforms': {
            'windows-x86_64': {
                'signature': signature.read_text(encoding='utf-8').strip(),
                'url': args.base_url.rstrip('/') + '/' + name,
            }
        },
    }
    (out / 'latest.json').write_bytes((json.dumps(feed, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    lines = [f'{sha256(out / n)}  {n}' for n in (name, name + '.sig', 'latest.json')]
    (out / 'SHA256SUMS.txt').write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))  # LF endings so `sha256sum -c` works
    print('release folder:', out)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
