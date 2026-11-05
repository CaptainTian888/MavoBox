import React, {useEffect, useState} from 'react';

type Asset={id:string;path:string;type:string};
type Provider={id:string;name:string;type:string};
type Brief={product_name:string;selling_points:string[];evidence:string[];forbidden_words:string[];reference_assets:string[];
  requirements:string;language:string;platform:string;target_seconds:number;style:string;variant_count:number;
  dimensions:Record<string,string[]>;budget_usd:number|null;providers:Record<string,string>;voice:string;
  bgm_asset_id:string|null;concat_tail_asset_id:string|null;font_path:string;transition:boolean};
type Analysis={summary:string;why_it_works:string;hook_type:string;transcript:{word:string;start:number;end:number}[];
  beats:{start:number;end:number;role:string;what_happens:string;speaker:string;on_screen_text:string[]}[];open_questions:string[]};
type Shot={id:string;kind:string;duration:number;prompt:string;audio_ref:string;word_start:number;word_end:number};
type Project={id:string;name:string;status:string;source_asset_id:string|null;source_url:string|null;mode:string;hook_seconds:number;
  brief:Brief;analysis:Analysis|null;treatment:{shots:Shot[]}|null;script:unknown;timeline:unknown;parent_id:string|null;output_asset_id:string|null};
type Media={id:string;shot_id:string;kind:string;status:string;attempt:number;metadata:{estimated?:boolean;fallback?:boolean;error?:string;progress?:number}};
type Cost={quote_usd:number;known_total_usd:number;total_usd:number|null;unknown:boolean;over_budget:boolean;spent_usd:number;reserved_usd:number;note:string;
  items:{kind:string;shot_id:string;cached:boolean;cost_estimate:number|null}[]};
type Task={id:string;kind:string;status:string;progress:number};
const initial:Brief={product_name:'',selling_points:[],evidence:[],forbidden_words:[],reference_assets:[],requirements:'',language:'zh',
  platform:'TikTok',target_seconds:5,style:'',variant_count:1,dimensions:{},budget_usd:null,providers:{},voice:'alloy',
  bgm_asset_id:null,concat_tail_asset_id:null,font_path:'',transition:false};

async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok){const e=await r.json().catch(()=>({}));throw new Error(typeof e.detail==='string'?e.detail:'请求失败');}
  return r.status===204?undefined as T:r.json();
}
const lines=(s:string)=>s.split('\n').map(x=>x.trim()).filter(Boolean);

