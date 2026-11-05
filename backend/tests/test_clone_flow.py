import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from core.config import Settings
from core.db import Database
from core.secrets import MemorySecrets
from core.providers import Providers
from core.queue import TaskQueue
from modules.clone.service import CloneService, OPERATIONS
from modules.clone.brief import validate_brief
from modules.clone.assemble import build_timeline, write_ass, render
from modules.clone.variants import expand
from test_clone import analysis, treatment, script


@pytest.fixture
def service(tmp_path,monkeypatch):
    settings=Settings(tmp_path/'data',retries=0)
    db=Database(settings.data_dir/'db.sqlite')
    secrets=MemorySecrets()
    calls=[]
    class Video:
        def __init__(self,*args,**kwargs): pass
        def submit(self,images,prompt,duration,aspect,extra):
            calls.append(('video',prompt));return {'id':'job'}
        def wait_result(self,job,progress=lambda _:None): return 'https://cdn.test/result.mp4'
        def download(self,url,target): Path(target).write_bytes(b'video')
    class TTS:
        def __init__(self,*args,**kwargs): pass
        def speak(self,text,target,voice): calls.append(('tts',text));Path(target).write_bytes(b'voice')
    s=CloneService(db,secrets,settings,dict(video_gen=Video,tts=TTS))
    providers={}
    for kind,pricing in [('video_gen',{'per_second':1.}),('tts',{'per_character':.1}),('llm',{'per_request':0.})]:
        row=Providers(db,secrets).save(SimpleNamespace(type=kind,name=kind,base_url='https://provider.test',model='m',api_style='replicate',extra={'pricing':pricing}))
        secrets.set(row['id'],'private-test-value')
        providers[kind]=row['id']
    reference=tmp_path/'reference.mp4';reference.write_bytes(b'reference')
    image=tmp_path/'product.png';image.write_bytes(b'image')
    source=db.add_asset(reference,dict(type='video',duration=5.,width=160,height=284))
    picture=db.add_asset(image,dict(type='image',duration=0.,width=160,height=284))
    p=s.save(dict(name='test',source_asset_id=source['id'],source_url=None,mode='hook',hook_seconds=5.,brief=dict(
        providers=providers,reference_assets=[picture['id']],budget_usd=10.,target_seconds=5.)))
    s.update(p['id'],analysis=analysis(),treatment=treatment(picture['id']),script=script(),status='planned')
    monkeypatch.setattr('modules.clone.generation.probe',lambda path:dict(type='video',duration=5.,width=160,height=284))
    return s,p['id'],calls


def test_budget_gate_dry_run_unknown_and_over_budget(service):
    s,id,calls=service
    queue=SimpleNamespace(create=lambda *a,**kw:pytest.fail('unconfirmed operation queued'))
    result=s.enqueue(queue,id,'generate',{})
    assert result['dry_run'] and result['cost']['known_total_usd']>0
    with pytest.raises(ValueError,match='费用'):
        s.enqueue(queue,id,'generate',dict(dry_run=False))
    assert calls==[] and s.get(id)['status']=='needs_attention'
    s.update(id,budget_usd=0.)
    assert s.cost(id)['over_budget']
    provider=s.provider(s.get(id),'video_gen')
    s.db.execute("UPDATE providers SET extra='{}' WHERE id=?",(provider['id'],))
    assert s.cost(id)['unknown']
    with pytest.raises(ValueError): s.generator.reserve(s.get(id),False)
    s.generator.reserve(s.get(id),True)
    assert s.db.one('SELECT * FROM clone_reservations')['unknown']==1


def test_generation_cache_and_single_shot_regenerate(service):
    s,id,calls=service
    p=s.get(id)
    s.generator.reserve(p,True)
    s.generator.run(p,lambda _:None,lambda:False)
    assert [c[0] for c in calls]==['video','tts']
    s.generator.run(p,lambda _:None,lambda:False)
    assert len(calls)==2 and s.cost(id)['known_total_usd']==0
    s.generator.run(p,lambda _:None,lambda:False,only_shot='s1',force=True)
    assert [c[0] for c in calls]==['video','tts','video']
    media=s.media(id)
    assert max(m['attempt'] for m in media if m['kind']=='video')>=2
    assert all('private-test-value' not in json.dumps(m) for m in media)


