"""Offline M3 contracts. Run on host; HTTP transports never reach the network."""
import json
from pathlib import Path
import httpx
import pytest
from core.db import Database
from core.queue import Cancelled
from modules.clone.adapters.base import Auth, RateLimit, Transient, Fatal, ContentPolicy
from modules.clone.adapters.video_client import VideoClient
from modules.clone.adapters.asr_client import estimate_words
from modules.clone.schemas import validate_analysis, validate_treatment, validate_script, structured


def analysis():
    return dict(language='zh', duration=5., hook_type='question', summary='问题', why_it_works='悬念',
        beats=[dict(start=0., end=5., role='hook', what_happens='展示', speaker='host',
                    visual=dict(shot_type='close', camera='fixed', composition='center'),
                    on_screen_text=[], graphics=[], audio=dict(music='', sfx='', voice_style='warm'))],
        transcript=[], systems=[], open_questions=[])


def treatment(asset='image'):
    return dict(beats=[dict(beat_ref=0, preserve='问题', rebuild='新产品', form='展示')],
        shots=[dict(id='s1', beat_ref=0, kind='product', duration=5., prompt='show product', negative='',
                    reference_assets=[asset], camera='fixed', audio_ref='line1', word_start=0, word_end=1)])


def script():
    return dict(language='zh', segments=[dict(id='line1', role='host', words=[
        dict(display='你好', spoken='你好', marks=['emphasis']), dict(display='品牌', spoken='品牌', marks=['cta'])])])


def test_migration_repeatable_and_preserves_provider(tmp_path):
    import sqlite3
    path = tmp_path / 'db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE providers(id TEXT PRIMARY KEY,type TEXT,name TEXT,base_url TEXT,model TEXT)')
        con.execute("INSERT INTO providers VALUES ('p','llm','old','https://example.test','model')")
    Database(path); db = Database(path)
    assert db.one('SELECT * FROM providers')['extra'] == '{}'
    assert db.one('SELECT * FROM providers')['name'] == 'old'


def test_strict_models_and_retry():
    a = validate_analysis(analysis(), 5.)
    t = validate_treatment(treatment(), a, ['image'], 5., {})
    validate_script(script(), t)
    bad = treatment('missing')
    with pytest.raises(ValueError): validate_treatment(bad, a, ['image'], 5., {})
    bad = treatment(); bad['shots'][0]['duration'] = 7.
    with pytest.raises(ValueError): validate_treatment(bad, a, ['image'], 7., {})
    replies = iter(['broken', '```json\n' + json.dumps(analysis()) + '\n```'])
    assert structured(lambda _: next(replies), [], lambda v: validate_analysis(v, 5.)) == a
    with pytest.raises(ValueError): structured(lambda _: '{}', [], lambda v: validate_analysis(v, 5.))


def test_invalid_words_references_forbidden_terms_and_retry_limit():
    a=analysis();a['transcript']=[dict(word='bad',start=3.,end=2.)]
    with pytest.raises(ValueError):validate_analysis(a,5.)
    a=analysis();a['duration']=float('nan')
    with pytest.raises(ValueError):validate_analysis(a,5.)
    a=analysis();a['unknown']='field'
    with pytest.raises(ValueError):validate_analysis(a,5.)
    t=treatment();t['shots'][0]['audio_ref']='missing'
    with pytest.raises(ValueError):validate_script(script(),t)
    t=treatment();t['shots'][0]['word_end']=0
    with pytest.raises(ValueError):validate_script(script(),t)
    with pytest.raises(ValueError):validate_script(script(),treatment(),['品牌'])
    calls=[]
    with pytest.raises(ValueError):
        structured(lambda messages:calls.append(messages) or '{}',[],lambda v:validate_analysis(v,5.))
    assert len(calls)==3


@pytest.mark.parametrize('status,error', [(401,Auth),(403,Auth),(429,RateLimit),(503,Transient),(400,Fatal),(422,ContentPolicy)])
def test_adapter_errors_are_sanitized(status, error):
    client = VideoClient(dict(base_url='https://example.test', model='m', api_style='replicate', extra={}),
        'fake-private-value', transport=httpx.MockTransport(lambda r: httpx.Response(status, text='fake-private-value')),
        retries=0)
    with pytest.raises(error) as exc: client.submit([], 'p', 5, '9:16', {})
    assert 'fake-private-value' not in str(exc.value)


@pytest.mark.parametrize('style', ['fal_queue','replicate','custom_http'])
def test_video_protocols(style):
    requests = []
    def http(req):
        requests.append(req)
        if req.method == 'POST':
            return httpx.Response(200, json={'request_id':'j','id':'j', 'status_url':'https://example.test/status/j',
                'response_url':'https://example.test/result/j'})
        if '/result/' in str(req.url): return httpx.Response(200, json={'video':{'url':'https://cdn.test/v.mp4'}})
        return httpx.Response(200, json={'status':'COMPLETED' if style == 'fal_queue' else 'succeeded',
            'output':['https://cdn.test/v.mp4']})
    extra = {'submit_path':'/jobs', 'poll_path':'/status/{job_id}', 'result_path':'output.0',
             'body_template':{'prompt':'{prompt}', 'images':'{images}', 'duration':'{duration}'}}
    c = VideoClient(dict(base_url='https://example.test',model='m',api_style=style,extra=extra),
                    'test-key', transport=httpx.MockTransport(http))
    job = c.submit([], 'hello', 5, '9:16', {})
    result = c.poll(job)
    assert result['status'] == 'succeeded' and result['url'].endswith('v.mp4')
    assert requests[0].headers['Authorization'] == ('Key test-key' if style == 'fal_queue' else 'Bearer test-key')
    assert json.loads(requests[0].content).get('input', json.loads(requests[0].content))['prompt'] == 'hello'


def test_estimated_words_follow_character_weight():
    words = estimate_words(['a', 'bbb'], 4.)
    assert words == [{'word':'a','start':0.,'end':1.}, {'word':'bbb','start':1.,'end':4.}]


def test_cancel_before_paid_submit():
    c = VideoClient(dict(base_url='https://example.test',model='m',api_style='replicate',extra={}),
                    'test', cancelled=lambda: True,
                    transport=httpx.MockTransport(lambda _: pytest.fail('cancelled call reached HTTP')))
    with pytest.raises(Cancelled): c.submit([], 'p', 5, '9:16', {})
