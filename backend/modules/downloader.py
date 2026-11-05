from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import yt_dlp
from core.ffmpeg import locate, probe, trim
from core.queue import Cancelled


class SilentLogger:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


class Downloader:
    def __init__(self, db, data_dir):
        self.db = db
        self.directory = data_dir / 'media'
        self.directory.mkdir(exist_ok=True)

    def __call__(self, payload, progress, cancelled):
        def hook(data):
            if cancelled():
                raise Cancelled()
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            if total:
                progress(min(75, data.get('downloaded_bytes', 0) / total * 75))

        target = self.directory / f'{uuid4().hex}.mp4'
        try:
            with TemporaryDirectory(dir=self.directory) as directory:
                options = dict(outtmpl=str(Path(directory) / 'source.%(ext)s'),
                    format='best[ext=mp4]/best', noplaylist=True, playlist_items='1', max_downloads=1, quiet=True, no_warnings=True,
                    logger=SilentLogger(), progress_hooks=[hook], socket_timeout=20,
                    retries=0, ffmpeg_location=locate('ffmpeg'))
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(payload['url'], download=True)
                    source = Path(ydl.prepare_filename(info))
                progress(80)
                trim(source, target, payload['seconds'], cancelled)
                progress(95)
                metadata = probe(target)
                if cancelled():
                    raise Cancelled()
                return self.db.add_asset(target.resolve(), metadata)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
