"""File logging with secret scrubbing, so students can send a log without leaking keys."""
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    (re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{6,}'), 'Bearer ***'),
    (re.compile(r'(?i)\bKey\s+[A-Za-z0-9._~+/=-]{16,}'), 'Key ***'),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{8,}'), 'sk-***'),
    (re.compile(r'\bMAVO1\.[A-Za-z0-9_.-]{20,}'), 'MAVO1.***'),
    (re.compile(r'(?i)(["\']?(?:api[_-]?key|token|password|secret|authorization)["\']?\s*[:=]\s*["\']?)[^\s"\',}]+'), r'\1***'),
    (re.compile(r'(://)[^/\s:@]+:[^/\s@]+@'), r'\1***:***@'),
    (re.compile(r'(https?://[^\s?"\']+)\?[^\s"\']+'), r'\1?***'),  # signed-URL query strings
]


def scrub(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class ScrubFormatter(logging.Formatter):
    def format(self, record):
        return scrub(super().format(record))


def log_dir(data_dir) -> Path:
    return Path(data_dir) / 'logs'


def setup(data_dir, level=logging.INFO):
    """Log to <data_dir>/logs/mavobox.log (1 MB x 3 files). Safe to call more than once."""
    directory = log_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(getattr(h, '_mavobox', False) for h in root.handlers):
        return directory / 'mavobox.log'
    handler = RotatingFileHandler(directory / 'mavobox.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(ScrubFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    handler._mavobox = True
    root.addHandler(handler)
    root.setLevel(level)
    return directory / 'mavobox.log'


def tail(data_dir, lines=40) -> list[str]:
    path = log_dir(data_dir) / 'mavobox.log'
    try:
        with path.open('rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 64_000))
            data = handle.read().decode('utf-8', 'replace')
    except OSError:
        return []
    return [scrub(line) for line in data.splitlines()[-lines:]]
