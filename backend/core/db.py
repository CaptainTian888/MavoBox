import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = path
        with self.connect() as con:
            con.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS account_groups (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proxies (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, server TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, group_id TEXT, proxy_id TEXT,
                    profile_dir TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',
                    last_checked_at TEXT, last_error TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, asset_id TEXT, video_path TEXT NOT NULL,
                    caption TEXT NOT NULL, hashtags TEXT NOT NULL DEFAULT '[]', scheduled_at TEXT NOT NULL,
                    privacy TEXT NOT NULL DEFAULT 'private', ai_generated INTEGER NOT NULL DEFAULT 0,
                    allow_comment INTEGER NOT NULL DEFAULT 1, allow_duet INTEGER NOT NULL DEFAULT 0,
                    allow_stitch INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'scheduled',
                    attempts INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 1,
                    error TEXT, result_url TEXT, jitter_seconds INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    next_attempt_at TEXT);
                CREATE INDEX IF NOT EXISTS publish_due ON publish_jobs(status, scheduled_at);
                CREATE INDEX IF NOT EXISTS publish_account ON publish_jobs(account_id, finished_at);
                CREATE TABLE IF NOT EXISTS publish_logs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ts TEXT NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, type TEXT NOT NULL,
                    duration REAL, width INTEGER, height INTEGER, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                    base_url TEXT NOT NULL, model TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL, created_at TEXT NOT NULL);
            ''')
            # Additive, repeatable migration for optional per-account context settings.
            columns = {row['name'] for row in con.execute('PRAGMA table_info(accounts)')}
            for name, kind in (('browser_locale', 'TEXT'), ('browser_timezone', 'TEXT'),
                               ('browser_width', 'INTEGER'), ('browser_height', 'INTEGER')):
                if name not in columns:
                    con.execute(f'ALTER TABLE accounts ADD COLUMN {name} {kind}')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def execute(self, sql, params=()):
        with self.connect() as con:
            con.execute(sql, params)

    def all(self, sql, params=()):
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def add_asset(self, path, metadata):
        asset_id = uuid4().hex
        self.execute("""INSERT INTO assets
            (id, path, type, duration, width, height, created_at)
            VALUES (?,?,?,?,?,?,?)""", (
            asset_id, str(path), metadata['type'], metadata['duration'],
            metadata['width'], metadata['height'], now()))
        return self.one("SELECT * FROM assets WHERE id=?", (asset_id,))
