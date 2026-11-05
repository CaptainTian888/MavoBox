import hashlib
import json
from pathlib import Path
from uuid import uuid4
from core.db import now
from core.ffmpeg import probe
from core.queue import Cancelled
from modules.remix.renderer import run_ffmpeg
from .adapters.asr_client import estimate_words
from .analysis import file_hash
from .adapters.base import Auth, Fatal, ContentPolicy, RateLimit, Transient


def cache_key(kind, provider, params):
    return hashlib.sha256(json.dumps([3,kind,provider,params],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def price(provider, kind, quantity=1):
    if provider is None: return 0.
    pricing = provider.get('extra',{}).get('pricing',{})
    unit = {'video':'per_second','anchor':'per_image','speech':'per_character','asr':'per_minute'}[kind]
    value = pricing.get(unit)
    return None if value is None else float(value) * quantity


def ken_burns(image, target, duration, cancelled=lambda: False):
    frames = max(1,round(duration * 30))
    run_ffmpeg(['-loop','1','-i',str(image),'-vf',
        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0005,1.12)':d={frames}:s=1080x1920:fps=30",
        '-t',str(duration),'-an','-c:v','libx264','-pix_fmt','yuv420p',str(target)],duration,cancelled=cancelled)


class Generator:
    def __init__(self, service):
        self.s = service

    def operations(self, p, only_shot=None):
        b, output = p['brief'], []
        providers = {kind:self.s.provider(p,kind,required=kind in ('video_gen','tts'))
                     for kind in ('video_gen','tts','asr','image_gen')}
        for asset in b['reference_assets'] if providers['image_gen'] else []:
            path = self.s.asset(asset)['path']
            params = dict(asset=asset, hash=file_hash(path), prompt='人物/产品定妆锚图，保留身份、形状、商标和真实特征。' + b['style'])
            output.append(self.op('anchor',providers['image_gen'],params,price(providers['image_gen'],'anchor'),asset))
        if providers['image_gen']:
            for shot in p['treatment']['shots']:
                if only_shot and shot['id']!=only_shot: continue
                if len(shot['reference_assets'])>1:
                    params=dict(assets=shot['reference_assets'],hashes=[file_hash(self.s.asset(a)['path']) for a in shot['reference_assets']],
                                prompt='将所有参考主体保持身份与产品形状组合为同一首帧，不加文字。'+shot['prompt'])
                    output.append(self.op('anchor',providers['image_gen'],params,price(providers['image_gen'],'anchor'),'composite_'+shot['id']))
        for shot in p['treatment']['shots']:
            if only_shot and shot['id'] != only_shot: continue
            params = dict(shot=shot, references=[file_hash(self.s.asset(a)['path']) for a in shot['reference_assets']],
                          style=b['style'], anchor_provider=providers['image_gen'])
            output.append(self.op('video',providers['video_gen'],params,price(providers['video_gen'],'video',shot['duration']),shot['id']))
        if not only_shot:
            for segment in p['script']['segments']:
                text = ' '.join(w['spoken'] for w in segment['words'])
                params = dict(segment=segment, text=text, voice=b['voice'], language=b['language'], asr=providers['asr'])
                amount = price(providers['tts'],'speech',len(text))
                # ASR estimate is based on the target duration, later held conservatively as estimate.
                asr_cost = price(providers['asr'],'asr',b['target_seconds']/60)
                amount = None if amount is None or asr_cost is None else amount + asr_cost
                output.append(self.op('speech',providers['tts'],params,amount,segment['id']))
        return output

    def op(self, kind, provider, params, amount, shot_id):
        key = cache_key(kind,provider,params)
        return dict(kind=kind,provider=provider,params=params,cost_estimate=amount,shot_id=shot_id,cache_key=key)

    def cached(self, op):
        rows = self.s.db.all("SELECT * FROM generated_media WHERE cache_key=? AND status='succeeded' ORDER BY created_at DESC",(op['cache_key'],))
        # User overrides are valid for their project, not equivalent paid model outputs for other projects.
        return next((r for r in rows if r['path'] and Path(r['path']).is_file()
                     and not json.loads(r['metadata']).get('fallback')
                     and not json.loads(r['metadata']).get('replacement_asset_id')),None)

    def estimate(self, p, only_shot=None, force=False):
        items = []
        for op in self.operations(p,only_shot):
            cached = None if force and (op['kind'] == 'video' or not only_shot) else self.cached(op)
            items.append(dict(kind=op['kind'],shot_id=op['shot_id'],cached=bool(cached),
                              cost_estimate=0. if cached else op['cost_estimate']))
        unknown = any(i['cost_estimate'] is None for i in items)
        total = sum(i['cost_estimate'] or 0 for i in items)
        root = self.s.get(p['parent_id']) if p['parent_id'] else p
        spent = self.s.db.one('SELECT COALESCE(SUM(spent_usd),0) AS n FROM clone_projects WHERE id=? OR parent_id=?',(root['id'],root['id']))['n']
        reserved = self.s.db.one('SELECT COALESCE(SUM(amount),0) AS n FROM clone_reservations WHERE root_id=?',(root['id'],))['n']
        return dict(items=items,known_total_usd=round(total,6),unknown=unknown,total_usd=None if unknown else round(total,6),
                    budget_usd=root['budget_usd'],spent_usd=spent,reserved_usd=reserved,
                    over_budget=root['budget_usd'] is not None and spent+reserved+total > root['budget_usd'],
                    requires_confirmation=unknown or total > 0,
                    note='本次生成阶段估算；共享已花费包含分析/规划的保守估算。未知单价无法保证实际总额，已提交请求按估算保守计费，并非供应商实际账单。')

    def stage_estimate(self,p,operation):
        provider=self.s.provider(p,'llm',True)
        # Structured output may require up to 3 requests per document.
        attempts=3 if operation=='analyze' else 6
        unit=provider['extra'].get('pricing',{}).get('per_request')
        items=[dict(kind=operation,shot_id='llm',cached=False,cost_estimate=None if unit is None else attempts*unit)]
        asr=self.s.provider(p,'asr') if operation=='analyze' else None
        if asr:
            seconds=p['hook_seconds']
            if p['source_asset_id']:
                source=self.s.asset(p['source_asset_id'])
                seconds=source['duration'] if p['mode']=='full' else min(seconds,source['duration'])
            elif p['mode']=='full': seconds=3600
            items.append(dict(kind='asr',shot_id='reference',cached=False,cost_estimate=price(asr,'asr',seconds/60)))
        total=sum(i['cost_estimate'] or 0 for i in items)
        root=self.s.get(p['parent_id']) if p['parent_id'] else p
        spent=self.s.db.one('SELECT COALESCE(SUM(spent_usd),0) AS n FROM clone_projects WHERE id=? OR parent_id=?',(root['id'],root['id']))['n']
        reserved=self.s.db.one('SELECT COALESCE(SUM(amount),0) AS n FROM clone_reservations WHERE root_id=?',(root['id'],))['n']
        unknown=any(i['cost_estimate'] is None for i in items)
        return dict(items=items,known_total_usd=total,total_usd=None if unknown else total,unknown=unknown,
                    over_budget=root['budget_usd'] is not None and spent+reserved+total>root['budget_usd'],
                    spent_usd=spent,reserved_usd=reserved,budget_usd=root['budget_usd'],requires_confirmation=unknown or total>0,
                    note='分析/规划按最多 JSON 重试次数保守估算；LLM 单价使用 pricing.per_request，未知价格须明确确认。')

    def reserve_quote(self,p,cost,confirm):
        if (cost['requires_confirmation'] or cost['over_budget']) and not confirm:
            self.s.update(p['id'],status='needs_attention')
            raise ValueError('费用未知、需要付费或超出共享预算；请查看估算并明确确认预算')
        self.s.db.execute('INSERT OR REPLACE INTO clone_reservations VALUES (?,?,?,?)',
            (p['id'],p['parent_id'] or p['id'],cost['known_total_usd'],int(cost['unknown'])))
        return cost

    def reserve(self, p, confirm, only_shot=None, force=False):
        # The service lock serializes cost calculation and queue admission in this local process.
        cost = self.estimate(p,only_shot,force)
        return self.reserve_quote(p,cost,confirm)

    def charge(self, p, op):
        # Charges are conservative estimates, including failed/ambiguous submissions.
        amount = op['cost_estimate'] or 0
        with self.s.db.connect() as con:
            con.execute('UPDATE clone_projects SET spent_usd=spent_usd+? WHERE id=?',(amount,p['id']))
            con.execute('UPDATE clone_reservations SET amount=MAX(0,amount-?) WHERE project_id=?',(amount,p['id']))

    def run(self, p, progress, cancelled, only_shot=None, force=False, fallback=False, replacement=None):
        operations = self.operations(p,only_shot)
        for index, op in enumerate(operations):
            if cancelled(): raise Cancelled()
            if (replacement or fallback) and op['kind'] != 'video': continue
            self.materialize(p,op,cancelled,force and (op['kind']=='video' or not only_shot),fallback,replacement,
                             lambda value:progress((index+value/100)/max(1,len(operations))*95))
            progress((index+1)/max(1,len(operations))*95)

    def materialize(self, p, op, cancelled, force=False, fallback=False, replacement=None, progress=lambda _:None):
        db = self.s.db
        # One lock per cache key prevents concurrent variants submitting the same paid request twice.
        with self.s.cache_lock(op['cache_key']):
            cached = None if force or replacement else self.cached(op)
            if cached:
                db.execute('''INSERT INTO generated_media
                    (id,project_id,shot_id,kind,provider,prompt,cost_estimate,status,path,attempt,cache_key,metadata,created_at)
                    VALUES (?,?,?,?,?,?,0,'succeeded',?,0,?,?,?)''',
                    (uuid4().hex,p['id'],op['shot_id'],op['kind'],op['provider']['id'],json.dumps(op['params'],ensure_ascii=False),
                     cached['path'],op['cache_key'],cached['metadata'],now()))
                return
            pending = None if force or replacement else db.one('''SELECT * FROM generated_media
                WHERE cache_key=? AND project_id=? AND status IN ('submitted','failed','unknown') ORDER BY created_at DESC LIMIT 1''',
                (op['cache_key'],p['id']))
            if pending and pending['status']=='unknown':
                raise ValueError('上次提交结果未知；请先核查 Provider 账单，再明确选择重生成')
            media_id = pending['id'] if pending and pending['job'] else uuid4().hex
            suffix = {'anchor':'.png','video':'.mp4','speech':'.wav'}[op['kind']]
            target = self.s.directory / (media_id + suffix)
            if not pending or not pending['job']:
                attempt = db.one('SELECT COUNT(*) AS n FROM generated_media WHERE project_id=? AND shot_id=? AND kind=?',
                                 (p['id'],op['shot_id'],op['kind']))['n'] + 1
                db.execute('''INSERT INTO generated_media
                    (id,project_id,shot_id,kind,provider,prompt,cost_estimate,status,attempt,cache_key,created_at)
                    VALUES (?,?,?,?,?,?,?,'pending',?,?,?)''',
                    (media_id,p['id'],op['shot_id'],op['kind'],op['provider']['id'],json.dumps(op['params'],ensure_ascii=False),
                     0 if replacement or fallback else op['cost_estimate'],attempt,op['cache_key'],now()))
            metadata = {}
            try:
                if cancelled(): raise Cancelled()
                client = None if replacement or fallback else self.s.adapter(p, {'anchor':'image_gen','video':'video_gen','speech':'tts'}[op['kind']],cancelled)
                if replacement:
                    asset = self.s.asset(replacement)
                    if asset['id'] == p['source_asset_id']: raise ValueError('参考视频不能作为替换镜头')
                    if asset['type']=='image': ken_burns(asset['path'],target,op['params']['shot']['duration'],cancelled)
                    else:
                        import shutil
                        if probe(asset['path'])['duration'] < op['params']['shot']['duration']: raise ValueError('替换素材时长不足')
                        shutil.copyfile(asset['path'],target)
                    metadata['replacement_asset_id'] = replacement
                elif op['kind']=='anchor':
                    db.execute("UPDATE generated_media SET status='unknown' WHERE id=?",(media_id,))
                    self.charge(p,op)
                    references=op['params'].get('assets',[op['params'].get('asset')])
                    client.generate(op['params']['prompt'],[self.s.asset(a)['path'] for a in references],target)
                elif op['kind']=='video':
                    shot = op['params']['shot']
                    refs = []
                    for asset in shot['reference_assets']:
                        anchor = db.one("SELECT path FROM generated_media WHERE project_id=? AND shot_id=? AND kind='anchor' AND status='succeeded' ORDER BY created_at DESC LIMIT 1",(p['id'],asset))
                        refs.append(anchor['path'] if anchor else self.s.asset(asset)['path'])
                    composite=db.one("SELECT path FROM generated_media WHERE project_id=? AND shot_id=? AND kind='anchor' AND status='succeeded' ORDER BY created_at DESC LIMIT 1",(p['id'],'composite_'+shot['id']))
                    if composite: refs=[composite['path']]
                    if fallback:
                        if not refs: raise ValueError('静态兜底需要用户参考图')
                        ken_burns(refs[0],target,shot['duration'],cancelled)
                        metadata['fallback'] = True
                    else:
                        if pending and pending['job']: job = json.loads(pending['job'])
                        else:
                            db.execute("UPDATE generated_media SET status='unknown' WHERE id=?",(media_id,))
                            self.charge(p,op)
                            job = client.submit(refs,shot['prompt'] + '\nCamera: ' + shot['camera'] + '\nNo speech, no captions.',
                                                shot['duration'],'9:16',{'negative_prompt':shot['negative']})
                            db.execute("UPDATE generated_media SET job=?,status='submitted' WHERE id=?",(json.dumps(job),media_id))
                        def report(value):
                            db.execute('UPDATE generated_media SET metadata=? WHERE id=?',(json.dumps({'progress':value}),media_id))
                            progress(value)
                        url = client.wait_result(job,report)
                        client.download(url,target)
                        if probe(target)['duration'] + .1 < shot['duration']: raise ValueError('生成镜头时长不足')
                else:
                    if not pending or not pending['job']:
                        db.execute("UPDATE generated_media SET status='unknown' WHERE id=?",(media_id,))
                        self.charge(p,op)
                        client.speak(op['params']['text'],target,op['params']['voice'])
                        db.execute("UPDATE generated_media SET job=?,status='submitted',path=? WHERE id=?",
                                   (json.dumps({'speech_ready':True}),str(target),media_id))
                    duration = probe(target)['duration']
                    words = op['params']['segment']['words']
                    asr = self.s.adapter(p,'asr',cancelled,required=False)
                    recognized = asr.transcribe(target,duration,p['brief']['language']) if asr else []
                    # Script remains authoritative. ASR defines boundaries only; never replaces its text.
                    import re
                    normalize=lambda text: re.sub(r'\W','',text).casefold()
                    if len(recognized)==len(words) and all(normalize(r['word'])==normalize(w['spoken']) for w,r in zip(words,recognized)):
                        timed = [dict(word=w['display'],start=r['start'],end=r['end']) for w,r in zip(words,recognized)]
                        estimated = False
                    else:
                        timed = estimate_words([w['spoken'] for w in words],duration)
                        for t,w in zip(timed,words): t['word']=w['display']
                        estimated = True
                    metadata = dict(words=timed,estimated=estimated,duration=duration,segment_id=op['shot_id'])
                if cancelled(): raise Cancelled()
                db.execute("UPDATE generated_media SET path=?,status='succeeded',metadata=? WHERE id=?",(str(target),json.dumps(metadata,ensure_ascii=False),media_id))
            except BaseException as exc:
                # A persisted job can safely resume; an uncertain POST requires explicit regeneration.
                if isinstance(exc,(Auth,Fatal,ContentPolicy)):
                    db.execute("UPDATE generated_media SET status='failed' WHERE id=?",(media_id,))
                else:
                    db.execute("UPDATE generated_media SET status=CASE WHEN job IS NOT NULL THEN 'submitted' WHEN status='pending' THEN 'failed' ELSE status END WHERE id=?",(media_id,))
                messages={Auth:'认证失败，请检查系统凭据',RateLimit:'供应商限流',Transient:'请求暂时失败或提交结果未知，请先核查账单',
                          Fatal:'结果或接口配置无效，请检查 Provider',ContentPolicy:'生成失败或内容被拒绝',Cancelled:'已取消本地任务'}
                message=next((v for k,v in messages.items() if isinstance(exc,k)),'素材处理失败，请检查文件、时长及本地配置')
                db.execute('UPDATE generated_media SET metadata=? WHERE id=?',(json.dumps({'error':message,'error_type':type(exc).__name__},ensure_ascii=False),media_id))
                raise
