import subprocess

import pytest
from core.ffmpeg import locate
from modules import qa


def make(tmp_path, name, args):
    try:
        ffmpeg = locate('ffmpeg')
    except RuntimeError:
        pytest.skip('ffmpeg unavailable')
    path = tmp_path / name
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', *args, str(path)], check=True, capture_output=True)
    return path


def test_parse_filter_log_extracts_all_measures():
    text = '''
[blackdetect @ 0x1] black_start:0 black_end:1.2 black_duration:1.2
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 3.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 2.0
[silencedetect @ 0x3] silence_start: 5.1
[silencedetect @ 0x3] silence_end: 8.2 | silence_duration: 3.1
  Integrated loudness:
    I:         -16.3 LUFS
  True peak:
    Peak:        -0.4 dBFS
'''
    m = qa.parse_filter_log(text)
    assert m['black'] == [(0.0, 1.2)] and m['freeze'] == [(3.5, 2.0)] and m['silence'] == [(5.1, 3.1)]
    assert m['lufs'] == -16.3 and m['true_peak'] == -0.4


def info(**kw):
    base = dict(duration=20.0, width=1080, height=1920, fps=30.0, has_audio=True)
    base.update(kw)
    return base


def codes(findings):
    return {f['code'] for f in findings}


def test_evaluate_clean_video_has_no_findings():
    assert qa.evaluate(info(), dict(black=[], freeze=[], silence=[], lufs=-14.5, true_peak=-2.0), 20.0) == []


def test_evaluate_flags_each_problem():
    measures = dict(black=[(0.0, 1.0)], freeze=[(5.0, 2.0)], silence=[(8.0, 3.0)], lufs=-30.0, true_peak=0.5)
    found = codes(qa.evaluate(info(width=1920, height=1080, fps=15, duration=2.0), measures, 20.0))
    assert {'too_short', 'duration_mismatch', 'aspect', 'low_fps', 'black', 'freeze', 'silence', 'quiet', 'clipping'} <= found
    assert 'no_audio' in codes(qa.evaluate(info(has_audio=False), {}, None))
    assert codes(qa.evaluate(info(duration=0, width=None), {}, None)) == {'unreadable'}
    assert 'loud' in codes(qa.evaluate(info(), dict(lufs=-5.0, true_peak=-3.0), None))


def test_real_video_inspection_and_contact_sheet(tmp_path):
    video = make(tmp_path, 'v.mp4', ['-f', 'lavfi', '-i', 'testsrc=size=270x480:rate=25',
                                      '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '4',
                                      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest'])
    sheet = tmp_path / 'sheet.jpg'
    report = qa.inspect(video, sheet, target_seconds=4)
    assert report['info']['has_audio'] and report['info']['width'] == 270
    assert report['info']['lufs'] is not None          # loudness really measured
    assert report['sheet'] and sheet.stat().st_size > 1000
    assert 'aspect' not in codes(report['findings'])   # 270x480 is 9:16


def test_black_and_silent_clip_is_reported(tmp_path):
    video = make(tmp_path, 'b.mp4', ['-f', 'lavfi', '-i', 'color=c=black:size=270x480:rate=25',
                                      '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '4',
                                      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest'])
    found = codes(qa.inspect(video, None)['findings'])
    assert 'black' in found and 'silence' in found and 'freeze' in found


def test_api_check_and_sheet_route(client, tmp_path):
    video = make(tmp_path, 'api.mp4', ['-f', 'lavfi', '-i', 'testsrc=size=270x480:rate=25', '-t', '3',
                                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p'])
    asset = client.post('/api/assets', json={'path': str(video)}).json()
    report = client.post(f"/api/qa/{asset['id']}", json={'target_seconds': 3}).json()
    assert report['status'] in ('ok', 'warn') and report['info']['has_audio'] is False
    assert 'no_audio' in codes(report['findings'])
    sheet = client.get(report['sheet_url'])
    assert sheet.status_code == 200 and sheet.headers['content-type'] == 'image/jpeg'
    assert client.post('/api/qa/' + 'a' * 32).status_code == 404
    assert client.post('/api/qa/../../etc').status_code in (404, 405)
    assert client.get('/api/qa/sheet/..%2f..%2fsecret.jpg').status_code == 404


def test_renderer_brings_loudness_to_target(tmp_path):
    from modules.remix.planner import rule_plan
    from modules.remix.renderer import render
    quiet = make(tmp_path, 'quiet.mp4', ['-f', 'lavfi', '-i', 'testsrc=size=160x284:rate=25',
                                          '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000,volume=0.05',
                                          '-t', '6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest'])
    items = [dict(id='a', asset_id='a', start=0., end=6., quality=1)]
    timeline = rule_plan(items, 6, 160, 284)
    raw = tmp_path / 'raw.mp4'
    out = tmp_path / 'norm.mp4'
    render(timeline, {'a': {'path': str(quiet)}}, raw, loudness=None)
    render(timeline, {'a': {'path': str(quiet)}}, out)
    before = qa.inspect(raw)['info']['lufs']
    after = qa.inspect(out)['info']['lufs']
    assert before < -30                                   # the source really is quiet
    assert abs(after - qa.TARGET_LUFS) < 3                # normalised close to -14 LUFS
    assert qa.inspect(out)['info']['true_peak'] <= -0.5   # and without clipping
