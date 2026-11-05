import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from core.ffmpeg import probe


def utc(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('时间必须包含时区')
    return value.astimezone(timezone.utc)


# Only application-owned messages enter logs/errors. Raw browser/driver exception
# strings, page text, request bodies and credentials never enter this channel.
MESSAGES = {
    'created': '发布任务已创建', 'started': '开始发布', 'success': '发布成功',
    'login': '登录已失效，请重新登录', 'attention': '需要人工处理，请查看本地截图并确认发布结果',
    'transient': '浏览器或网络暂时失败', 'failed': '发布失败，请检查浏览器和媒体',
    'restart': '应用中断，发布结果未知，请人工确认', 'missed': '离线期间错过发布时间',
    'cancel': '任务已取消', 'retry': '任务已安排重试', 'reschedule': '任务已改期',
    'complete': '用户已人工确认发布完成', 'media': '媒体不存在或不可读取',
    'ratio': '建议使用 9:16 竖屏视频', 'duration': '建议时长在 3 秒到 10 分钟之间',
    'size': '视频超过建议的 4GB 大小', 'probe': '无法探测视频规格，请人工确认',
}


def safe_result_url(value):
    if not value:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == 'https' and url.hostname in ('www.tiktok.com', 'tiktok.com')
                and not url.username and not url.password and not url.query and not url.fragment
                and url.port in (None, 443)):
            import re
            if re.fullmatch(r'/@[\w.\-]+/video/\d+', url.path):
                return value
    except (ValueError, TypeError):
        pass
    return None


