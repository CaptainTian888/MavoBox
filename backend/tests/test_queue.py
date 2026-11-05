from threading import Event, Lock
import time
from core.db import Database
from core.queue import TaskQueue


def wait_for(queue, task_id, status):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = queue.get(task_id)
        if task['status'] == status:
            return task
        time.sleep(.01)
    raise AssertionError(f'Expected {status}, got {queue.get(task_id)}')


def test_automatic_and_manual_retry(tmp_path):
    calls = []
    def handler(payload, progress, cancelled):
        calls.append(1)
        progress(30)
        if len(calls) <= 2:
            raise RuntimeError('Do not persist raw exceptions')
    queue = TaskQueue(Database(tmp_path / 'db'), {'test': handler}, retries=1)
    try:
        task = queue.create('test', {})
        failed = wait_for(queue, task['id'], 'failed')
        assert failed['attempts'] == 2
        assert 'raw exceptions' not in failed['error']
        queue.retry(task['id'])
        assert wait_for(queue, task['id'], 'succeeded')['progress'] == 100
    finally:
        queue.close()


def test_concurrency_cancel_pending_and_running(tmp_path):
    release = Event()
    lock = Lock()
    counts = {'active': 0, 'peak': 0}
    def handler(payload, progress, cancelled):
        with lock:
            counts['active'] += 1
            counts['peak'] = max(counts['peak'], counts['active'])
        try:
            while not release.wait(.01):
                progress(10)
        finally:
            with lock:
                counts['active'] -= 1
    queue = TaskQueue(Database(tmp_path / 'db'), {'test': handler}, concurrency=2)
    try:
        first = queue.create('test', {})['id']
        second = queue.create('test', {})['id']
        wait_for(queue, first, 'running')
        wait_for(queue, second, 'running')
        pending = queue.create('test', {})['id']
        assert queue.get(pending)['status'] == 'pending'
        queue.cancel(pending)
        queue.cancel(first)
        release.set()
        wait_for(queue, second, 'succeeded')
        assert queue.get(first)['status'] == 'cancelled'
        assert queue.get(pending)['status'] == 'cancelled'
        assert counts['peak'] <= 2
    finally:
        release.set()
        queue.close()


def test_restart_marks_running_failed(tmp_path):
    db = Database(tmp_path / 'db')
    db.execute("INSERT INTO tasks VALUES ('old','test','{}','running',40,NULL,1,0,'today')")
    db.execute("INSERT INTO tasks VALUES ('pending','test','{}','pending',0,NULL,0,0,'today')")
    queue = TaskQueue(db, {'test': lambda *args: None})
    try:
        assert queue.get('old')['status'] == 'failed'
        wait_for(queue, 'pending', 'succeeded')
    finally:
        queue.close()
