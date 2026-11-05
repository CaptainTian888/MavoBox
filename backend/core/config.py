import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def default_data_dir():
    """Windows: %LOCALAPPDATA%/TikTolTool; macOS: ~/Library/Application Support/TikTolTool; else ~/.local/share."""
    if os.environ.get("TIKTOL_DATA_DIR"):
        return Path(os.environ["TIKTOL_DATA_DIR"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TikTolTool"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TikTolTool"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "TikTolTool"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: default_data_dir())
    concurrency: int = 2
    license_required: bool = False  # the packaged launcher turns this on
    retries: int = 1
    publish_concurrency: int = 2
    publish_poll_seconds: float = 15
    publish_min_interval_seconds: int = 1800
    publish_missed_grace_seconds: int = 600
    publish_headless: bool = True
    browser_locale: str = 'en-US'
    browser_timezone: str = 'UTC'
    browser_width: int = 1280
    browser_height: int = 900
    browser_timeout_ms: int = 30000
    upload_timeout_ms: int = 180000
    login_timeout_seconds: int = 600

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        if self.concurrency < 1 or self.retries < 0:
            raise ValueError("Invalid queue settings")
        if (self.publish_concurrency < 1 or self.publish_poll_seconds <= 0
                or self.publish_min_interval_seconds < 0 or self.publish_missed_grace_seconds < 0
                or self.browser_timeout_ms <= 0 or self.upload_timeout_ms <= 0
                or self.login_timeout_seconds <= 0 or self.browser_width < 1 or self.browser_height < 1):
            raise ValueError('Invalid publisher settings')
        self.data_dir.mkdir(parents=True, exist_ok=True)