class PublisherService:
    def __init__(self, db, secrets, settings, now=None, randint=None):
        self.db, self.secrets, self.settings = db, secrets, settings
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.randint = randint or random.randint

    def timestamp(self):
        return utc(self.now()).isoformat()

    def require(self, table, identifier):
        # table is always an internal constant, never request input.
        row = self.db.one(f'SELECT * FROM {table} WHERE id=?', (identifier,))
        if not row:
            raise ValueError('记录不存在')
        return row

    def account(self, identifier):
        return self.require('accounts', identifier)

    def save_account(self, data, identifier=None):
        old = self.account(identifier) if identifier else {}
        values = {**old, **data}
        for field, table in (('group_id', 'account_groups'), ('proxy_id', 'proxies')):
            if values.get(field):
                self.require(table, values[field])
        identifier = identifier or uuid4().hex
        profile = str((self.settings.data_dir / 'profiles' / identifier).resolve())
        self.db.execute('''INSERT INTO accounts
            (id,name,group_id,proxy_id,profile_dir,status,created_at,
            browser_locale,browser_timezone,browser_width,browser_height) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,group_id=excluded.group_id,
            proxy_id=excluded.proxy_id,status=excluded.status,browser_locale=excluded.browser_locale,
            browser_timezone=excluded.browser_timezone,browser_width=excluded.browser_width,browser_height=excluded.browser_height''',
            (identifier, values['name'], values.get('group_id'), values.get('proxy_id'),
             profile, values.get('status', 'unknown'), old.get('created_at', self.timestamp()),
             values.get('browser_locale'), values.get('browser_timezone'), values.get('browser_width'), values.get('browser_height')))
        return self.account(identifier)

    def proxy(self, identifier):
        row = self.require('proxies', identifier)
        row['password_status'] = '已设置' if self.secrets.get('proxy:' + identifier) else '未设置'
        return row

    def save_proxy(self, data, identifier=None):
        if identifier:
            self.require('proxies', identifier)
        identifier = identifier or uuid4().hex
        password = data.get('password')
        if password is not None:
            if hasattr(password, 'get_secret_value'):
                password = password.get_secret_value()
            if password:
                self.secrets.set('proxy:' + identifier, password)
            else:
                self.secrets.delete('proxy:' + identifier)
        self.db.execute('''INSERT INTO proxies(id,name,server,username) VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,server=excluded.server,username=excluded.username''',
            (identifier, data['name'], data['server'], data.get('username', '')))
        return self.proxy(identifier)

    def log(self, job_id, code, level='info'):
        self.db.execute('INSERT INTO publish_logs(id,job_id,ts,level,message) VALUES (?,?,?,?,?)',
            (uuid4().hex, job_id, self.timestamp(), level, MESSAGES.get(code, MESSAGES['failed'])))

    def job(self, identifier):
        row = self.require('publish_jobs', identifier)
        row['hashtags'] = json.loads(row['hashtags'])
        for name in ('ai_generated', 'allow_comment', 'allow_duet', 'allow_stitch'):
            row[name] = bool(row[name])
        row['logs'] = self.db.all('SELECT * FROM publish_logs WHERE job_id=? ORDER BY ts,id', (identifier,))
        return row

    def media(self, path):
        path = Path(path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError('需要存在的视频绝对路径')
        warnings = []
        if path.stat().st_size > 4 * 1024**3:
            warnings.append('size')
        try:
            meta = probe(path)
        except Exception:
            warnings.append('probe')
        else:
            if meta['type'] != 'video':
                raise ValueError('素材必须包含视频')
            if not 3 <= meta['duration'] <= 600:
                warnings.append('duration')
            if not meta['height'] or abs((meta['width'] or 0) / meta['height'] - 9 / 16) > .03:
                warnings.append('ratio')
        return warnings

    def create_jobs(self, data):
        ids = data.get('account_ids') or [data.get('account_id')]
        if not ids or None in ids or len(set(ids)) != len(ids):
            raise ValueError('请选择不重复的账号')
        accounts = [self.account(identifier) for identifier in ids]
        if any(a['status'] == 'disabled' for a in accounts):
            raise ValueError('账号已停用')
        path = data.get('video_path')
        if data.get('asset_id'):
            path = self.require('assets', data['asset_id'])['path']
        if not path:
            raise ValueError('请选择素材或视频路径')
        warnings = self.media(path)
        start = utc(data['scheduled_at'])
        created, jobs = self.timestamp(), []
        # Validate everything before the batch's single transaction; no partial batch.
        with self.db.connect() as con:
            for index, account in enumerate(accounts):
                identifier = uuid4().hex
                scheduled = start + timedelta(minutes=data.get('stagger_minutes', 0) * index)
                caption = data.get('caption', '').replace('{account_name}', account['name']).replace('{index}', str(index + 1))
                if len(caption) + sum(len(tag) + 2 for tag in data.get('hashtags', [])) > 2200:
                    raise ValueError('展开后的文案过长')
                jitter = self.randint(0, data.get('jitter_window_seconds', 0))
                con.execute('''INSERT INTO publish_jobs
                    (id,account_id,asset_id,video_path,caption,hashtags,scheduled_at,privacy,ai_generated,
                    allow_comment,allow_duet,allow_stitch,max_retries,jitter_seconds,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                    identifier, account['id'], data.get('asset_id'), str(Path(path).resolve()), caption,
                    json.dumps(data.get('hashtags', []), ensure_ascii=False), scheduled.isoformat(),
                    data.get('privacy', 'private'), data.get('ai_generated', False),
                    data.get('allow_comment', True), data.get('allow_duet', False), data.get('allow_stitch', False),
                    data.get('max_retries', self.settings.retries), jitter, created, created))
                jobs.append(identifier)
        for identifier in jobs:
            self.log(identifier, 'created')
            for warning in warnings:
                self.log(identifier, warning, 'warning')
        return [dict(self.job(identifier), warnings=[MESSAGES[w] for w in warnings]) for identifier in jobs]

    def action(self, identifier, action, scheduled_at=None, result_url=None):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM publish_jobs WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            if row['status'] == 'running':
                raise ValueError('发布已开始，不能取消或改写状态，请等待结果')
            stamp = self.timestamp()
            if action == 'cancel':
                if row['status'] not in ('scheduled', 'needs_attention'):
                    raise ValueError('只能取消等待或待人工处理的任务')
                con.execute("UPDATE publish_jobs SET status='cancelled',finished_at=?,updated_at=? WHERE id=?", (stamp, stamp, identifier))
            elif action in ('retry', 'reschedule'):
                if action == 'retry' and row['status'] not in ('failed', 'needs_attention', 'missed', 'cancelled'):
                    raise ValueError('当前任务不能重试')
                if action == 'reschedule' and row['status'] == 'succeeded':
                    raise ValueError('已完成任务不能改期')
                target = utc(scheduled_at or self.now()).isoformat()
                con.execute("""UPDATE publish_jobs SET status='scheduled',scheduled_at=?,attempts=0,
                    error=NULL,result_url=NULL,next_attempt_at=NULL,started_at=NULL,finished_at=NULL,updated_at=? WHERE id=?""", (target, stamp, identifier))
            elif action == 'complete':
                if row['status'] != 'needs_attention':
                    raise ValueError('只能人工确认待处理任务')
                con.execute("UPDATE publish_jobs SET status='succeeded',error=NULL,result_url=?,finished_at=?,updated_at=? WHERE id=?",
                    (safe_result_url(result_url), stamp, stamp, identifier))
            else:
                raise ValueError('不支持的操作')
        self.log(identifier, action)
        return self.job(identifier)