def test_fallback_and_replacement_do_not_call_paid_clients(service,monkeypatch,tmp_path):
    s,id,calls=service
    monkeypatch.setattr('modules.clone.generation.ken_burns',lambda image,target,duration,cancelled:Path(target).write_bytes(b'fallback'))
    p=s.get(id)
    s.generator.run(p,lambda _:None,lambda:False,only_shot='s1',force=True,fallback=True)
    assert not calls
    assert s.media(id)[-1]['metadata']['fallback']
    s.generator.run(p,lambda _:None,lambda:False,only_shot='s1',replacement=p['brief']['reference_assets'][0])
    assert not calls and s.media(id)[-1]['metadata']['replacement_asset_id']


def test_variants_share_cache_and_total_budget(service):
    s,id,calls=service
    children=s.variants(id,2,{'cta':['购买','了解更多']})
    assert len(children)==2 and children[0]['analysis']==s.get(id)['analysis']
    for child in children:
        s.update(child['id'],treatment=s.get(id)['treatment'],script=s.get(id)['script'])
    s.generator.reserve(s.get(children[0]['id']),True)
    assert s.cost(children[1]['id'])['over_budget']
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(s.generator.run,s.get(c['id']),lambda _:None,lambda:False) for c in children]
        for f in futures:f.result()
    assert len(calls)==2 # the same requests from two variants are billed only once
    with pytest.raises(ValueError): s.delete(id)
    with pytest.raises(ValueError): expand(s.get(id)['brief'],3,{'style':['one']})


def test_deleting_variant_does_not_restore_spent_budget(service):
    s,id,_=service
    child=s.variants(id,1,{'style':['a']})[0]
    s.db.execute('UPDATE clone_projects SET spent_usd=8 WHERE id=?',(child['id'],))
    s.delete(child['id'])
    assert s.cost(id)['spent_usd']==8 and s.cost(id)['over_budget']


def test_word_anchored_timeline_and_safe_ass(service,tmp_path):
    s,id,_=service
    p=s.get(id)
    s.generator.run(p,lambda _:None,lambda:False)
    timeline=build_timeline(p['treatment'],p['script'],s.media(id),160,284)
    assert timeline['clips'][0]['mute'] and timeline['clone_duration']==5.
    assert timeline['clone_words'][0]['word']=='你好'
    assert all(a['path']!=s.asset(p['source_asset_id'])['path'] for a in timeline['clone_assets'].values())
    ass=tmp_path/'captions.ass'
    write_ass([dict(word='{\\pos(0,0)}test',start=0.,end=1.)],ass,160,284)
    text=ass.read_text(encoding='utf-8-sig')
    assert '{\\kf100}' in text and '{\\pos' not in text


def test_editing_script_cannot_reuse_old_voice(service):
    s,id,_=service
    p=s.get(id)
    s.generator.run(p,lambda _:None,lambda:False)
    changed=script();changed['segments'][0]['words'][0]['spoken']='全新台词'
    s.save_document(id,'script',changed)
    current=s.get(id)
    assert not any(m['kind']=='speech' for m in s.current_media(current))
    with pytest.raises(ValueError,match='音频'):
        build_timeline(current['treatment'],current['script'],s.current_media(current))


def test_ambiguous_submission_requires_explicit_regeneration(service):
    from modules.clone.adapters.base import Transient
    s,id,_=service
    requests=[]
    class Ambiguous:
        def __init__(self,*a,**kw):pass
        def submit(self,*a):requests.append('submit');raise Transient('timeout')
    s.factories['video_gen']=Ambiguous
    p=s.get(id)
    with pytest.raises(Transient): s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert s.media(id)[-1]['status']=='unknown'
    with pytest.raises(ValueError,match='未知'): s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert requests==['submit']
    with pytest.raises(Transient): s.generator.run(p,lambda _:None,lambda:False,only_shot='s1',force=True)
    assert requests==['submit','submit']


def test_persisted_video_job_resumes_without_new_submission(service):
    from modules.clone.adapters.base import Transient
    s,id,_=service
    requests=[]
    class Resumable:
        def __init__(self,*a,**kw):pass
        def submit(self,*a):requests.append('submit');return {'id':'remote-job'}
        def wait_result(self,job,progress=lambda _:None):
            requests.append('poll')
            if requests.count('poll')==1:raise Transient('temporary failure')
            return 'https://cdn.test/result'
        def download(self,url,target):Path(target).write_bytes(b'video')
    s.factories['video_gen']=Resumable
    p=s.get(id)
    with pytest.raises(Transient):s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert requests==['submit','poll','poll']
    assert s.get(id)['spent_usd']==5.


