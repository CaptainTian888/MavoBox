import json
import hashlib
from pathlib import Path
from threading import RLock
from uuid import uuid4
from core.db import now
from core.ffmpeg import probe
from core.queue import Cancelled
from modules.downloader import Downloader
from .adapters import LLMClient, ASRClient, ImageClient, TTSClient, VideoClient
from .analysis import analyze
from .brief import validate_brief
from .treatment import plan_treatment
from .script import plan_script
from .schemas import validate_treatment, validate_script
from .generation import Generator
from .assemble import build_timeline, render
from .variants import expand


JSON_FIELDS = ('brief','analysis','treatment','script','timeline','variant_params')
OPERATIONS = ('analyze','plan','generate','assemble','render','regenerate')


class CloneService:
    def __init__(self, db, secrets, settings, factories=None):
        self.db,self.secrets,self.settings=db,secrets,settings
        self.factories=dict(llm=LLMClient,asr=ASRClient,image_gen=ImageClient,tts=TTSClient,video_gen=VideoClient)
        self.factories.update(factories or {})
        self.lock=RLock()
        self.cache_locks={}
        self.active=set()
        self.directory=(settings.data_dir/'clone').resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.generator=Generator(self)
        db.execute("UPDATE clone_projects SET status='needs_attention' WHERE status IN ('analyzing','planning','generating','assembling','rendering')")
        db.execute('DELETE FROM clone_reservations')

    def cache_lock(self,key):
        with self.lock: return self.cache_locks.setdefault(key,RLock())

    def asset(self,id):
        row=self.db.one('SELECT * FROM assets WHERE id=?',(id,))
        if not row or not Path(row['path']).is_file(): raise ValueError('引用素材不存在')
        return row

    def provider(self,p,kind,required=False):
        id=p['brief']['providers'].get(kind)
        if not id:
            if required: raise ValueError('请先配置 '+kind+' Provider')
            return None
        row=self.db.one('SELECT * FROM providers WHERE id=?',(id,))
        if not row or row['type']!=kind: raise ValueError('Provider 不存在或类型不匹配')
        row['extra']=json.loads(row['extra'])
        return row

    def adapter(self,p,kind,cancelled=lambda: False,required=True):
        provider=self.provider(p,kind,required)
        if not provider: return None
        key=self.secrets.get(provider['id'])
        if not key: raise ValueError('Provider 密钥未设置')
        if kind=='llm': return self.factories[kind](provider,key)
        return self.factories[kind](provider,key,cancelled=cancelled)

    def get(self,id):
        row=self.db.one('SELECT * FROM clone_projects WHERE id=?',(id,))
        if not row: raise LookupError('克隆项目不存在')
        for k in JSON_FIELDS: row[k]=json.loads(row[k]) if row[k] else None
        return row

    def list(self):
        return [self.get(r['id']) for r in self.db.all('SELECT id FROM clone_projects ORDER BY created_at DESC')]

    def update(self,id,**values):
        allowed=set(JSON_FIELDS)|{'source_asset_id','status','output_asset_id','budget_usd','name','source_url','mode','hook_seconds'}
        if not set(values)<=allowed: raise ValueError('更新字段无效')
        values={k:json.dumps(v,ensure_ascii=False) if k in JSON_FIELDS and v is not None else v for k,v in values.items()}
        values['updated_at']=now()
        self.db.execute('UPDATE clone_projects SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),id))

    def idle(self,id):
        p=self.get(id)
        tasks=self.db.all("SELECT payload FROM tasks WHERE kind LIKE 'clone_%' AND status IN ('pending','running')")
        if id in self.active or any(json.loads(t['payload']).get('project_id')==id for t in tasks):
            raise ValueError('项目有任务运行，请先等待或取消')
        return p

    def check_brief(self,b,source=None):
        b=validate_brief(b)
        for id in b['reference_assets']:
            if self.asset(id)['type']!='image': raise ValueError('人物/产品参考素材必须是图片')
        for field,kind in (('bgm_asset_id','audio'),('concat_tail_asset_id','video')):
            if b[field]:
                if b[field]==source or self.asset(b[field])['type']!=kind:
                    raise ValueError('BGM 或后半段素材类型无效，不能复用参考视频')
        if b['font_path'] and not Path(b['font_path']).is_file(): raise ValueError('字幕字体文件不存在')
        b['providers']={k:v for k,v in b['providers'].items() if v}
        for kind in b['providers']: self.provider({'brief':b},kind,True)
        return b

    def save(self,data,id=None):
        with self.lock:
            if id: self.idle(id)
            source=data.get('source_asset_id')
            if bool(source)==bool(data.get('source_url')): raise ValueError('请提供一个参考链接或视频素材')
            if source and self.asset(source)['type']!='video': raise ValueError('参考素材必须是视频')
            b=self.check_brief(data.get('brief',{}),source)
            if id:
                self.update(id,**{k:data[k] for k in ('name','source_url','source_asset_id','mode','hook_seconds')},
                            brief=b,budget_usd=b['budget_usd'],analysis=None,treatment=None,script=None,timeline=None,
                            output_asset_id=None,status='created')
            else:
                id=uuid4().hex
                self.db.execute('''INSERT INTO clone_projects
                    (id,name,source_url,source_asset_id,hook_seconds,mode,brief,budget_usd,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',(id,data['name'],data.get('source_url'),source,data.get('hook_seconds',5),
                    data.get('mode','hook'),json.dumps(b,ensure_ascii=False),b['budget_usd'],now(),now()))
            return self.get(id)

    def delete(self,id):
        with self.lock:
            p=self.idle(id)
            if self.db.one('SELECT id FROM clone_projects WHERE parent_id=?',(id,)):
                raise ValueError('请先删除所有变体；共享预算根项目不能先删除')
            # Keep generated files/cache as paid reusable artifacts; delete only project records.
            with self.db.connect() as con:
                if p['parent_id']:
                    # Deleting a variant must never restore money already spent under the shared cap.
                    con.execute('UPDATE clone_projects SET spent_usd=spent_usd+? WHERE id=?',(p['spent_usd'],p['parent_id']))
                con.execute('DELETE FROM clone_projects WHERE id=?',(id,))

    def save_brief(self,id,value):
        with self.lock:
            p=self.idle(id)
            b=self.check_brief(value,p['source_asset_id'])
            self.update(id,brief=b,budget_usd=b['budget_usd'],treatment=None,script=None,timeline=None,output_asset_id=None,
                        status='analyzed' if p['analysis'] else 'created')
            return self.get(id)

    def validate_plan(self,p,treatment=None,script=None):
        if not p['analysis']: raise ValueError('请先解读参考视频')
        provider=self.provider(p,'video_gen',True)
        t=validate_treatment(treatment or p['treatment'],p['analysis'],p['brief']['reference_assets'],
                             p['brief']['target_seconds'],provider['extra'])
        s=validate_script(script or p['script'],t,p['brief']['forbidden_words'])
        if any(len(shot['reference_assets'])>1 for shot in t['shots']) and not self.provider(p,'image_gen'):
            extra=provider['extra']
            if '{images}' not in json.dumps(extra.get('body_template',{})) and not extra.get('multi_image_field'):
                raise ValueError('多个参考主体需要 image_gen 合成首帧，或在视频模板中配置 {images} / multi_image_field')
        return t,s

    def save_document(self,id,kind,value):
        with self.lock:
            p=self.idle(id)
            t,s=self.validate_plan(p,**{kind:value})
            self.update(id,treatment=t,script=s,timeline=None,output_asset_id=None,status='planned')
            return self.get(id)

    def media(self,id):
        self.get(id)
        rows=self.db.all('SELECT * FROM generated_media WHERE project_id=? ORDER BY created_at,rowid',(id,))
        for row in rows:
            row.pop('job',None) # Signed remote URLs stay internal.
            row['metadata']=json.loads(row['metadata'])
        return rows

    def current_media(self,p):
        keys={o['cache_key'] for o in self.generator.operations(p) if o['kind'] in ('video','speech')}
        return [m for m in self.media(p['id']) if m['cache_key'] in keys and m['path'] and Path(m['path']).is_file()]

    def fingerprint(self,p):
        configs={kind:self.provider(p,kind) for kind in p['brief']['providers']}
        return hashlib.sha256(json.dumps([p['brief'],p['treatment'],p['script'],configs],sort_keys=True).encode()).hexdigest()

    def cost(self,id,shot_id=None,force=False):
        p=self.get(id)
        self.validate_plan(p)
        return self.generator.estimate(p,shot_id,force)

    def enqueue(self,queue,id,operation,options=None):
        options=options or {}
        with self.lock:
            p=self.idle(id)
            if operation not in OPERATIONS: raise ValueError('未知操作')
            if operation=='plan' and not p['analysis']: raise ValueError('请先解读参考视频')
            if operation in ('analyze','plan'):
                quote=self.generator.stage_estimate(p,operation)
                if options.get('dry_run',True): return dict(dry_run=True,cost=quote)
                self.generator.reserve_quote(p,quote,options.get('confirm_budget',False))
            if operation in ('generate','regenerate','assemble','render'): self.validate_plan(p)
            if operation=='render' and not p['timeline']: raise ValueError('请先组装时间线')
            if operation=='regenerate':
                if options.get('shot_id') not in {s['id'] for s in p['treatment']['shots']}: raise ValueError('镜头不存在')
                if options.get('replacement_asset_id'):
                    asset=self.asset(options['replacement_asset_id'])
                    if asset['id']==p['source_asset_id'] or asset['type'] not in ('image','video'): raise ValueError('替换素材无效')
            if operation in ('generate','regenerate'):
                shot_id=options.get('shot_id')
                cost=self.generator.estimate(p,shot_id,options.get('force',False))
                if options.get('dry_run',True): return dict(dry_run=True,cost=cost,shots=p['treatment']['shots'])
                if options.get('fallback') or options.get('replacement_asset_id'):
                    self.db.execute('INSERT OR REPLACE INTO clone_reservations VALUES (?,?,0,0)',(id,p['parent_id'] or id))
                else: self.generator.reserve(p,options.get('confirm_budget',False),shot_id,options.get('force',False))
            try:
                self.active.add(id)
                task=queue.create('clone_'+operation,dict(project_id=id,**options,config_fingerprint=self.fingerprint(p)),max_retries=0)
            except BaseException:
                self.active.discard(id)
                self.db.execute('DELETE FROM clone_reservations WHERE project_id=?',(id,))
                raise
            return task

    def retry_task(self,queue,task_id):
        task=self.db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
        if task['kind'] in ('clone_generate','clone_regenerate','clone_analyze','clone_plan'):
            raise ValueError('付费任务请从项目生成入口重试，以重新核对预算和缓存')
        with self.lock:
            self.idle(json.loads(task['payload'])['project_id'])
            return queue.retry(task_id)

    def task_finished(self,task):
        if not task or not task['kind'].startswith('clone_'): return
        id=json.loads(task['payload'])['project_id']
        with self.lock:
            self.db.execute('DELETE FROM clone_reservations WHERE project_id=?',(id,))
            self.active.discard(id)
            if task['status'] in ('failed','cancelled'):
                current=self.db.one('SELECT status FROM clone_projects WHERE id=?',(id,))
                self.update(id,status='needs_attention' if task['status']=='cancelled' or (current and current['status']=='needs_attention') else 'failed')

    def variants(self,id,count,dimensions):
        with self.lock:
            p=self.idle(id)
            if p['parent_id']: raise ValueError('请从根项目展开变体')
            values=expand(p['brief'],count,dimensions)
            output=[]
            with self.db.connect() as con:
                for i,(brief,params) in enumerate(values):
                    child=uuid4().hex
                    con.execute('''INSERT INTO clone_projects
                        (id,name,source_url,source_asset_id,hook_seconds,mode,brief,analysis,status,budget_usd,parent_id,variant_params,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(child,p['name']+f' · {i+1}',p['source_url'],p['source_asset_id'],
                        p['hook_seconds'],p['mode'],json.dumps(brief,ensure_ascii=False),json.dumps(p['analysis']) if p['analysis'] else None,
                        'analyzed' if p['analysis'] else 'created',p['budget_usd'],id,json.dumps(params,ensure_ascii=False),now(),now()))
                    output.append(child)
            return [self.get(child) for child in output]

    def batch(self,queue,id,operation,options):
        if operation not in ('plan','generate'): raise ValueError('批量仅支持规划或生成')
        with self.lock:
            root=self.idle(id)
            if root['parent_id']: raise ValueError('请从根项目批量生成')
            children=[self.idle(r['id']) for r in self.db.all('SELECT id FROM clone_projects WHERE parent_id=? ORDER BY created_at',(id,))]
            if not children: raise ValueError('请先展开变体')
            quotes=[]
            for child in children:
                if not child['analysis']: raise ValueError('请先分析根项目，再展开共享解读的变体')
                if operation=='generate': self.validate_plan(child)
                quotes.append(self.generator.stage_estimate(child,'plan') if operation=='plan' else self.generator.estimate(child))
            amount=sum(q['known_total_usd'] for q in quotes)
            unknown=any(q['unknown'] for q in quotes)
            quote=dict(items=[dict(item,shot_id=c['name']+'/'+item['shot_id']) for c,q in zip(children,quotes) for item in q['items']],
                       known_total_usd=amount,total_usd=None if unknown else amount,unknown=unknown,
                       spent_usd=quotes[0]['spent_usd'],reserved_usd=quotes[0]['reserved_usd'],
                       over_budget=root['budget_usd'] is not None and quotes[0]['spent_usd']+quotes[0]['reserved_usd']+amount>root['budget_usd'],
                       note='全部变体合计保守估算；重复素材可能命中共享缓存，生成后自动组装并渲染。')
            if options.get('dry_run',True): return dict(dry_run=True,cost=quote)
            if (unknown or amount>0 or quote['over_budget']) and not options.get('confirm_budget'):
                raise ValueError('请先确认所有变体的合计费用与可能超支')
            tasks=[]
            for child in children:
                tasks.append(self.enqueue(queue,child['id'],operation,dict(dry_run=False,confirm_budget=options.get('confirm_budget',False),
                                                                          auto_assemble_render=operation=='generate')))
            return dict(tasks=tasks,cost=quote)

    def handler(self,operation):
        def handle(payload,progress,cancelled):
            id=payload['project_id']
            p=self.get(id)
            if cancelled(): raise Cancelled()
            if payload.get('config_fingerprint')!=self.fingerprint(p):
                self.update(id,status='needs_attention')
                raise ValueError('配置已变化，请重新估算并确认后运行')
            self.update(id,status={'analyze':'analyzing','plan':'planning','generate':'generating',
                'regenerate':'generating','assemble':'assembling','render':'rendering'}[operation])
            if operation in ('analyze','plan'):
                reservation=self.db.one('SELECT * FROM clone_reservations WHERE project_id=?',(id,))
                if not reservation:
                    self.update(id,status='needs_attention')
                    raise ValueError('分析/规划费用预留不存在，请重新确认')
                amount=reservation['amount']
                self.generator.charge(p,{'cost_estimate':amount})
                self.db.execute('''INSERT INTO generated_media
                    (id,project_id,kind,provider,prompt,cost_estimate,status,cache_key,created_at)
                    VALUES (?,?,?,?,?,?,'estimated',?,?)''',(uuid4().hex,id,operation,self.provider(p,'llm',True)['id'],
                    '分析/规划最大请求次数保守费用预留',None if reservation['unknown'] else amount,uuid4().hex,now()))
            if operation=='analyze':
                if not p['source_asset_id']:
                    asset=Downloader(self.db,self.settings.data_dir)(dict(url=p['source_url'],
                        seconds=p['hook_seconds'] if p['mode']=='hook' else 3600),lambda n:progress(n*.15),cancelled)
                    self.update(id,source_asset_id=asset['id'])
                    p=self.get(id)
                result=analyze(self.db,self.asset(p['source_asset_id'])['path'],p['hook_seconds'],p['mode'],
                    p['brief']['language'],self.adapter(p,'llm',cancelled),self.adapter(p,'asr',cancelled,False),self.directory,
                    json.dumps([self.provider(p,'llm'),self.provider(p,'asr')],sort_keys=True),progress,cancelled)
                self.update(id,analysis=result,treatment=None,script=None,timeline=None,output_asset_id=None,status='analyzed')
            elif operation=='plan':
                extra=self.provider(p,'video_gen',True)['extra']
                t=plan_treatment(self.adapter(p,'llm',cancelled),p['analysis'],p['brief'],extra,p['variant_params'],cancelled)
                progress(50)
                s=plan_script(self.adapter(p,'llm',cancelled),t,p['brief'],p['variant_params'],cancelled)
                self.validate_plan(p,t,s)
                self.update(id,treatment=t,script=s,timeline=None,output_asset_id=None,status='planned')
            elif operation in ('generate','regenerate'):
                if not self.db.one('SELECT project_id FROM clone_reservations WHERE project_id=?',(id,)):
                    self.update(id,status='needs_attention')
                    raise ValueError('生成费用预留不存在，请重新确认预算')
                self.generator.run(p,progress,cancelled,payload.get('shot_id'),payload.get('force',False),
                                   payload.get('fallback',False),payload.get('replacement_asset_id'))
                self.update(id,timeline=None,output_asset_id=None,status='planned')
                if payload.get('auto_assemble_render'):
                    if cancelled(): raise Cancelled()
                    timeline=build_timeline(p['treatment'],p['script'],self.current_media(p),transition=p['brief']['transition'])
                    self.update(id,timeline=timeline,status='assembled')
                    target=self.directory/(uuid4().hex+'.mp4')
                    render(timeline,target,p['brief'],self.db,progress,cancelled)
                    if cancelled():
                        target.unlink(missing_ok=True)
                        raise Cancelled()
                    asset=self.db.add_asset(target,probe(target))
                    self.update(id,output_asset_id=asset['id'],status='rendered')
            elif operation=='assemble':
                timeline=build_timeline(p['treatment'],p['script'],self.current_media(p),transition=p['brief']['transition'])
                self.update(id,timeline=timeline,output_asset_id=None,status='assembled')
            else:
                target=self.directory/(uuid4().hex+'.mp4')
                render(p['timeline'],target,p['brief'],self.db,progress,cancelled)
                if cancelled():
                    target.unlink(missing_ok=True)
                    raise Cancelled()
                asset=self.db.add_asset(target,probe(target))
                self.update(id,output_asset_id=asset['id'],status='rendered')
            if cancelled(): raise Cancelled()
        return handle
