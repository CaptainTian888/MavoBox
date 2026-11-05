import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4
from core.db import now
from core.ffmpeg import probe
from core.queue import Cancelled
from .scenes import SceneAnalyzer, params_hash
from .tagging import LLMClient, tag_shots
from .planner import plan, validate_timeline
from .renderer import render


BUSY = ('analyzing', 'planning', 'rendering')
VIDEO_SUFFIXES = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}


def is_link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def local_path(value, directory=False):
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('需要不含上级跳转的本地绝对路径')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('本地路径不存在')
    return path.resolve()


def scan(db, folder, recursive=False):
    raw = Path(folder).expanduser()
    if is_link(raw):
        raise ValueError('扫描根目录不能为链接')
    root = local_path(folder, directory=True)
    paths = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in sorted(dirs) if not is_link(Path(current) / d)]
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix.lower() not in VIDEO_SUFFIXES or path.is_symlink():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            paths.append(resolved)
        if not recursive:
            break
    output, skipped = [], 0
    for path in paths:
        existing = db.one('SELECT * FROM assets WHERE path=?', (str(path),))
        if existing:
            output.append(existing)
            continue
        try:
            metadata = probe(path)
            if metadata['type'] != 'video':
                skipped += 1
                continue
            output.append(db.add_asset(path, metadata))
        except Exception:
            skipped += 1
    return {'assets': output, 'skipped': skipped}