def test_batch_estimate_and_admission_are_offline(service):
    s,id,calls=service
    children=s.variants(id,2,{'style':['a','b']})
    for c in children:s.update(c['id'],treatment=s.get(id)['treatment'],script=s.get(id)['script'])
    queued=[]
    queue=SimpleNamespace(create=lambda kind,payload,**kw:queued.append((kind,payload)) or {'id':str(len(queued))})
    quote=s.batch(queue,id,'generate',{})
    assert quote['dry_run'] and quote['cost']['over_budget'] and not calls and not queued
    with pytest.raises(ValueError):s.batch(queue,id,'generate',{'dry_run':False})
    result=s.batch(queue,id,'generate',{'dry_run':False,'confirm_budget':True,'confirm_amount':100.})
    assert len(result['tasks'])==2
    assert all(payload['auto_assemble_render'] for kind,payload in queued)
    assert len(s.db.all('SELECT * FROM clone_reservations'))==2


def test_cancelled_queued_task_releases_budget(service):
    s,id,calls=service
    entered=threading.Event();release=threading.Event()
    def block(payload,progress,cancelled):
        entered.set();release.wait(3)
    queue=TaskQueue(s.db,{'block':block,**{'clone_'+op:s.handler(op) for op in OPERATIONS}},concurrency=1,retries=0,on_finished=s.task_finished)
    try:
        queue.create('block',{})
        assert entered.wait(2)
        task=s.enqueue(queue,id,'generate',dict(dry_run=False,confirm_budget=True,confirm_amount=100.))
        queue.cancel(task['id']);release.set()
        deadline=time.monotonic()+3
        while id in s.active and time.monotonic()<deadline:time.sleep(.01)
        assert not calls and not s.db.all('SELECT * FROM clone_reservations')
        assert s.get(id)['status']=='needs_attention'
    finally:
        release.set();queue.close()


def test_queued_provider_change_prevents_paid_call(service):
    s,id,calls=service
    saved=[]
    queue=SimpleNamespace(create=lambda kind,payload,**kw:saved.append(payload) or {'id':'task'})
    s.enqueue(queue,id,'generate',dict(dry_run=False,confirm_budget=True,confirm_amount=100.))
    provider=s.provider(s.get(id),'video_gen')
    s.db.execute('UPDATE providers SET model=? WHERE id=?',('changed',provider['id']))
    with pytest.raises(ValueError,match='配置已变化'):s.handler('generate')(saved[0],lambda _:None,lambda:False)
    assert not calls and s.get(id)['status']=='needs_attention'


