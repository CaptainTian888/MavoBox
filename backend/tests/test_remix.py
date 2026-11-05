import json
import pytest
import subprocess
import time
from pathlib import Path
from fastapi.testclient import TestClient
from api.app import create_app
from core.config import Settings
from core.db import Database
from core.ffmpeg import locate, probe
from core.queue import Cancelled
from core.secrets import MemorySecrets
from modules.remix.scenes import SceneAnalyzer, split, detect_scenes
from modules.remix.renderer import render
from modules.remix.tagging import LLMClient
import httpx

from modules.remix.planner import validate_timeline, rule_plan, plan
from modules.remix.tagging import parse_json, tag_shots


def shots():
    return [dict(id=str(i), asset_id='a', start=i * 3., end=(i + 1) * 3.,
                 quality=10-i, frame_path='', tagged=False) for i in range(3)]


def test_rules_and_invalid_timeline():
    timeline = rule_plan(shots(), 6, 160, 284)
    assert timeline['fallback'] is True
    validate_timeline(timeline, shots(), 6)
    for field, value in [('shot_id', 'unknown'), ('src_start', -1), ('speed', True),
                         ('src_end', 100), ('asset_id', 'wrong')]:
        bad = json.loads(json.dumps(timeline))
        bad['clips'][0][field] = value
        with pytest.raises(ValueError):
            validate_timeline(bad, shots(), 6)
    with pytest.raises(ValueError):
        validate_timeline(dict(timeline, clips=timeline['clips'] * 2), shots(), 12)


def test_json_and_planner_retry():
    assert parse_json('```json\n{"a":1}\n```') == {'a': 1}
    calls = []
    def client(messages):
        calls.append(messages)
        return 'not json'
    result = plan(client, shots(), '剪辑', 6, width=160, height=284)
    assert len(calls) == 3 and result['fallback']


def test_tagging_failure_and_retry(tmp_path):
    frame = tmp_path / 'a.jpg'
    frame.write_bytes(b'frame')
    items = [dict(shots()[0], frame_path=str(frame))]
    calls = []
    def client(messages):
        calls.append(messages)
        if len(calls) == 1:
            return 'bad'
        return json.dumps([dict(shot_id='0', description='场景', tags=['风景'],
            shot_type='wide', has_person=False, quality_score=8, suggested_use='开场')])
    assert tag_shots(client, items)[0]['tagged']
    assert len(calls) == 2
    assert not tag_shots(lambda _: 'bad', items)[0]['tagged']


def test_scene_fallback_frames_cache(video, tmp_path):
    db = Database(tmp_path / 'test.sqlite')
    asset = db.add_asset(video, probe(video))
    calls = []
    def detector(*_):
        calls.append(1)
        raise RuntimeError('unavailable')
    analyzer = SceneAnalyzer(db, tmp_path, detector=detector)
    result = analyzer.analyze(asset, segment_seconds=.75)
    assert len(result) == 3
    assert all(Path(s['frame_path']).is_file() for s in result)
    assert result[-1]['end'] == asset['duration']
    assert analyzer.analyze(asset, segment_seconds=.75) == result
    assert len(calls) == 1
    Path(result[0]['frame_path']).unlink()
    assert analyzer.analyze(asset, segment_seconds=.75) == result
    assert Path(result[0]['frame_path']).is_file()
    assert len(calls) == 1
    assert split(2, .75) == [(0, .75), (.75, 1.5), (1.5, 2)]


@pytest.fixture
def cut_video(tmp_path):
    try:
        ffmpeg = locate('ffmpeg')
    except RuntimeError:
        pytest.skip('ffmpeg unavailable')
    path = tmp_path / 'cuts.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc=size=160x120:rate=25:duration=2', '-f', 'lavfi', '-i',
        'color=c=red:size=160x120:rate=25:duration=2', '-filter_complex',
        '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True)
    return path


