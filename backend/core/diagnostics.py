"""Support report: everything needed to debug a student's problem, with no secrets in it."""
import os
import platform
import sys
from pathlib import Path
from urllib.parse import urlsplit

from core import env as envmod
from core import logs
from core.version import APP_NAME, APP_VERSION


def folder_sizes(data_dir, cap=50_000):
    """Top-level sizes of the data folder (MB), counting at most `cap` files to stay fast."""
    sizes, seen = {}, 0
    root = Path(data_dir)
    try:
        entries = list(root.iterdir())
    except OSError:
        return sizes
    for entry in entries:
        total = 0
        try:
            if entry.is_dir():
                for current, _dirs, files in os.walk(entry):
                    for name in files:
                        seen += 1
                        if seen > cap:
                            break
                        try:
                            total += os.path.getsize(os.path.join(current, name))
                        except OSError:
                            pass
                    if seen > cap:
                        break
            elif entry.is_file():
                total = entry.stat().st_size
        except OSError:
            continue
        sizes[entry.name] = round(total / 1_048_576, 1)
    return sizes


def provider_summary(db):
    rows = db.all('SELECT type, name, base_url, model, api_style FROM providers ORDER BY type, name')
    out = []
    for row in rows:
        host = urlsplit(row['base_url']).netloc or '(无)'
        out.append(dict(type=row['type'], style=row['api_style'], host=host, model=row['model']))
    return out


def build(db, settings, license_status, installer, gateway=None):
    env = envmod.environment(settings.data_dir, installer)
    counts = {r['status']: r['n'] for r in db.all('SELECT status, COUNT(*) AS n FROM tasks GROUP BY status')}
    failures = db.all("SELECT kind, error, created_at FROM tasks WHERE status='failed' ORDER BY created_at DESC LIMIT 8")
    report = dict(
        app=f'{APP_NAME} {APP_VERSION}',
        system=f'{platform.platform()} / {platform.machine()}',
        frozen=bool(getattr(sys, 'frozen', False)),
        python=platform.python_version(),
        data_dir=str(settings.data_dir),
        folder_mb=folder_sizes(settings.data_dir),
        ffmpeg=env['ffmpeg'], ffprobe=env['ffprobe'], chromium=env['chromium'],
        license=dict(licensed=license_status['licensed'], expires=license_status['expires'],
                     name_set=bool(license_status['name'])),
        providers=provider_summary(db),
        gateway=({k: gateway.get(k) for k in ('available', 'enabled', 'running', 'version', 'port')} if gateway else None),
        tasks=counts,
        recent_failures=[dict(kind=f['kind'], error=logs.scrub(str(f['error'] or ''))[:300], at=f['created_at']) for f in failures],
        log_tail=logs.tail(settings.data_dir, 40),
    )
    report['text'] = render_text(report)
    return report


def render_text(r) -> str:
    def tool(t):
        return f"{'OK' if t['ok'] else '缺失'}" + (f" ({t.get('source') or 'path'})" if t.get('ok') and 'source' in t else '')
    lines = [
        f"## {r['app']} 诊断报告",
        f"系统: {r['system']}  打包版: {r['frozen']}  Python: {r['python']}",
        f"数据目录: {r['data_dir']}",
        f"目录占用(MB): {r['folder_mb']}",
        f"ffmpeg: {tool(r['ffmpeg'])}  ffprobe: {tool(r['ffprobe'])}  Chromium: {'OK' if r['chromium']['ok'] else '未安装/不完整'}",
        f"授权: {'已激活' if r['license']['licensed'] else '未激活'}  到期: {r['license']['expires'] or '永久/未知'}",
        f"AI 服务({len(r['providers'])}): " + '; '.join(f"{p['type']}/{p['style']}@{p['host']}:{p['model']}" for p in r['providers']),
        f"内置网关: {('版本 ' + str(r['gateway']['version']) + ('，运行中' if r['gateway']['running'] else '，未运行') + ('，已启用' if r['gateway']['enabled'] else '，未启用')) if r['gateway'] and r['gateway']['available'] else '未内置'}",
        f"任务统计: {r['tasks']}",
        '最近失败任务:' if r['recent_failures'] else '最近失败任务: 无',
        *[f"  - {f['at']} {f['kind']}: {f['error']}" for f in r['recent_failures']],
        '--- 日志末尾 ---',
        *r['log_tail'],
        '(报告不含 API Key、授权码和账号 Cookie)',
    ]
    return '\n'.join(lines)