def test_api_full_mocked_flow(client,monkeypatch,tmp_path):
    s=client.app.state.clone
    reference=tmp_path/'source.mp4';reference.write_bytes(b'reference')
    source=s.db.add_asset(reference,dict(type='video',duration=5.,width=160,height=284))
    image=tmp_path/'picture.png';image.write_bytes(b'image')
    picture=s.db.add_asset(image,dict(type='image',duration=0.,width=160,height=284))
    providers={}
    for kind in ('llm','video_gen','tts'):
        response=client.post('/api/providers',json=dict(type=kind,name=kind,base_url='https://provider.test',model='m',extra={'pricing':{'per_request':0,'per_second':0,'per_character':0}}))
        assert response.status_code==201
        providers[kind]=response.json()['id']
        client.put('/api/secrets/'+providers[kind],json={'key':'not-a-real-key'})
    monkeypatch.setattr(s,'adapter',lambda *a,**kw:lambda _: '{}')
    monkeypatch.setattr('modules.clone.service.analyze',lambda *a,**kw:analysis())
    monkeypatch.setattr('modules.clone.service.plan_treatment',lambda *a,**kw:treatment(picture['id']))
    monkeypatch.setattr('modules.clone.service.plan_script',lambda *a,**kw:script())
    def generate(p,progress,cancelled,*args):
        ops={(o['kind'],o['shot_id']):o for o in s.generator.operations(p)}
        for kind,shot in [('video','s1'),('speech','line1')]:
            path=s.directory/(kind+'.mp4');path.write_bytes(b'generated')
            meta={'words':[dict(word='你好',start=0.,end=2.5),dict(word='品牌',start=2.5,end=5.)],'duration':5.}
            s.db.execute('''INSERT INTO generated_media (id,project_id,shot_id,kind,prompt,status,path,cache_key,metadata,created_at)
                VALUES (?,?,?,?,?,'succeeded',?,?,?,?)''',(kind,p['id'],shot,kind,'',str(path),ops[(kind,shot)]['cache_key'],json.dumps(meta),'today'))
    monkeypatch.setattr(s.generator,'run',generate)
    monkeypatch.setattr('modules.clone.service.render',lambda timeline,target,*args:Path(target).write_bytes(b'rendered'))
    monkeypatch.setattr('modules.clone.service.probe',lambda _:dict(type='video',duration=5.,width=1080,height=1920))
    response=client.post('/api/clone/projects',json=dict(name='API flow',source_asset_id=source['id'],brief=dict(reference_assets=[picture['id']],providers=providers)))
    assert response.status_code==201
    id=response.json()['id'];base='/api/clone/projects/'+id
    def wait(task):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=client.get('/api/tasks/'+task['id']).json()
            if row['status'] in ('succeeded','failed','cancelled') and id not in s.active:
                assert row['status']=='succeeded',row
                return
            time.sleep(.01)
        pytest.fail('queue did not finish')
    for op in ('analyze','plan','generate','assemble','render'):
        body={'dry_run':False,'confirm_budget':True,'confirm_amount':100.} if op in ('analyze','plan','generate') else None
        response=client.post(base+'/'+op,json=body)
        assert response.status_code==202,response.text
        wait(response.json())
    assert client.get(base).json()['status']=='rendered'
    output=client.get(base).json()['output_asset_id']
    assert client.get('/api/clone/output/'+output).content==b'rendered'
    assert client.get(base+'/analysis').json()['why_it_works']=='悬念'
    assert client.put(base+'/script',json=script()).status_code==200
    assert client.post(base+'/variants',json={'count':2,'dimensions':{'style':['a','b']}}).status_code==201


