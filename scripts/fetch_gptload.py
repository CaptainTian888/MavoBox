"""Fetch the LATEST GPT-Load release for bundling into the installer (run by build.cmd / build.sh).

  python scripts/fetch_gptload.py [--platform windows-amd64] [--major 2] [--out vendor/gpt-load] [--allow-stale] [--from-file PATH]

* "Latest" = the release the maintainers flag as latest on GitHub (releases/latest). If that release's
  major version is not --major (an incompatible line), the build stops instead of bundling it.
* The binary is verified against the SHA256SUMS published in the same release before it is used.
* Licence texts and THIRD_PARTY_NOTICES.md of the release are saved next to it and shipped in the installer.
* Nothing is replaced unless the download verified; an up-to-date copy is not downloaded again.
* If the latest version cannot be determined (offline, rate limit) the script FAILS, so a build can never
  silently ship an outdated gateway. --allow-stale reuses an existing copy and says so loudly.

Output folder: gpt-load(.exe), VERSION (json), licenses/. Standard library only.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = 'tbphp/gpt-load'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ROOT = Path(__file__).resolve().parents[1]
LICENSE_ASSETS = ('LICENSE', 'MIT.txt', 'Apache-2.0.txt', 'MPL-2.0.txt', 'Inno-Setup.txt', 'THIRD_PARTY_NOTICES.md', 'SHA256SUMS')


def asset_name(platform: str) -> str:
    return f'gpt-load-{platform}' + ('.exe' if platform.startswith('windows') else '')


def request(url: str, accept=None):
    headers = {'User-Agent': 'mavobox-build', 'Accept': accept or '*/*'}
    token = os.environ.get('GITHUB_TOKEN')
    if token and url.startswith('https://api.github.com/'):
        headers['Authorization'] = f'Bearer {token}'
    return urllib.request.Request(url, headers=headers)


def get_json(url: str):
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request(url, 'application/vnd.github+json'), timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'无法访问 GitHub API：{last}')


def download(url: str, target: Path, expected_size=None, retries=8):
    """Download with resume + retries (GitHub's CDN occasionally drops long transfers)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        have = target.stat().st_size if target.exists() else 0
        if expected_size and have == expected_size:
            return
        req = request(url)
        if have:
            req.add_header('Range', f'bytes={have}-')
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                mode = 'ab' if have and response.status == 206 else 'wb'
                with target.open(mode) as out:
                    shutil.copyfileobj(response, out, 1 << 20)
        except (urllib.error.URLError, TimeoutError, OSError, ConnectionError) as exc:
            print(f'  下载中断（第 {attempt + 1} 次）：{exc}；重试…', flush=True)
            time.sleep(2 * (attempt + 1))
            continue
        if not expected_size or target.stat().st_size == expected_size:
            return
    raise RuntimeError(f'下载失败：{url}')


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
    parser.add_argument('--platform', default='windows-amd64')
    parser.add_argument('--major', type=int, default=2)
    parser.add_argument('--out', default=str(ROOT / 'vendor' / 'gpt-load'))
    parser.add_argument('--allow-stale', action='store_true')
    parser.add_argument('--from-file', help='use a binary you downloaded yourself (still verified against the release SHA256SUMS)')
    args = parser.parse_args()
    out = Path(args.out)
    binary_name = 'gpt-load' + ('.exe' if args.platform.startswith('windows') else '')
    version_file = out / 'VERSION'
    current = json.loads(version_file.read_text(encoding='utf-8')) if version_file.is_file() else None

    try:
        release = get_json(API)
    except RuntimeError as exc:
        if args.allow_stale and current and (out / binary_name).is_file():
            print(f'警告：{exc}\n警告：改用本地已有的 GPT-Load {current["tag"]}（可能不是最新版）', file=sys.stderr)
            return 0
        print(f'错误：{exc}\n无法确认 GPT-Load 的最新版本，已停止打包（可加 --allow-stale 使用本地旧版）。', file=sys.stderr)
        return 1

    tag = release['tag_name']
    match = re.match(r'v?(\d+)\.', tag)
    if not match or int(match.group(1)) != args.major:
        print(f'错误：GPT-Load 最新版本是 {tag}，主版本不是 {args.major}，与本应用的集成方式可能不兼容。请人工确认后再调整 --major。', file=sys.stderr)
        return 1
    assets = {a['name']: a for a in release['assets']}
    wanted = asset_name(args.platform)
    if wanted not in assets or 'SHA256SUMS' not in assets:
        print(f'错误：版本 {tag} 里没有 {wanted} 或 SHA256SUMS', file=sys.stderr)
        return 1

    if current and current.get('tag') == tag and current.get('platform') == args.platform and (out / binary_name).is_file():
        if sha256(out / binary_name) == current.get('sha256'):
            print(f'GPT-Load 已是最新：{tag}（无需重新下载）')
            return 0

    print(f'最新版本 {tag}，下载 {wanted}（{assets[wanted]["size"] / 1048576:.0f} MB）…', flush=True)
    work = out.with_name(out.name + '.partial')
    work.mkdir(parents=True, exist_ok=True)
    sums_path = work / 'SHA256SUMS'
    sums_path.unlink(missing_ok=True)
    download(assets['SHA256SUMS']['browser_download_url'], sums_path)
    expected = None
    for line in sums_path.read_text(encoding='utf-8').splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == wanted:
            expected = parts[0].lower()
    if not expected:
        print(f'错误：SHA256SUMS 里没有 {wanted} 的校验值', file=sys.stderr)
        return 1
    partial = work / wanted
    if args.from_file:
        shutil.copyfile(args.from_file, partial)
    else:
        download(assets[wanted]['browser_download_url'], partial, assets[wanted]['size'])
    actual = sha256(partial)
    if actual != expected:
        partial.unlink(missing_ok=True)
        print(f'错误：校验失败（期望 {expected}，实际 {actual}），未使用该文件。', file=sys.stderr)
        return 1
    print('SHA-256 校验通过', flush=True)

    licenses = work / 'licenses'
    licenses.mkdir(exist_ok=True)
    for name in LICENSE_ASSETS:
        if name in assets:
            download(assets[name]['browser_download_url'], licenses / name, assets[name]['size'])

    # Everything verified: swap in atomically-ish (keep the old copy until the new one is in place).
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(partial, out / (binary_name + '.new'))
    os.replace(out / (binary_name + '.new'), out / binary_name)
    shutil.rmtree(out / 'licenses', ignore_errors=True)
    shutil.copytree(licenses, out / 'licenses')
    version_file.write_text(json.dumps(dict(
        tag=tag, platform=args.platform, sha256=actual, source=release['html_url'],
        published_at=release.get('published_at'), fetched_at=dt.datetime.now(dt.timezone.utc).isoformat()),
        indent=2) + '\n', encoding='utf-8')
    shutil.rmtree(work, ignore_errors=True)
    print(f'完成：GPT-Load {tag} -> {out / binary_name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
