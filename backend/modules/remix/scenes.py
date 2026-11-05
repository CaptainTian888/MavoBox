import hashlib
import json
import math
from pathlib import Path
from threading import RLock
from uuid import uuid4
from core.ffmpeg import locate
from core.queue import Cancelled


def params_hash(threshold=27., segment_seconds=4.):
    return hashlib.sha256(json.dumps([1, threshold, segment_seconds]).encode()).hexdigest()


def detect_scenes(path, threshold=27.):
    from scenedetect import detect, ContentDetector
    return [(a.get_seconds(), b.get_seconds()) for a, b in
            detect(str(path), ContentDetector(threshold=threshold))]


def split(duration, segment_seconds=4.):
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(segment_seconds) or segment_seconds <= 0:
        raise ValueError('视频时长或均分时长无效')
    return [(i * segment_seconds, min(duration, (i+1) * segment_seconds))
            for i in range(math.ceil(duration / segment_seconds))]


def extract_frame(source, target, seconds, cancelled=lambda: False):
    from .renderer import run_ffmpeg
    run_ffmpeg(['-ss', str(seconds), '-i', str(source), '-frames:v', '1',
        '-vf', 'scale=512:-2', '-q:v', '3', str(target)], cancelled=cancelled)


class SceneAnalyzer:
    def __init__(self, db, data_dir, detector=detect_scenes, frame_extractor=extract_frame):
        self.db, self.detector, self.frame_extractor = db, detector, frame_extractor
        self.lock = RLock()
        self.frames = Path(data_dir) / 'frames'
        self.frames.mkdir(exist_ok=True)

    def analyze(self, asset, threshold=27., segment_seconds=4., cancelled=lambda: False):
        with self.lock:
            return self._analyze(asset, threshold, segment_seconds, cancelled)

    def _analyze(self, asset, threshold, segment_seconds, cancelled):
        key = params_hash(threshold, segment_seconds)
        cached = self.db.all('SELECT * FROM shots WHERE asset_id=? AND params_hash=? ORDER BY start', (asset['id'], key))
        if cached:
            # Restore missing JPEGs in place, preserving IDs used by saved timelines.
            for shot in cached:
                if not Path(shot['frame_path']).is_file():
                    self.frame_extractor(asset['path'], Path(shot['frame_path']),
                                         (shot['start'] + shot['end']) / 2, cancelled)
            return cached
        if cancelled():
            raise Cancelled()
        try:
            ranges = self.detector(asset['path'], threshold)
            if len(ranges) < 2:
                ranges = split(asset['duration'], segment_seconds)
        except Exception:
            ranges = split(asset['duration'], segment_seconds)
        if len(ranges) > 2000:
            raise ValueError('镜头数量过多，请增大均分时长')
        rows, created = [], []
        try:
            for start, end in ranges:
                if cancelled():
                    raise Cancelled()
                if not (0 <= start < end <= asset['duration'] + 0.1):
                    raise ValueError('切分结果无效')
                end = min(end, asset['duration'])
                shot_id = uuid4().hex
                target = self.frames / (shot_id + '.jpg')
                created.append(target)
                self.frame_extractor(asset['path'], target, (start + end) / 2, cancelled)
                rows.append((shot_id, asset['id'], start, end, str(target.resolve()), key))
            with self.db.connect() as con:
                con.executemany('INSERT INTO shots (id,asset_id,start,end,frame_path,params_hash) VALUES (?,?,?,?,?,?)', rows)
        except BaseException:
            for target in created:
                target.unlink(missing_ok=True)
            raise
        return self.db.all('SELECT * FROM shots WHERE asset_id=? AND params_hash=? ORDER BY start', (asset['id'], key))
