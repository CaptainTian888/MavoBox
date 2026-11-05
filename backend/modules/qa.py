"""Automatic quality check of a finished video (idea borrowed from OpenFilm's `look` command: verify
what was made instead of trusting it). Everything is measured with ffmpeg: format, black / frozen
frames, silence, loudness and peaks, plus a contact sheet of evenly spaced frames to eyeball."""
import re
import subprocess
from pathlib import Path

from core.ffmpeg import locate, probe

TARGET_LUFS = -14.0          # TikTok / short-video delivery target
PEAK_LIMIT_DB = -1.0         # true peaks above this risk clipping after platform re-encoding


def _run(args, timeout):
    return subprocess.run([locate('ffmpeg'), '-nostdin', '-hide_banner', *args], capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=timeout)


def parse_filter_log(text: str) -> dict:
    """Parse blackdetect / freezedetect / silencedetect / ebur128 output from ffmpeg's stderr."""
    black = [(float(a), float(b)) for a, b in re.findall(r'black_start:([\d.]+)\s+black_end:[\d.]+\s+black_duration:([\d.]+)', text)]
    freeze_starts = [float(x) for x in re.findall(r'freeze_start:\s*([\d.]+)', text)]
    freeze_durs = [float(x) for x in re.findall(r'freeze_duration:\s*([\d.]+)', text)]
    # A clip that stays frozen until its end only reports freeze_start (no duration): keep it with None.
    freeze = [(start, freeze_durs[i] if i < len(freeze_durs) else None) for i, start in enumerate(freeze_starts)]
    silence_starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[\d.]+)', text)]
    silence_durs = [float(x) for x in re.findall(r'silence_duration:\s*([\d.]+)', text)]
    silence = list(zip(silence_starts, silence_durs))
    loud = re.findall(r'\bI:\s+(-?\d+(?:\.\d+)?)\s+LUFS', text)
    peak = re.findall(r'\bPeak:\s+(-?\d+(?:\.\d+)?)\s+dBFS', text)
    return dict(black=black, freeze=freeze, silence=silence,
                lufs=float(loud[-1]) if loud else None, true_peak=float(peak[-1]) if peak else None)


def evaluate(info: dict, measures: dict, target_seconds=None) -> list:
    """Turn measurements into findings: [{level, code, message, at?}]. Pure function, easy to test."""
    findings = []

    def add(level, code, message, at=None):
        findings.append(dict(level=level, code=code, message=message, **({'at': at} if at is not None else {})))

    duration, width, height = info.get('duration') or 0, info.get('width') or 0, info.get('height') or 0
    if duration <= 0 or not width:
        add('error', 'unreadable', '无法读取视频内容，文件可能已损坏')
        return findings
    if duration < 3:
        add('warn', 'too_short', f'时长只有 {duration:.1f} 秒，TikTok 建议至少 3 秒')
    if target_seconds and abs(duration - target_seconds) > max(0.5, target_seconds * 0.05):
        add('warn', 'duration_mismatch', f'成片 {duration:.1f} 秒，与目标 {target_seconds:.1f} 秒相差较大')
    if height and abs(width / height - 9 / 16) > 0.01:
        add('warn', 'aspect', f'画幅是 {width}×{height}，不是竖屏 9:16，发布后可能出现黑边或被裁切')
    fps = info.get('fps') or 0
    if fps and fps < 24:
        add('warn', 'low_fps', f'帧率只有 {fps:.0f}，画面可能不流畅')
    if not info.get('has_audio'):
        add('warn', 'no_audio', '成片没有音轨')
    for start, length in measures.get('black', []):
        where = '开头' if start < 0.2 else ('结尾' if start + length >= duration - 0.2 else f'{start:.1f}s 处')
        add('warn', 'black', f'{where}有 {length:.1f} 秒黑屏', at=round(start, 2))
    for start, length in measures.get('freeze', []):
        length = duration - start if length is None else length
        add('warn', 'freeze', f'{start:.1f}s 起画面静止 {length:.1f} 秒', at=round(start, 2))
    silent_total = sum(length for _s, length in measures.get('silence', []))
    if info.get('has_audio') and duration and silent_total / duration > 0.3:
        add('warn', 'silence', f'静音累计 {silent_total:.1f} 秒（占 {silent_total / duration:.0%}）')
    elif info.get('has_audio'):
        for start, length in measures.get('silence', []):
            if length >= 2.5:
                add('warn', 'silence', f'{start:.1f}s 起静音 {length:.1f} 秒', at=round(start, 2))
    lufs, peak = measures.get('lufs'), measures.get('true_peak')
    if lufs is not None and info.get('has_audio'):
        if lufs < TARGET_LUFS - 6:
            add('warn', 'quiet', f'整体响度 {lufs:.1f} LUFS，明显偏小（短视频常用约 {TARGET_LUFS:.0f}）')
        elif lufs > TARGET_LUFS + 5:
            add('warn', 'loud', f'整体响度 {lufs:.1f} LUFS，偏大，平台可能压低或失真')
        if peak is not None and peak > PEAK_LIMIT_DB:
            add('warn', 'clipping', f'声音峰值 {peak:.1f} dBFS，可能爆音（建议低于 {PEAK_LIMIT_DB:.0f}）')
    return findings


def contact_sheet(video: Path, out: Path, duration: float, columns=6, rows=2, tile_width=160):
    count = columns * rows
    fps = count / max(duration, 0.1)
    vf = f'fps={fps:.6f},scale={tile_width}:-2,tile={columns}x{rows}:padding=4:color=black'
    out.parent.mkdir(parents=True, exist_ok=True)
    result = _run(['-y', '-i', str(video), '-vf', vf, '-frames:v', '1', '-q:v', '4', str(out)], timeout=120)
    if result.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        return False
    return True


def inspect(video, sheet_path=None, target_seconds=None) -> dict:
    video = Path(video)
    info = probe(video)
    info['has_audio'] = bool(info.get('has_audio', False))
    measures = {}
    if info.get('duration', 0) > 0 and info.get('width'):
        vf = 'blackdetect=d=0.4:pix_th=0.10,freezedetect=n=0.003:d=1.5'
        args = ['-i', str(video), '-vf', vf]
        if info['has_audio']:
            args += ['-af', 'silencedetect=n=-45dB:d=1.5,ebur128=peak=true:framelog=quiet']
        else:
            args += ['-an']
        args += ['-f', 'null', '-']
        measures = parse_filter_log(_run(args, timeout=max(60, int(info['duration'] * 4))).stderr)
    findings = evaluate(info, measures, target_seconds)
    sheet_ok = bool(sheet_path) and info.get('duration', 0) > 0 and contact_sheet(video, Path(sheet_path), info['duration'])
    worst = 'error' if any(f['level'] == 'error' for f in findings) else ('warn' if findings else 'ok')
    return dict(status=worst, findings=findings, sheet=bool(sheet_ok),
                info=dict(duration=round(info.get('duration', 0), 2), width=info.get('width'), height=info.get('height'),
                          fps=round(info.get('fps') or 0, 1), has_audio=info['has_audio'],
                          lufs=measures.get('lufs'), true_peak=measures.get('true_peak'),
                          size_mb=round(video.stat().st_size / 1_048_576, 1) if video.is_file() else None))