def test_real_ffmpeg_word_caption_render(video,tmp_path):
    """Host integration check: real generated stand-in + TTS stand-in + ASS + ffprobe."""
    import subprocess
    from core.ffmpeg import locate,probe
    audio=tmp_path/'voice.wav'
    subprocess.run([locate('ffmpeg'),'-nostdin','-v','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                    '-t','2',str(audio)],check=True,capture_output=True)
    t=treatment();t['shots'][0]['duration']=2.
    media=[dict(id='clip',kind='video',shot_id='s1',status='succeeded',path=str(video),metadata={}),
           dict(id='voice',kind='speech',shot_id='line1',status='succeeded',path=str(audio),
                metadata=dict(duration=2.,words=[dict(word='Hello',start=0.,end=1.),dict(word='world',start=1.,end=2.)]))]
    timeline=build_timeline(t,script(),media,160,284)
    target=tmp_path/'output.mp4'
    render(timeline,target,validate_brief({}),Database(tmp_path/'db'))
    info=probe(target)
    assert info['width']==160 and info['height']==284 and 1.9<info['duration']<2.2
    # ASS is actually applied: compare a decoded frame to the uncaptioned original.
    raw=subprocess.run([locate('ffmpeg'),'-v','error','-ss','0.5','-i',str(target),'-frames:v','1',
                        '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True).stdout
    assert len(raw)==160*284*3
    pixels=zip(raw[0::3],raw[1::3],raw[2::3])
    # Karaoke primary color is yellow; assert visible pixels in caption region, not only file existence.
    assert any(r>170 and g>170 and b<100 for i,(r,g,b) in enumerate(pixels) if i//160>180)


def test_accepted_post_with_unusable_body_stays_unknown_not_failed(service):
    """2xx but unparseable body: provider may have billed us, so a rerun must not silently resubmit."""
    from modules.clone.adapters.base import Fatal
    s,id,_=service
    requests=[]
    class BadBody:
        def __init__(self,*a,**kw): self.accepted=False
        def submit(self,*a):
            requests.append('submit'); self.accepted=True
            raise Fatal('Provider 返回无效或含敏感信息的数据')
    s.factories['video_gen']=BadBody
    p=s.get(id)
    with pytest.raises(Fatal): s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert s.media(id)[-1]['status']=='unknown'
    with pytest.raises(ValueError,match='未知'): s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert requests==['submit']


def test_rejected_post_is_plain_failure_and_may_retry(service):
    from modules.clone.adapters.base import Fatal
    s,id,_=service
    class Rejects:
        def __init__(self,*a,**kw): self.accepted=False
        def submit(self,*a): raise Fatal('Provider 请求失败，请检查配置')
    s.factories['video_gen']=Rejects
    with pytest.raises(Fatal): s.generator.run(s.get(id),lambda _:None,lambda:False,only_shot='s1')
    assert s.media(id)[-1]['status']=='failed'

@pytest.mark.parametrize('error', ['Fatal', 'Transient'])
def test_asr_failure_preserves_paid_speech(service, monkeypatch, error):
    from modules.clone.adapters import base
    s, identifier, calls = service
    original = s.adapter
    class BrokenASR:
        def transcribe(self, *args): raise getattr(base, error)('failed')
    monkeypatch.setattr(s, 'adapter', lambda p, kind, *args, **kwargs: BrokenASR() if kind == 'asr' else original(p, kind, *args, **kwargs))
    p = s.get(identifier)
    op = next(op for op in s.generator.operations(p) if op['kind'] == 'speech')
    s.generator.materialize(p, op, lambda: False)
    row = s.media(identifier)[-1]
    assert row['status'] == 'succeeded'
    assert row['metadata']['estimated'] is True


def test_unknown_cache_blocks_other_project(service):
    s, identifier, calls = service
    p = s.get(identifier)
    op = s.generator.operations(p)[0]
    other = s.variants(identifier, 1, {'cta': ['立即购买']})[0]['id']
    s.db.execute("INSERT INTO generated_media (id,project_id,shot_id,kind,provider,prompt,status,attempt,cache_key,created_at) VALUES (?,?,?,?,?,?,'unknown',1,?,?)",
                 ('unknown-other', other, op['shot_id'], op['kind'], op['provider']['id'], '{}', op['cache_key'], '2026-01-01'))
    with pytest.raises(ValueError, match='未知'):
        s.generator.materialize(p, op, lambda: False)
    assert not calls


def test_stale_confirmation_amount_is_rejected(service):
    from fastapi import HTTPException
    from core.queue import TaskQueue
    s,id,_=service
    queue=TaskQueue(s.db,{},1,0)
    try:
        quote=s.generator.estimate(s.get(id))['quote_usd']
        assert quote>0
        with pytest.raises(HTTPException) as exc:
            s.enqueue(queue,id,'generate',dict(dry_run=False,confirm_budget=True,confirm_amount=quote/2))
        assert exc.value.status_code==409
        with pytest.raises(HTTPException): # missing amount is also refused
            s.enqueue(queue,id,'generate',dict(dry_run=False,confirm_budget=True))
    finally:
        queue.close()


def test_missing_reference_fails_before_any_charge(service):
    s,id,calls=service
    p=s.get(id)
    picture=s.asset(p['brief']['reference_assets'][0])
    Path(picture['path']).unlink()
    with pytest.raises(ValueError,match='不存在|不可读'):
        s.generator.run(p,lambda _:None,lambda:False,only_shot='s1')
    assert not calls
    assert s.media(id)==[]  # rejected before any media row existed, so nothing was charged
    assert s.get(id)['spent_usd']==0


def test_precheck_rejects_bad_local_inputs(tmp_path):
    from modules.clone.generation import Generator
    good=tmp_path/'a.png';good.write_bytes(b'x')
    Generator.precheck('p',5,[str(good)])
    for args in (('',5,[str(good)]),('p',0,[str(good)]),('p',5,[str(tmp_path/'missing.png')])):
        with pytest.raises(ValueError): Generator.precheck(*args)


def test_batch_enqueue_is_all_or_nothing(service):
    s,id,calls=service
    children=s.variants(id,2,{'style':['a','b']})
    for c in children:s.update(c['id'],treatment=s.get(id)['treatment'],script=s.get(id)['script'])
    created=[];cancelled=[]
    queue=SimpleNamespace(create=lambda kind,payload,**kw:created.append(kind) or {'id':str(len(created))},
                          cancel=lambda task_id:cancelled.append(task_id))
    original=s.enqueue
    state={'n':0}
    def flaky(*a,**kw):
        state['n']+=1
        if state['n']==2: raise ValueError('boom')
        return original(*a,**kw)
    s.enqueue=flaky
    with pytest.raises(ValueError,match='boom'):
        s.batch(queue,id,'generate',{'dry_run':False,'confirm_budget':True,'confirm_amount':100.})
    assert cancelled==['1']
