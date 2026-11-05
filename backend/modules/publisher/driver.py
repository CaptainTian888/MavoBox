from abc import ABC, abstractmethod
from collections import deque
from threading import Lock


class LoginRequired(Exception):
    pass


class NeedsAttention(Exception):
    pass


class Transient(Exception):
    pass


class PublisherDriver(ABC):
    @abstractmethod
    def check_login(self, account): ...

    @abstractmethod
    def open_login(self, account): ...

    @abstractmethod
    def publish(self, account, job): ...


class FakeDriver(PublisherDriver):
    """Offline driver. Never opens a browser or contacts TikTok."""
    def __init__(self, errors=(), logged_in=True):
        self.errors = deque(errors)
        self.logged_in = logged_in
        self.published = []
        self.lock = Lock()

    def check_login(self, account):
        return self.logged_in

    def open_login(self, account):
        return self.logged_in

    def publish(self, account, job):
        with self.lock:
            if self.errors:
                raise self.errors.popleft()
            self.published.append((account['id'], job['id']))
        return None
