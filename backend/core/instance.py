"""Single-instance guard: only one backend may own a data directory at a time.

Two backends on one SQLite database / Chromium profile would mark each other's running
jobs as failed and corrupt state. The holder keeps an exclusive lock on byte 0 of
`instance.lock` for its whole lifetime (the OS releases it even if the process is killed)
and publishes its URL after offset 16 so a second launch can point at the running one.
"""
import msvcrt
from pathlib import Path

OFFSET = 16


class InstanceLock:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'instance.lock'
        self.file = None

    def acquire(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, 'a+b')
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self.file = handle
        return True

    def publish(self, url):
        self.file.seek(OFFSET)
        self.file.truncate(OFFSET)
        self.file.write(url.encode('ascii'))
        self.file.flush()

    def running_url(self):
        """URL published by the current holder, or None."""
        try:
            with open(self.path, 'rb') as handle:
                handle.seek(OFFSET)  # byte 0 is locked by the holder; read past it
                data = handle.read(256)
        except OSError:
            return None
        url = data.decode('ascii', 'ignore').strip('\x00 \r\n')
        return url if url.startswith('http://127.0.0.1:') else None

    def release(self):
        if self.file:
            try:
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                self.file.close()
                self.file = None