class RemixService:
    def __init__(self, db, secrets, settings, client_factory=LLMClient):
        self.db, self.secrets, self.settings = db, secrets, settings
        self.client_factory, self.lock = client_factory, RLock()
        self.scenes = SceneAnalyzer(db, settings.data_dir)
        db.execute("UPDATE remix_projects SET status='failed' WHERE status IN ('analyzing','planning','rendering')")

    def get(self, project_id):
        row = self.db.one('SELECT * FROM remix_projects WHERE id=?', (project_id,))
        if row is None:
            raise LookupError('混剪项目不存在')
        row['settings'] = json.loads(row['settings'])
        row['timeline'] = json.loads(row['timeline']) if row['timeline'] else None
        row['asset_ids'] = [r['asset_id'] for r in self.db.all(
            'SELECT asset_id FROM remix_project_assets WHERE project_id=? ORDER BY rowid', (project_id,))]
        return row

    def list(self):
        return [self.get(r['id']) for r in self.db.all('SELECT id FROM remix_projects ORDER BY updated_at DESC')]

    def require_idle(self, project_id):
        project = self.get(project_id)
        active = self.db.all("SELECT payload FROM tasks WHERE kind LIKE 'remix_%' AND status IN ('pending','running')")
        if project['status'] in BUSY or any(json.loads(t['payload']).get('project_id') == project_id for t in active):
            raise ValueError('项目有任务正在运行，请先等待或取消')
        return project

    def save(self, data, project_id=None):
        with self.lock:
            if project_id:
                self.require_idle(project_id)
            settings = data['settings']
            if data['width'] % 2 or data['height'] % 2:
                raise ValueError('画幅尺寸必须为偶数')
            if settings.get('provider_id'):
                self.provider(settings['provider_id'])
            if settings.get('bgm_path'):
                settings['bgm_path'] = str(local_path(settings['bgm_path']))
            ids = list(dict.fromkeys(data['asset_ids']))
            for asset_id in ids:
                row = self.db.one('SELECT * FROM assets WHERE id=?', (asset_id,))
                if not row or row['type'] != 'video' or not row['duration'] or row['duration'] <= 0:
                    raise ValueError('需要有效的视频素材')
                local_path(row['path'])
            project_id = project_id or uuid4().hex
            with self.db.connect() as con:
                con.execute('''INSERT INTO remix_projects
                    (id,name,brief_text,target_seconds,width,height,settings,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    brief_text=excluded.brief_text,target_seconds=excluded.target_seconds,width=excluded.width,
                    height=excluded.height,settings=excluded.settings,timeline=NULL,output_asset_id=NULL,
                    status='draft',updated_at=excluded.updated_at''', (project_id, data['name'], data['brief_text'],
                    data['target_seconds'], data['width'], data['height'], json.dumps(settings), now(), now()))
                con.execute('DELETE FROM remix_project_assets WHERE project_id=?', (project_id,))
                con.executemany('INSERT INTO remix_project_assets VALUES (?,?)', [(project_id, a) for a in ids])
            return self.get(project_id)

    def delete(self, project_id):
        with self.lock:
            self.require_idle(project_id)
            with self.db.connect() as con:
                con.execute('DELETE FROM remix_project_assets WHERE project_id=?', (project_id,))
                con.execute('DELETE FROM remix_projects WHERE id=?', (project_id,))

    def provider(self, provider_id):
        row = self.db.one("SELECT * FROM providers WHERE id=? AND type='llm'", (provider_id,))
        if not row:
            raise ValueError('请选择已配置的 LLM Provider')
        return row

    def client(self, project):
        row = self.provider(project['settings'].get('provider_id', ''))
        key = self.secrets.get(row['id'])
        if not key:
            raise ValueError('请先配置 Provider 的 API Key')
        return self.client_factory(row, key)

    def tag_hash(self, project):
        row = self.provider(project['settings'].get('provider_id', ''))
        return hashlib.sha256(json.dumps([1, row['id'], row['base_url'], row['model']]).encode()).hexdigest()

    def shots(self, project_id):
        p = self.get(project_id)
        key = params_hash(p['settings']['threshold'], p['settings']['segment_seconds'])
        rows = self.db.all('''SELECT s.* FROM shots s JOIN remix_project_assets a ON s.asset_id=a.asset_id
            WHERE a.project_id=? AND s.params_hash=? ORDER BY a.rowid,s.start''', (project_id, key))
        for row in rows:
            row['tags'] = json.loads(row['tags'])
            row['tagged'], row['has_person'] = bool(row['tagged']), bool(row['has_person'])
            row['frame_url'] = '/api/remix/frames/' + Path(row['frame_path']).name
        return rows

    def estimate(self, project_id):
        p = self.get(project_id)
        cached = self.shots(project_id)
        cached_ids = {s['asset_id'] for s in cached}
        missing = [self.db.one('SELECT * FROM assets WHERE id=?', (a,)) for a in p['asset_ids'] if a not in cached_ids]
        try:
            tag_hash = self.tag_hash(p)
        except ValueError:
            tag_hash = ''
        count = sum(not s['tagged'] or s['tagging_hash'] != tag_hash for s in cached)
        count += sum(math.ceil(a['duration'] / p['settings']['segment_seconds']) for a in missing)
        return dict(images=count, approximate=bool(missing), message=f'预计将发送 {count} 张图给模型；未切分素材按均分估算，实际切点可能不同。')

    def check_timeline(self, project, value):
        result = validate_timeline(value, self.shots(project['id']), project['target_seconds'],
            allow_repeat=project['settings']['allow_repeat'])
        if (result['width'], result['height']) != (project['width'], project['height']):
            raise ValueError('画幅必须与项目设置一致')
        if result['bgm']:
            if result['bgm']['path'] != project['settings']['bgm_path']:
                raise ValueError('BGM 必须为项目指定的本地文件')
            local_path(result['bgm']['path'])
        font = result['global_caption_style']['font']
        if font:
            local_path(font)
        return result

    def save_timeline(self, project_id, value):
        with self.lock:
            p = self.require_idle(project_id)
            result = self.check_timeline(p, value)
            self.db.execute("UPDATE remix_projects SET timeline=?,status='planned',output_asset_id=NULL,updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), now(), project_id))
            return self.get(project_id)

    def enqueue(self, queue, project_id, operation, tag=True):
        with self.lock:
            p = self.require_idle(project_id)
            if (operation == 'analyze' and tag) or operation == 'plan':
                self.client(p)  # Fail synchronously for missing configuration, without sending images.
            if not p['asset_ids']:
                raise ValueError('请先选择素材')
            if operation == 'plan' and not self.shots(project_id):
                raise ValueError('请先分析素材')
            if operation == 'render':
                self.check_timeline(p, p['timeline'])
            status = {'analyze': 'analyzing', 'plan': 'planning', 'render': 'rendering'}[operation]
            self.db.execute('UPDATE remix_projects SET status=?,updated_at=? WHERE id=?', (status, now(), project_id))
            try:
                # LLM retries are bounded inside each stage; do not re-bill the whole job automatically.
                return queue.create('remix_' + operation, {'project_id': project_id, 'tag': tag}, max_retries=0)
            except Exception:
                self.db.execute("UPDATE remix_projects SET status='failed' WHERE id=?", (project_id,))
                raise

    def task_finished(self, task):
        if task and task['kind'].startswith('remix_') and task['status'] in ('cancelled', 'failed'):
            payload = json.loads(task['payload'])
            with self.lock:
                self.db.execute("UPDATE remix_projects SET status=?,updated_at=? WHERE id=? AND status IN ('analyzing','planning','rendering')",
                    (task['status'], now(), payload['project_id']))

    def retry_task(self, queue, task_id):
        with self.lock:
            task = self.db.one('SELECT * FROM tasks WHERE id=?', (task_id,))
            payload = json.loads(task['payload'])
            project_id = payload['project_id']
            self.require_idle(project_id)
            status = {'remix_analyze': 'analyzing', 'remix_plan': 'planning', 'remix_render': 'rendering'}[task['kind']]
            self.db.execute('UPDATE remix_projects SET status=?,updated_at=? WHERE id=?', (status, now(), project_id))
            try:
                return queue.retry(task_id)
            except Exception:
                self.db.execute("UPDATE remix_projects SET status='failed' WHERE id=?", (project_id,))
                raise

    def handler(self, operation):
        def handle(payload, progress, cancelled):
            project_id = payload['project_id']
            p = self.get(project_id)
            self.db.execute('UPDATE remix_projects SET status=?,updated_at=? WHERE id=?',
                ({'analyze': 'analyzing', 'plan': 'planning', 'render': 'rendering'}[operation], now(), project_id))
            try:
                if operation == 'analyze':
                    for i, asset_id in enumerate(p['asset_ids']):
                        asset = self.db.one('SELECT * FROM assets WHERE id=?', (asset_id,))
                        self.scenes.analyze(asset, p['settings']['threshold'], p['settings']['segment_seconds'], cancelled)
                        progress((i + 1) / len(p['asset_ids']) * 50)
                    if payload.get('tag', True):
                        tag_hash = self.tag_hash(p)
                        pending = [s for s in self.shots(project_id) if not s['tagged'] or s['tagging_hash'] != tag_hash]
                        client = self.client(p)
                        for offset in range(0, len(pending), 6):
                            for shot in tag_shots(client, pending[offset:offset+6], cancelled=cancelled):
                                self.db.execute('''UPDATE shots SET description=?,tags=?,shot_type=?,has_person=?,quality=?,
                                    tagged=?,suggested_use=?,tagging_hash=? WHERE id=?''', (shot['description'], json.dumps(shot['tags'], ensure_ascii=False),
                                    shot['shot_type'], shot['has_person'], shot['quality'], shot['tagged'], shot['suggested_use'], tag_hash, shot['id']))
                            progress(50 + (offset + 6) / max(1, len(pending)) * 48)
                    status = 'analyzed'
                elif operation == 'plan':
                    options = dict(p['settings'])
                    options.pop('provider_id', None)
                    result = plan(self.client(p), self.shots(project_id), p['brief_text'], p['target_seconds'],
                        p['width'], p['height'], cancelled=cancelled, **options)
                    result = self.check_timeline(p, result)
                    self.db.execute('UPDATE remix_projects SET timeline=?,output_asset_id=NULL WHERE id=?',
                        (json.dumps(result, ensure_ascii=False), project_id))
                    status = 'planned'
                else:
                    timeline = self.check_timeline(p, p['timeline'])
                    target = self.settings.data_dir / 'media' / (uuid4().hex + '.mp4')
                    assets = {a: self.db.one('SELECT * FROM assets WHERE id=?', (a,)) for a in p['asset_ids']}
                    render(timeline, assets, target, progress, cancelled, p['settings']['fit'], p['settings']['font_path'])
                    try:
                        if cancelled():
                            raise Cancelled()
                        asset = self.db.add_asset(target.resolve(), probe(target))
                        self.db.execute('UPDATE remix_projects SET output_asset_id=? WHERE id=?', (asset['id'], project_id))
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
                    status = 'rendered'
                if cancelled():
                    raise Cancelled()
                self.db.execute('UPDATE remix_projects SET status=?,updated_at=? WHERE id=?', (status, now(), project_id))
            except BaseException:
                # The queue's completion callback releases the project only after cleanup.
                # This also handles cancellation of a job that never began execution.
                raise
        return handle