export function Clone(){
  const [projects,setProjects]=useState<Project[]>([]),[selected,setSelected]=useState('');
  const [assets,setAssets]=useState<Asset[]>([]),[providers,setProviders]=useState<Provider[]>([]);
  const [tasks,setTasks]=useState<Task[]>([]),[media,setMedia]=useState<Media[]>([]);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const [name,setName]=useState('新钩子'),[url,setUrl]=useState(''),[source,setSource]=useState(''),[seconds,setSeconds]=useState(5),[mode,setMode]=useState('hook');
  const [brief,setBrief]=useState<Brief>(initial),[script,setScript]=useState(''),[treatment,setTreatment]=useState('');
  const [cost,setCost]=useState<Cost|null>(null),[confirmed,setConfirmed]=useState(false),[dimensions,setDimensions]=useState('{"style":["自然讲述","快速演示"]}');
  const [replacement,setReplacement]=useState<Record<string,string>>({});
  const [regenCosts,setRegenCosts]=useState<Record<string,Cost>>({});
  const [stageCosts,setStageCosts]=useState<Record<string,Cost>>({});
  const [forceAll,setForceAll]=useState(false);
  const p=projects.find(x=>x.id===selected);
  const refresh=async()=>{
    const [ps,as,pr,ts]=await Promise.all([api<Project[]>('/clone/projects'),api<Asset[]>('/assets'),api<Provider[]>('/providers'),api<Task[]>('/tasks')]);
    setProjects(ps);setAssets(as);setProviders(pr);setTasks(ts.filter(t=>t.kind.startsWith('clone_')));
    if(selected)setMedia(await api<Media[]>(`/clone/projects/${selected}/media`));
  };
  useEffect(()=>{let live=true;const poll=()=>{if(live)void refresh().catch(e=>{if(live)setError(String(e.message));});};
    poll();const timer=window.setInterval(poll,2000);return()=>{live=false;clearInterval(timer);};},[selected]);
  function choose(project:Project){setSelected(project.id);setBrief(project.brief);setScript(JSON.stringify(project.script,null,2));
    setTreatment(JSON.stringify(project.treatment,null,2));setCost(null);setConfirmed(false);setRegenCosts({});setStageCosts({});setForceAll(false);}
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();await refresh();}catch(e){setError(e instanceof Error?e.message:'操作失败');}finally{setBusy(false);}}
  const endpoint=(action:string)=>`/clone/projects/${selected}/${action}`;
  async function action(op:string,body?:unknown){await api(endpoint(op),'POST',body);setNotice('已提交，请在进度区查看');}
  async function estimateStage(op:string){const result=await api<{cost:Cost}>(endpoint(op),'POST',{});setStageCosts(c=>({...c,[op]:result.cost}));}
  const stageConfirm=(op:string)=>stageCosts[op]&&<div><p>{stageCosts[op].note}</p><p>费用：{stageCosts[op].unknown?'未知':`$${stageCosts[op].known_total_usd.toFixed(4)}`}{stageCosts[op].over_budget?'（超过共享预算）':''}</p><button disabled={busy} onClick={()=>void run(async()=>{await action(op,{dry_run:false,confirm_budget:true});setStageCosts({});})}>确认费用并{op==='analyze'?'解读':op==='batch/generate'?'批量生成成片':'生成方案'}（允许显示的未知费用或超支）</button></div>;
  async function upload(file:File,kind:string){
    const r=await fetch(`/api/clone/assets/upload?kind=${kind}`,{method:'POST',body:file,headers:{'Content-Type':'application/octet-stream'}});
    if(!r.ok)throw new Error('素材上传失败，请检查格式与大小');
    const a:Asset=await r.json();
    if(kind==='video')setSource(a.id);else setBrief(b=>({...b,reference_assets:[...b.reference_assets,a.id]}));
  }
  const change=<K extends keyof Brief>(key:K,value:Brief[K])=>{setBrief(b=>({...b,[key]:value}));setConfirmed(false);setCost(null);};
  return <section className="clone-page">
    <h2>爆款克隆 · 钩子复刻</h2>
    <p className="clone-policy" role="note">本工具默认不复用参考视频的画面与音乐，仅学习结构。请确认人物肖像、产品素材及音乐的使用权，并遵守版权与平台规则。AI 生成内容发布时应勾选 AI 标注。</p>
    {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    <label>项目<select value={selected} onChange={e=>{const next=projects.find(x=>x.id===e.target.value);if(next)choose(next);else setSelected('');}}>
      <option value="">新建项目</option>{projects.map(x=><option key={x.id} value={x.id}>{x.parent_id?'↳ ':''}{x.name} · {x.status}</option>)}</select></label>
    {!selected&&<article><h3>① 新建</h3><form onSubmit={e=>{e.preventDefault();void run(async()=>{
      const next=await api<Project>('/clone/projects','POST',{name,source_url:source?null:url,source_asset_id:source||null,hook_seconds:seconds,mode,brief:{...brief,target_seconds:seconds}});
      choose(next);
    });}}>
      <label>项目名<input required value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>参考 TikTok 链接<input type="url" value={url} onChange={e=>{setUrl(e.target.value);setSource('');}}/></label>
      <label>或选择本地视频<select value={source} onChange={e=>{setSource(e.target.value);setUrl('');}}><option value="">请选择</option>{assets.filter(a=>a.type==='video').map(a=><option key={a.id} value={a.id}>{a.path}</option>)}</select></label>
      <label>导入视频文件<input type="file" accept="video/*" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f)void run(()=>upload(f,'video'));}}/></label>
      <label>范围<select value={mode} onChange={e=>setMode(e.target.value)}><option value="hook">只复刻钩子</option><option value="full">整条克隆</option></select></label>
      <label>{mode==='hook'?'钩子秒数 / 目标时长':'初始目标时长（可在 Brief 中修改）'}<input type="number" min="0.5" max="3600" step="0.5" value={seconds} onChange={e=>setSeconds(+e.target.value)}/></label>
      <button disabled={busy||(!source&&!url)}>创建</button>
    </form></article>}
    {p&&<>
      <p>状态：<strong>{p.status}</strong></p>
      <article><h3>② 参考解读</h3><p>请先在下方 Brief 选择并保存 LLM / ASR。此步骤会调用配置的分析 API，供应商可能计费。</p>
        <button disabled={busy} onClick={()=>void run(()=>estimateStage('analyze'))}>解读 / 重新解读 · 先估算</button>{stageConfirm('analyze')}
        {p.analysis&&<><h4>{p.analysis.hook_type} · {p.analysis.summary}</h4><p>为什么有效：{p.analysis.why_it_works}</p>
          <ol>{p.analysis.beats.map((b,i)=><li key={i}><strong>{b.start.toFixed(2)}–{b.end.toFixed(2)}s · {b.role}</strong> {b.speaker}：{b.what_happens}<p>{b.on_screen_text.join(' / ')}</p></li>)}</ol>
          <p>{p.analysis.transcript.map((w,i)=><span key={i} title={`${w.start}–${w.end}s`}>{w.word} </span>)}</p>
          {p.analysis.open_questions.map((q,i)=><p key={i}>{q}</p>)}</>}
      </article>
      <article><h3>③ Brief · 用户意图</h3><form onSubmit={e=>{e.preventDefault();void run(async()=>{const next=await api<Project>(endpoint('brief'),'PUT',brief);choose(next);setNotice('Brief 已保存；旧方案已失效');});}}>
        <label>产品名称<input value={brief.product_name} onChange={e=>change('product_name',e.target.value)}/></label>
        {(['selling_points','evidence','forbidden_words'] as const).map(k=><label key={k}>{{selling_points:'卖点（每行一个）',evidence:'真实证据 / 功能（每行一个）',forbidden_words:'禁用词（每行一个）'}[k]}<textarea value={brief[k].join('\n')} onChange={e=>change(k,e.target.value.split('\n'))}/></label>)}
        <label>简要要求<textarea value={brief.requirements} onChange={e=>change('requirements',e.target.value)}/></label>
        <label>人物 / 产品参考图（多选）<select multiple value={brief.reference_assets} onChange={e=>change('reference_assets',Array.from(e.target.selectedOptions,o=>o.value))}>{assets.filter(a=>a.type==='image').map(a=><option key={a.id} value={a.id}>{a.path}</option>)}</select></label>
        <label>上传参考图<input type="file" accept="image/*" disabled={busy} onChange={e=>{const f=e.target.files?.[0];if(f)void run(()=>upload(f,'image'));}}/></label>
        <label>目标语言<input value={brief.language} onChange={e=>change('language',e.target.value)}/></label>
        <label>平台<input value={brief.platform} onChange={e=>change('platform',e.target.value)}/></label>
        <label>目标时长（须匹配模型可生成时长之和）<input type="number" min="0.5" max="3600" step="0.5" value={brief.target_seconds} onChange={e=>change('target_seconds',+e.target.value)}/></label>
        <label>风格<input value={brief.style} onChange={e=>change('style',e.target.value)}/></label>
        <label>配音 voice<input value={brief.voice} onChange={e=>change('voice',e.target.value)}/></label>
        <label>变体数<input type="number" min="1" max="20" value={brief.variant_count} onChange={e=>change('variant_count',+e.target.value)}/></label>
        <label>共享预算上限（美元，空白表示未设）<input type="number" min="0" step="0.01" value={brief.budget_usd??''} onChange={e=>change('budget_usd',e.target.value===''?null:+e.target.value)}/></label>
        {['llm','video_gen','tts','asr','image_gen'].map(kind=><label key={kind}>{kind}{['asr','image_gen'].includes(kind)?'（可选）':''}<select value={brief.providers[kind]||''} onChange={e=>change('providers',{...brief.providers,[kind]:e.target.value})}><option value="">未选择</option>{providers.filter(x=>x.type===kind).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>)}
        {(['bgm_asset_id','concat_tail_asset_id'] as const).map(k=><label key={k}>{k==='bgm_asset_id'?'用户 BGM':'用户后半段素材'}<select value={brief[k]||''} onChange={e=>change(k,e.target.value||null)}><option value="">不使用</option>{assets.filter(a=>a.type===(k==='bgm_asset_id'?'audio':'video')&&a.id!==p.source_asset_id).map(a=><option key={a.id} value={a.id}>{a.path}</option>)}</select></label>)}
        <label><input type="checkbox" checked={brief.transition} onChange={e=>change('transition',e.target.checked)}/>镜头淡入</label>
        <button disabled={busy}>保存 Brief</button>
      </form></article>
      <article><h3>④ 方案 · Script 与镜头</h3>
        <button disabled={busy||!p.analysis} onClick={()=>void run(()=>estimateStage('plan'))}>生成方案 · 先估算 LLM 费用</button>{stageConfirm('plan')}
        {p.treatment&&<><p>当前方案已生成。编辑前载入最新版本；保存会重新校验引用及模型时长。</p>
          <button onClick={()=>{setScript(JSON.stringify(p.script,null,2));setTreatment(JSON.stringify(p.treatment,null,2));}}>载入当前方案供编辑</button>
          <label>Script JSON（display 为字幕，spoken 为读音）<textarea rows={14} value={script} onChange={e=>setScript(e.target.value)}/></label>
          <button disabled={busy} onClick={()=>void run(async()=>{await api(endpoint('script'),'PUT',JSON.parse(script));setCost(null);setConfirmed(false);})}>保存台词</button>
          <details><summary>Treatment JSON · 编辑镜头与词区间</summary><textarea aria-label="Treatment JSON" rows={14} value={treatment} onChange={e=>setTreatment(e.target.value)}/><button disabled={busy} onClick={()=>void run(async()=>{await api(endpoint('treatment'),'PUT',JSON.parse(treatment));setCost(null);setConfirmed(false);})}>保存镜头方案</button></details>
          <label><input type="checkbox" checked={forceAll} onChange={e=>{setForceAll(e.target.checked);setCost(null);setConfirmed(false);}}/>强制重生成全部素材（可能重复收费；仅在核查过未知提交结果后使用）</label>
          <button disabled={busy} onClick={()=>void run(async()=>{const result=await api<{cost:Cost}>(endpoint('generate'),'POST',{force:forceAll});setCost(result.cost);setConfirmed(false);})}>估算费用（dry run）</button>
          {cost&&<><p>本次：{cost.unknown?'未知（已知部分 $'+cost.known_total_usd.toFixed(4)+'）':'$'+cost.total_usd?.toFixed(4)}；共享已计费估算 ${cost.spent_usd.toFixed(4)}；预留 ${cost.reserved_usd.toFixed(4)}</p>
            {cost.over_budget&&<p className="error">将超过预算上限。确认意味着允许此次超支。</p>}<p>{cost.note}</p>
            <ul>{cost.items.map((x,i)=><li key={i}>{x.kind} / {x.shot_id}：{x.cached?'命中缓存':x.cost_estimate===null?'单价未知':'$'+x.cost_estimate.toFixed(4)}</li>)}</ul>
            <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我确认当前估算；如含未知价格或超预算，我同意继续调用生成 API</label>
            <button disabled={busy||(!confirmed&&(cost.unknown||cost.known_total_usd>0))} onClick={()=>void run(()=>action('generate',{dry_run:false,confirm_budget:confirmed,confirm_amount:cost.quote_usd,force:forceAll}))}>确认预算并生成</button></>}
        </>}
      </article>
      <article><h3>⑤ 生成进度</h3>
        {tasks.filter(t=>['pending','running'].includes(t.status)).map(t=><div key={t.id}>{t.kind} · {t.status}<progress value={t.progress} max={100}/><button onClick={()=>void run(async()=>{await api(`/tasks/${t.id}/cancel`,'POST');})}>取消</button></div>)}
        {p.treatment?.shots.map(shot=>{const m=media.filter(x=>x.kind==='video'&&x.shot_id===shot.id).at(-1);return <article key={shot.id}>
          <strong>{shot.id} · {shot.kind} · {shot.duration}s</strong><p>{shot.prompt}</p><p>绑定 {shot.audio_ref} 的词 {shot.word_start}–{shot.word_end}</p>
          <p>{m?`${m.status} · 第 ${m.attempt} 次`:'尚未生成'} {m?.metadata.fallback?'（静态图兜底）':''}</p>
          {m?.metadata.error&&<p className="error">{m.metadata.error}</p>}{m?.metadata.progress!==undefined&&<progress value={m.metadata.progress} max={100}/>}
          {m?.status==='succeeded'&&<video controls preload="metadata" src={`/api/clone/media/${m.id}`}/>}
          <button disabled={busy} onClick={()=>void run(async()=>{const c=await api<Cost>(endpoint(`cost?shot_id=${encodeURIComponent(shot.id)}&force=true`));setRegenCosts({...regenCosts,[shot.id]:c});})}>重生成费用</button>
          {regenCosts[shot.id]&&<button disabled={busy} onClick={()=>void run(async()=>{await action(`shots/${shot.id}/regenerate`,{dry_run:false,force:true,confirm_budget:true,confirm_amount:regenCosts[shot.id].quote_usd});setRegenCosts({});})}>确认重生成：{regenCosts[shot.id].unknown?'价格未知':`$${regenCosts[shot.id].known_total_usd.toFixed(4)}`}{regenCosts[shot.id].over_budget?'（超预算）':''}</button>}
          <button disabled={busy} onClick={()=>void run(()=>action(`shots/${shot.id}/regenerate`,{dry_run:false,fallback:true,force:true}))}>用用户参考图静态兜底</button>
          <label>替换素材<select value={replacement[shot.id]||''} onChange={e=>setReplacement({...replacement,[shot.id]:e.target.value})}><option value="">选择用户图片 / 视频</option>{assets.filter(a=>['image','video'].includes(a.type)&&a.id!==p.source_asset_id).map(a=><option key={a.id} value={a.id}>{a.path}</option>)}</select></label>
          <button disabled={busy||!replacement[shot.id]} onClick={()=>void run(()=>action(`shots/${shot.id}/regenerate`,{dry_run:false,replacement_asset_id:replacement[shot.id]}))}>替换镜头</button>
        </article>;})}
        {media.filter(m=>m.kind==='speech'&&m.status==='succeeded').map(m=><p key={m.id}>{m.shot_id} 配音 {m.metadata.estimated?'· 词时间为估算':''}<audio controls src={`/api/clone/media/${m.id}`}/></p>)}
      </article>
      <article><h3>⑥ 成片与变体</h3><button disabled={busy||!p.treatment} onClick={()=>void run(()=>action('assemble'))}>组装词级时间线</button><button disabled={busy||!p.timeline} onClick={()=>void run(()=>action('render'))}>渲染 9:16 成片</button>
        {p.output_asset_id&&<video controls src={`/api/clone/output/${p.output_asset_id}`}/>}
        {!p.parent_id&&<><label>变体维度 JSON（person 使用参考图 asset ID；product / dialogue / style / cta 使用文本候选）<textarea rows={5} value={dimensions} onChange={e=>setDimensions(e.target.value)}/></label>
          <button disabled={busy} onClick={()=>void run(async()=>{await action('variants',{count:brief.variant_count,dimensions:JSON.parse(dimensions)});})}>展开 {brief.variant_count} 个变体</button>
          <p>变体共享解读缓存、并发队列与根项目预算。可逐个编辑，也可一次生成全部变体。</p>
          <button disabled={busy} onClick={()=>void run(()=>estimateStage('batch/plan'))}>全部变体 · 规划费用</button>{stageConfirm('batch/plan')}
          <button disabled={busy} onClick={()=>void run(()=>estimateStage('batch/generate'))}>全部变体 · 成片费用</button>{stageConfirm('batch/generate')}</>}
        {projects.filter(x=>x.parent_id===(p.parent_id||p.id)).map(x=><p key={x.id}><button onClick={()=>choose(x)}>{x.name} · {x.status}</button></p>)}
        <button disabled={busy} onClick={()=>void run(async()=>{await api(`/clone/projects/${selected}`,'DELETE');setSelected('');})}>删除项目记录（保留付费素材缓存）</button>
      </article>
    </>}
  </section>;
}
