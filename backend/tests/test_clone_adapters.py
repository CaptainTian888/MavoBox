import json
from pathlib import Path
import httpx
import pytest
from modules.clone.adapters.asr_client import ASRClient
from modules.clone.adapters.tts_client import TTSClient
from modules.clone.adapters.image_client import ImageClient
from modules.clone.adapters.video_client import VideoClient
from modules.clone.adapters.base import Fatal, Transient


def provider(style='openai_compat',extra=None):
    return dict(base_url='https://provider.test/v1',model='model',api_style=style,extra=extra or {})


def test_asr_multipart_and_word_times(tmp_path):
    audio=tmp_path/'voice.wav';audio.write_bytes(b'audio')
    def http(req):
        assert req.url.path=='/v1/audio/transcriptions'
        body=req.read()
        for text in (b'verbose_json',b'timestamp_granularities[]',b'word',b'language',b'zh',b'audio'):
            assert text in body
        return httpx.Response(200,json={'words':[{'word':'hello','start':0.,'end':1.}]})
    c=ASRClient(provider(),'private',transport=httpx.MockTransport(http))
    assert c.transcribe(audio,1.,'zh')[0]['end']==1.


def test_tts_uses_script_and_image_edits(tmp_path,monkeypatch):
    seen=[]
    def http(req):
        seen.append(req)
        if req.url.path.endswith('speech'):
            assert json.loads(req.content)['input']=='brand pronunciation'
            return httpx.Response(200,content=b'wav-data')
        assert req.url.path.endswith('/images/edits')
        assert b'reference-bytes' in req.read()
        return httpx.Response(200,json={'data':[{'b64_json':'aW1hZ2U='}]})
    transport=httpx.MockTransport(http)
    dest=tmp_path/'speech.wav'
    TTSClient(provider(),'private',transport=transport).speak('brand pronunciation',dest)
    assert dest.read_bytes()==b'wav-data'
    reference=tmp_path/'ref.png';reference.write_bytes(b'reference-bytes')
    monkeypatch.setattr('core.ffmpeg.probe',lambda _:dict(type='image',width=512,duration=0))
    image=tmp_path/'output.png'
    ImageClient(provider(),'private',transport=transport).generate('same identity',[reference],image)
    assert image.read_bytes()==b'image'


def test_generation_image_endpoint_and_no_auth_on_download(tmp_path,monkeypatch):
    def http(req):
        if req.url.host=='cdn.test':
            assert 'authorization' not in req.headers
            return httpx.Response(200,content=b'image')
        assert req.url.path.endswith('/images/generations')
        return httpx.Response(200,json={'data':[{'url':'https://cdn.test/image.png'}]})
    monkeypatch.setattr('core.ffmpeg.probe',lambda _:dict(type='image',width=512,duration=0))
    output=tmp_path/'a.png'
    ImageClient(provider(),'private',transport=httpx.MockTransport(http)).generate('prompt',[],output)
    assert output.read_bytes()==b'image'


def test_get_backoff_but_no_blind_paid_post_retry():
    attempts=[];waits=[]
    def http(req):
        attempts.append(req.method)
        if len(attempts)<3:return httpx.Response(503)
        return httpx.Response(200,json={'status':'succeeded','output':'https://cdn.test/video.mp4'})
    c=VideoClient(provider('replicate'),'private',transport=httpx.MockTransport(http))
    c.wait=lambda seconds:waits.append(seconds)
    assert c.poll('job')['status']=='succeeded'
    assert attempts==['GET']*3 and waits==[1,2]
    attempts.clear()
    with pytest.raises(Transient): c.submit([],'prompt',5.,'9:16',{})
    assert attempts==['POST']


def test_download_validates_ffprobe_and_removes_bad_result(tmp_path,monkeypatch):
    c=VideoClient(provider('replicate'),'private',transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'bad')))
    def bad(_):raise ValueError('unreadable')
    monkeypatch.setattr('core.ffmpeg.probe',bad)
    path=tmp_path/'bad.mp4'
    with pytest.raises(ValueError): c.download('https://cdn.test/video',path)
    assert not path.exists()


def test_local_image_data_url_and_upload_mode(tmp_path):
    image=tmp_path/'p.png';image.write_bytes(b'image')
    received=[]
    def http(req):
        if req.url.path.endswith('/upload'):
            assert b'image' in req.read()
            return httpx.Response(200,json={'data':{'url':'https://cdn.test/anchor.png'}})
        received.append(json.loads(req.content))
        return httpx.Response(200,json={'id':'j'})
    for mode in ('data_url','upload'):
        c=VideoClient(provider('custom_http',dict(image_upload=mode,upload_path='/upload',upload_result_path='data.url',
            body_template={'image':'{image}','duration':'{duration}','prompt':'{prompt}'},submit_path='/jobs')),
            'private',transport=httpx.MockTransport(http))
        c.submit([image],'hello',5.,'9:16',{})
    assert received[0]['image'].startswith('data:image/png;base64,')
    assert received[1]['image']=='https://cdn.test/anchor.png' and received[1]['duration']==5.


def test_provider_cannot_redirect_credentials_or_echo_key():
    c=VideoClient(provider('fal_queue'),'private',transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'id':'private'})))
    with pytest.raises(Fatal): c.request('GET','https://evil.test/status')
    with pytest.raises(Fatal): c.json(httpx.Response(200,json={'id':'private'}))


def test_extra_cannot_store_authentication(client):
    for extra in ({'headers':{'Authorization':'credential'}},{'defaults':{'api_key':'credential'}},
                  {'upload_path':'https://host.test/upload?key=credential'},{'pricing':{'per_second':-1}}):
        response=client.post('/api/providers',json=dict(type='video_gen',name='unsafe',base_url='https://provider.test',model='m',extra=extra))
        assert response.status_code==422
        assert 'credential' not in response.text


def test_analysis_cache_skips_multimodal_calls(tmp_path,monkeypatch):
    from core.db import Database
    from modules.clone.analysis import analyze
    from test_clone import analysis
    source=tmp_path/'ref.mp4';source.write_bytes(b'video')
    monkeypatch.setattr('modules.clone.analysis.probe',lambda _:dict(duration=5.))
    monkeypatch.setattr('modules.clone.analysis.trim',lambda source,target,*a:Path(target).write_bytes(b'clip'))
    monkeypatch.setattr('modules.clone.analysis.detect_scenes',lambda _:[(0.,2.),(2.,5.)])
    monkeypatch.setattr('modules.clone.analysis.extract_frame',lambda source,target,*a:Path(target).write_bytes(b'frame'))
    monkeypatch.setattr('modules.clone.analysis.has_audio',lambda _:False)
    db=Database(tmp_path/'db');calls=[]
    def llm(messages):calls.append(messages);return json.dumps(analysis())
    first=analyze(db,source,5.,'hook','zh',llm,None,tmp_path)
    second=analyze(db,source,5.,'hook','zh',llm,None,tmp_path)
    assert first==second and len(calls)==1
    assert first['transcript']==[] and first['open_questions']
