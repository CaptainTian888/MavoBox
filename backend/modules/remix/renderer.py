import json
import queue
import subprocess
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from core.ffmpeg import locate
from core.queue import Cancelled
from .planner import duration


def default_font():
    """First CJK-capable system font that exists (Windows YaHei, macOS PingFang/STHeiti, Linux Noto)."""
    for candidate in (r'C:\Windows\Fonts\msyh.ttc', '/System/Library/Fonts/PingFang.ttc',
                      '/System/Library/Fonts/STHeiti Light.ttc', '/Library/Fonts/Arial Unicode.ttf',
                      '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'):
        if Path(candidate).is_file():
            return candidate
    return None


def run_ffmpeg(args, total=1, progress=lambda _: None, cancelled=lambda: False, cwd=None):
    if cancelled():
        raise Cancelled()
    # Drain progress on a separate thread so a silent encoder is still cancellable.
    lines = queue.Queue()
    with subprocess.Popen([locate('ffmpeg'), '-nostdin', '-hide_banner', '-v', 'error', '-y',
            '-progress', 'pipe:1', '-nostats', *args], cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace') as process:
        def read():
            for line in process.stdout:
                lines.put(line)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while True:
                if cancelled():
                    raise Cancelled()
                try:
                    line = lines.get(timeout=0.1)
                    if line.startswith('out_time_us='):
                        try:
                            progress(min(99, float(line.split('=', 1)[1]) / 1_000_000 / max(total, .001) * 100))
                        except ValueError:
                            pass
                except queue.Empty:
                    if process.poll() is not None:
                        break
            if process.wait():
                raise ValueError('视频渲染失败，请检查媒体、字幕字体和 ffmpeg')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)
            process.stdout.close()


def has_audio(path):
    result = subprocess.run([locate('ffprobe'), '-v', 'error', '-select_streams', 'a',
        '-show_entries', 'stream=index', '-of', 'json', str(path)], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('无法读取音轨')
    return bool(json.loads(result.stdout)['streams'])


LOUDNESS_TARGET = -14.0   # LUFS, short-video delivery level; pass loudness=None to render() to skip normalisation


def render(timeline, assets, target, progress=lambda _: None, cancelled=lambda: False,
           fit='crop', font_path='', loudness=LOUDNESS_TARGET):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height, fps = timeline['width'], timeline['height'], timeline['fps']
    clips = timeline['clips']
    try:
        with TemporaryDirectory(dir=target.parent) as directory:
            work = Path(directory)
            lengths = []
            for i, clip in enumerate(clips):
                source = Path(assets[clip['asset_id']]['path']).resolve()
                if not source.is_file():
                    raise ValueError('素材文件不存在')
                length = (clip['src_end'] - clip['src_start']) / clip['speed']
                lengths.append(length)
                args = ['-ss', str(clip['src_start']), '-t', str(clip['src_end'] - clip['src_start']), '-i', str(source)]
                audio = has_audio(source) and not clip['mute']
                if not audio:
                    args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
                prefix = f'[0:v]setpts=(PTS-STARTPTS)/{clip["speed"]},fps={fps},setsar=1'
                if fit == 'blur':
                    vf = prefix + f',split[fg][bg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=20[blur];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[front];[blur][front]overlay=(W-w)/2:(H-h)/2'
                else:
                    vf = prefix + f',scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}'
                vf += ',format=yuv420p'
                cap = clip['caption']
                if cap:
                    # Text lives in a generated file: it can never introduce filter options.
                    name = f'caption{i}.txt'
                    (work / name).write_text(cap['text'], encoding='utf-8')
                    style = timeline['global_caption_style']
                    font = style['font'] or font_path or default_font()
                    font_option = ''
                    if font and Path(font).is_file():
                        # Copy to an internally named path; no user path enters drawtext.
                        import shutil
                        font_name = f'font{i}.ttf'
                        shutil.copyfile(font, work / font_name)
                        font_option = f":fontfile='{font_name}'"
                    y = {'top': 'h*0.1', 'center': '(h-text_h)/2', 'bottom': 'h*0.85-text_h'}[cap['position']]
                    vf += (f",drawtext=textfile='{name}':expansion=none{font_option}:fontsize={style['size']}"
                           f":fontcolor={style['color']}:borderw={style['outline']}:bordercolor=black:x=(w-text_w)/2:y={y}"
                           f":enable='between(t,{cap['start_offset']},{cap['end_offset']})'")
                trans = clip['transition_in']
                if trans['type'] == 'fade':
                    vf += f",fade=t=in:st=0:d={trans['duration']}"
                vf += '[v]'
                audio_input = '[0:a]' if audio else '[1:a]'
                speed = clip['speed'] if audio else 1.
                # atempo supports .5..2 on older ffmpeg builds.
                tempos = []
                while speed < .5:
                    tempos.append('atempo=0.5'); speed /= .5
                while speed > 2:
                    tempos.append('atempo=2'); speed /= 2
                tempos.append(f'atempo={speed}')
                af = audio_input + ','.join(tempos) + f',aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length},asetpts=PTS-STARTPTS[a]'
                args += ['-filter_complex', vf + ';' + af, '-map', '[v]', '-map', '[a]', '-t', str(length),
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-video_track_timescale', '90000', str(work / f'{i}.mp4')]
                run_ffmpeg(args, length, lambda p: progress((i + p / 100) / len(clips) * 65), cancelled, work)
            args = []
            for i in range(len(clips)):
                args += ['-i', str(work / f'{i}.mp4')]
            filters = []
            for i in range(len(clips)):
                filters += [f'[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]', f'[{i}:a]asetpts=PTS-STARTPTS[a{i}]']
            video, audio, elapsed = 'v0', 'a0', lengths[0]
            for i in range(1, len(clips)):
                trans = clips[i]['transition_in']
                vn, an = f'joinedv{i}', f'joineda{i}'
                if trans['type'] in ('crossfade', 'slide'):
                    d = trans['duration']
                    effect = 'fade' if trans['type'] == 'crossfade' else 'slideleft'
                    filters += [f'[{video}][v{i}]xfade=transition={effect}:duration={d}:offset={elapsed-d}[{vn}]',
                                f'[{audio}][a{i}]acrossfade=d={d}:c1=tri:c2=tri[{an}]']
                    elapsed -= d
                else:
                    filters += [f'[{video}][{audio}][v{i}][a{i}]concat=n=2:v=1:a=1[{vn}][{an}]']
                elapsed += lengths[i]
                video, audio = vn, an
            bgm = timeline['bgm']
            if bgm:
                path = Path(bgm['path'])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError('BGM 文件不存在')
                args += ['-stream_loop', '-1', '-i', str(path.resolve())]
                d = min(bgm['fade_out'], elapsed)
                filters += [f'[{len(clips)}:a]aresample=48000,volume={bgm["volume"]},atrim=duration={elapsed},afade=t=out:st={elapsed-d}:d={max(d, .001)}[music]',
                    f'[{audio}]volume=0.6[original]', '[original][music]amix=inputs=2:duration=first:normalize=0[mixed]']
                audio = 'mixed'
            if loudness is not None:
                filters.append(f'[{audio}]loudnorm=I={loudness}:TP=-1.5:LRA=11,aresample=48000[final]')
                audio = 'final'
            args += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', f'[{video}]', '-map', f'[{audio}]',
                '-t', str(duration(timeline)), '-r', str(fps), '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-movflags', '+faststart', str(target)]
            run_ffmpeg(args, elapsed, lambda p: progress(65 + p * .34), cancelled, work)
            if cancelled():
                raise Cancelled()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