def test_scenedetect_cuts(cut_video):
    pytest.importorskip('scenedetect')
    cuts = detect_scenes(cut_video, 20.)
    assert len(cuts) >= 2
    assert any(abs(start - 2) < .2 for start, _ in cuts)


def test_renderer_real(video, tmp_path):
    bgm = tmp_path / 'music.wav'
    subprocess.run([locate('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=4', str(bgm)], check=True, capture_output=True)
    # Three distinct source intervals, transitions shorten the 1.8 s sum to 1.6 s.
    items = [dict(id=str(i), asset_id='a', start=i*.6, end=(i+1)*.6, quality=5) for i in range(3)]
    timeline = rule_plan(items, 1.8, 160, 284)
    for i in (1, 2):
        timeline['clips'][i]['transition_in'] = {'type': 'crossfade' if i == 1 else 'slide', 'duration': .1}
    timeline['clips'][0]['caption'] = dict(text="中文 ' : , ; [x] % {test}\\ hello", position='bottom', start_offset=0., end_offset=.5)
    timeline['bgm'] = dict(path=str(bgm), volume=.2, fade_out=.2)
    timeline = validate_timeline(timeline, items, 1.6)
    target = tmp_path / 'output.mp4'
    values = []
    render(timeline, {'a': {'path': str(video)}}, target, progress=values.append)
    meta = probe(target)
    assert (meta['width'], meta['height']) == (160, 284)
    assert abs(meta['duration'] - 1.6) < .15
    data = subprocess.run([locate('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(target)], capture_output=True, check=True)
    assert {s['codec_type'] for s in json.loads(data.stdout)['streams']} == {'video', 'audio'}
    assert values and max(values) > 65
    with pytest.raises(Cancelled):
        render(timeline, {'a': {'path': str(video)}}, tmp_path / 'cancel.mp4', cancelled=lambda: True)
    assert not (tmp_path / 'cancel.mp4').exists()


def test_llm_never_leaks_key(caplog):
    import uuid
    key = uuid.uuid4().hex
    provider = dict(base_url='https://offline.invalid/v1', model='test')
    def fail(request):
        assert request.headers['Authorization'] == 'Bearer ' + key
        raise RuntimeError(key)
    with pytest.raises(ValueError) as exc:
        LLMClient(provider, key, httpx.MockTransport(fail))([])
    assert key not in str(exc.value) and key not in caplog.text
    def echo(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': key}}]})
    assert key not in LLMClient(provider, key, httpx.MockTransport(echo))([])


def wait_task(client, task_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        row = client.get('/api/tasks/' + task_id).json()
        if row['status'] not in ('pending', 'running'):
            assert row['status'] == 'succeeded', row
            return
        time.sleep(.05)
    pytest.fail('task timed out')


def test_remix_api_flow(video, tmp_path, caplog):
    secret = MemorySecrets()
    def factory(provider, key):
        def complete(messages):
            content = messages[0]['content']
            if isinstance(content, list):
                ids = [v['text'].split('=', 1)[1] for v in content if v.get('text', '').startswith('shot_id=')]
                return json.dumps([dict(shot_id=i, description='镜头', tags=['测试'], shot_type='wide',
                    has_person=False, quality_score=8, suggested_use='主体') for i in ids])
            # Deliberately invalid planner response exercises rule fallback.
            return '{}'
        return complete
    app = create_app(Settings(tmp_path / 'data', retries=0), secret, remix_client_factory=factory)
    with TestClient(app) as client:
        provider = client.post('/api/providers', json=dict(type='llm', name='Mock', model='mock', base_url='https://offline.invalid/v1')).json()
        import uuid
        key = uuid.uuid4().hex
        client.put('/api/secrets/' + provider['id'], json={'key': key})
        scan_result = client.post('/api/assets/scan', json={'folder': str(video.parent), 'recursive': True})
        assert scan_result.status_code == 200
        asset = scan_result.json()['assets'][0]
        data = dict(name='项目', target_seconds=1.5, width=160, height=284, asset_ids=[asset['id']],
            settings=dict(provider_id=provider['id'], segment_seconds=.75))
        p = client.post('/api/remix/projects', json=data)
        assert p.status_code == 201
        root = '/api/remix/projects/' + p.json()['id']
        assert client.get(root + '/estimate').json()['images'] == 3
        task = client.post(root + '/analyze', json={'tag': True})
        assert task.status_code == 202
        wait_task(client, task.json()['id'])
        items = client.get(root + '/shots').json()
        assert len(items) == 3 and all(s['tagged'] for s in items)
        assert client.get(items[0]['frame_url']).headers['content-type'] == 'image/jpeg'
        assert client.get(root + '/estimate').json()['images'] == 0
        wait_task(client, client.post(root + '/plan').json()['id'])
        timeline = client.get(root).json()['timeline']
        assert timeline['fallback']
        assert len(client.get(root + '/preview-frames').json()) == 3
        invalid = json.loads(json.dumps(timeline))
        invalid['clips'][0]['src_start'] = -1
        assert client.put(root + '/timeline', json=invalid).status_code == 400
        assert client.put(root + '/timeline', json=timeline).status_code == 200
        wait_task(client, client.post(root + '/render').json()['id'])
        output = client.get(root).json()['output_asset_id']
        assert client.get('/api/remix/output/' + output).status_code == 200
        assert client.get('/api/remix/output/' + asset['id']).status_code == 404
        assert key not in client.get(root).text and key not in caplog.text
        assert client.put(root, json=data).status_code == 200
        assert client.delete(root).status_code == 204
        assert client.get(root).status_code == 404


def test_scan_security(client, video, tmp_path):
    for path in ('relative', str(tmp_path / '..' / tmp_path.name)):
        assert client.post('/api/assets/scan', json={'folder': path}).status_code == 400
    root = tmp_path / 'scan'
    root.mkdir()
    try:
        (root / 'escape.mp4').symlink_to(video)
        (root / 'outside').symlink_to(video.parent, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    assert client.post('/api/assets/scan', json={'folder': str(root), 'recursive': True}).json()['assets'] == []


def test_rule_uneven_durations():
    items = [dict(id='long', asset_id='a', start=0., end=10., quality=10),
             dict(id='short', asset_id='a', start=10., end=11., quality=1)]
    assert abs(sum(c['src_end'] - c['src_start'] for c in rule_plan(items, 10)['clips']) - 10) < .01


def test_planner_repair_has_validation_reason():
    calls = []
    good = rule_plan(shots(), 6)
    bad = json.loads(json.dumps(good))
    bad['clips'][0]['shot_id'] = 'invalid'
    def client(messages):
        calls.append(json.loads(json.dumps(messages)))
        return json.dumps(bad if len(calls) == 1 else dict(good, fallback=False))
    result = plan(client, shots(), 'test', 6)
    assert not result['fallback'] and len(calls) == 2
    assert '镜头引用无效' in calls[1][-1]['content']


def test_cancel_during_encode(video, tmp_path):
    timeline = rule_plan([dict(id='a', asset_id='a', start=0., end=2., quality=1)], 2, 160, 284)
    state = {'cancel': False}
    def progress(_):
        state['cancel'] = True
    target = tmp_path / 'cancelled.mp4'
    with pytest.raises(Cancelled):
        render(timeline, {'a': {'path': str(video)}}, target, progress, lambda: state['cancel'])
    assert not target.exists()


def test_queue_cancel_pending_project_unlocks(video, tmp_path):
    from threading import Event
    from core.queue import TaskQueue
    from modules.remix.service import RemixService
    from api.remix import ProjectInput
    db = Database(tmp_path / 'queue.sqlite')
    settings = Settings(tmp_path)
    service = RemixService(db, MemorySecrets(), settings)
    asset = db.add_asset(video, probe(video))
    project = service.save(ProjectInput(name='pending', asset_ids=[asset['id']]).model_dump())
    release = Event()
    def blocker(*_):
        release.wait(5)
    queue = TaskQueue(db, {'block': blocker, 'remix_analyze': service.handler('analyze')},
                      concurrency=1, retries=0, on_finished=service.task_finished)
    try:
        queue.create('block', {})
        task = service.enqueue(queue, project['id'], 'analyze', False)
        queue.cancel(task['id'])
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and service.get(project['id'])['status'] != 'cancelled':
            time.sleep(.02)
        assert service.get(project['id'])['status'] == 'cancelled'
        assert service.require_idle(project['id'])
    finally:
        release.set()
        queue.close()


@pytest.mark.parametrize('change', [
    {'fps': '30'}, {'width': 161}, {'version': True}, {'height': -2}, {'clips': []},
    {'bgm': {'path': 'x', 'volume': 5}}, {'global_caption_style': {'color': 'red:evil=1'}},
])
def test_invalid_schema(change):
    value = rule_plan(shots(), 6)
    value.update(change)
    with pytest.raises(ValueError):
        validate_timeline(value, shots(), 6)


@pytest.mark.parametrize('change', [
    {'src_end': float('nan')}, {'src_start': '0'}, {'mute': 1},
    {'transition_in': {'type': 'explode', 'duration': .2}},
    {'transition_in': {'type': 'crossfade', 'duration': 4.}},
    {'caption': {'text': 'x', 'start_offset': 1., 'end_offset': .2}},
    {'caption': {'text': 'x', 'start_offset': 0., 'end_offset': 50.}},
])
def test_invalid_clip(change):
    value = rule_plan(shots(), 6)
    value['clips'][0].update(change)
    with pytest.raises(ValueError):
        validate_timeline(value, shots(), 6)


def test_duration_tolerance_and_allow_repeat():
    from modules.remix.planner import duration
    value = rule_plan(shots(), 6)
    with pytest.raises(ValueError):
        validate_timeline(value, shots(), 10)
    repeat = rule_plan(shots(), 18, allow_repeat=True)
    assert duration(repeat) == 18
    validate_timeline(repeat, shots(), 18, allow_repeat=True)
    with pytest.raises(ValueError):
        rule_plan(shots(), 18)


def test_tagging_bad_schema_downgrades(tmp_path):
    frame = tmp_path / 'frame.jpg'
    frame.write_bytes(b'image')
    items = [dict(shots()[0], frame_path=str(frame))]
    for result in ('{}', '[{"shot_id":"other"}]', '[]', '```json\n[]\n```'):
        assert tag_shots(lambda _: result, items)[0]['tagged'] is False


def test_renderer_blur_speed_original_audio(video, tmp_path):
    source = tmp_path / 'audio.mp4'
    subprocess.run([locate('ffmpeg'), '-nostdin', '-v', 'error', '-y', '-i', str(video),
        '-f', 'lavfi', '-i', 'sine=frequency=880:duration=2', '-c:v', 'copy', '-c:a', 'aac',
        '-shortest', str(source)], check=True, capture_output=True)
    items = [dict(id='a', asset_id='a', start=0., end=2., quality=1)]
    timeline = rule_plan(items, 2, 160, 284)
    timeline['clips'][0]['speed'] = 2.
    timeline['clips'][0]['transition_in'] = {'type': 'fade', 'duration': .2}
    validate_timeline(timeline, items, 1)
    target = tmp_path / 'blur.mp4'
    render(timeline, {'a': {'path': str(source)}}, target, fit='blur')
    assert abs(probe(target)['duration'] - 1) < .15


def test_scan_without_symlink_privilege(client, video, tmp_path, monkeypatch):
    # Exercise escape rejection even on Windows hosts without symlink creation rights.
    import modules.remix.service as service
    monkeypatch.setattr(service.os, 'walk', lambda *_args, **_kw: iter([(str(video.parent), [], [video.name])]))
    root = tmp_path / 'allowed'
    root.mkdir()
    assert client.post('/api/assets/scan', json={'folder': str(root), 'recursive': True}).json()['assets'] == []
