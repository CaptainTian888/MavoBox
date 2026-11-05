from core.instance import InstanceLock


def test_second_holder_is_refused_and_sees_url(tmp_path):
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert first.acquire()
    first.publish('http://127.0.0.1:8123')
    assert second.acquire() is False
    assert second.running_url() == 'http://127.0.0.1:8123'
    first.release()
    assert second.acquire()
    second.release()


def test_garbage_url_is_ignored(tmp_path):
    lock = InstanceLock(tmp_path)
    assert lock.acquire()
    lock.publish('http://evil.test')
    assert lock.running_url() is None
    lock.release()
