import {useEffect, useState} from 'react';

type Asset = {id: string; path: string; type: string; duration: number};
type Provider = {id: string; name: string; type: string};
type Caption = {text: string; position: string; start_offset: number; end_offset: number};
type Clip = {shot_id: string; asset_id: string; src_start: number; src_end: number; speed: number; transition_in: {type: string; duration: number}; caption: Caption | null; mute: boolean};
type Timeline = {version: number; width: number; height: number; fps: number; clips: Clip[]; bgm: unknown; global_caption_style: unknown; notes: string; fallback: boolean};
type Settings = {provider_id: string; bgm_path: string; captions: boolean; transition: string; threshold: number; segment_seconds: number; allow_repeat: boolean; fit: string; font_path: string};
type Form = {name: string; brief_text: string; target_seconds: number; width: number; height: number; asset_ids: string[]; settings: Settings};
type Project = Form & {id: string; status: string; timeline: Timeline | null; output_asset_id: string | null};
type Shot = {id: string; frame_url: string; description: string; tagged: boolean};
type Task = {id: string; status: string; progress: number; error: string | null};
const initial = (): Form => ({name: '新混剪项目', brief_text: '', target_seconds: 30, width: 1080, height: 1920,
  asset_ids: [], settings: {provider_id: '', bgm_path: '', captions: false, transition: 'none', threshold: 27, segment_seconds: 4, allow_repeat: false, fit: 'crop', font_path: ''}});

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!r.ok) {const d = await r.json().catch(() => ({})); throw new Error(typeof d.detail === 'string' ? d.detail : '请求失败');}
  return r.status === 204 ? undefined as T : r.json();
}

export function Remix() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [form, setForm] = useState<Form>(initial);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [dirty, setDirty] = useState(false);
  const [folder, setFolder] = useState('');
  const [path, setPath] = useState('');
  const [recursive, setRecursive] = useState(false);
  const [tag, setTag] = useState(true);
  const [estimate, setEstimate] = useState('');
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const root = project ? `/remix/projects/${project.id}` : '';
  const working = !!task && ['pending', 'running'].includes(task.status);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try {await action();} catch (e) {setError(e instanceof Error ? e.message : '操作失败');} finally {setBusy(false);}
  }
  async function library() {
    const [a, p, list] = await Promise.all([api<Asset[]>('/assets'), api<Provider[]>('/providers'), api<Project[]>('/remix/projects')]);
    setAssets(a.filter(v => v.type === 'video')); setProviders(p.filter(v => v.type === 'llm')); setProjects(list);
  }
  async function load(id: string) {
    const p = await api<Project>(`/remix/projects/${id}`);
    setProject(p); setForm({name: p.name, brief_text: p.brief_text, target_seconds: p.target_seconds, width: p.width, height: p.height, asset_ids: p.asset_ids, settings: p.settings});
    setTimeline(p.timeline); setDirty(false);
    setShots(await api<Shot[]>(`/remix/projects/${id}/shots`));
    setEstimate((await api<{message: string}>(`/remix/projects/${id}/estimate`)).message);
  }
  useEffect(() => {void library().catch(() => setError('无法读取混剪项目'));}, []);
  useEffect(() => {
    if (!task || !working) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api<Task>(`/tasks/${task.id}`).then(async t => {
        if (!active) return;
        setTask(t);
        if (!['pending', 'running'].includes(t.status) && project) {
          await load(project.id); await library();
          if (t.error) setError(t.error);
        }
      }).catch(() => {if (active) setError('无法读取任务状态');});
    }, 1000);
    return () => {active = false; window.clearInterval(timer);};
  }, [task?.id, working, project?.id]);
  function setting<K extends keyof Settings>(key: K, value: Settings[K]) {setForm({...form, settings: {...form.settings, [key]: value}});}
  function edit(index: number, patch: Partial<Clip>) {
    if (!timeline) return;
    setTimeline({...timeline, clips: timeline.clips.map((c, i) => i === index ? {...c, ...patch} : c)}); setDirty(true);
  }
  function move(index: number, delta: number) {
    if (!timeline || index + delta < 0 || index + delta >= timeline.clips.length) return;
    const clips = [...timeline.clips]; [clips[index], clips[index + delta]] = [clips[index + delta], clips[index]];
    setTimeline({...timeline, clips}); setDirty(true);
  }
  const disabled = busy || working;
  return <section><h2>智能混剪</h2>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="filters"><label>已有项目<select disabled={disabled || dirty} value={project?.id ?? ''} onChange={e => void run(async () => {setTask(null); if (e.target.value) await load(e.target.value); else {setProject(null); setForm(initial()); setTimeline(null); setShots([]); setEstimate('');}})}><option value="">创建新项目</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name} · {p.status}</option>)}</select></label>
      {project && <button disabled={disabled} onClick={() => void run(async () => {await api(root, 'DELETE'); setProject(null); setForm(initial()); setTimeline(null); setShots([]); setTask(null); await library();})}>删除项目（保留素材）</button>}</div>
    <fieldset disabled={disabled}><legend>素材</legend>
      <form onSubmit={e => {e.preventDefault(); void run(async () => {await api('/assets', 'POST', {path}); setPath(''); await library();});}}><label>本地视频绝对路径<input value={path} onChange={e => setPath(e.target.value)} required/></label><button>登记文件</button></form>
      <form onSubmit={e => {e.preventDefault(); void run(async () => {const result = await api<{assets: Asset[]; skipped: number}>('/assets/scan', 'POST', {folder, recursive}); await library(); setForm({...form, asset_ids: [...new Set([...form.asset_ids, ...result.assets.map(a => a.id)])]}); setNotice(`已登记 ${result.assets.length} 个素材，跳过 ${result.skipped} 个文件`);});}}><label>文件夹绝对路径<input value={folder} onChange={e => setFolder(e.target.value)} required/></label><label className="check"><input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)}/>递归扫描</label><button>扫描并选中</button></form>
      <div className="remix-assets">{assets.map(a => <label className="check" key={a.id}><input type="checkbox" checked={form.asset_ids.includes(a.id)} onChange={e => setForm({...form, asset_ids: e.target.checked ? [...form.asset_ids, a.id] : form.asset_ids.filter(id => id !== a.id)})}/>{a.path} · {a.duration.toFixed(1)} 秒</label>)}</div>
    </fieldset>
    <form onSubmit={e => {e.preventDefault(); void run(async () => {const p = await api<Project>(project ? root : '/remix/projects', project ? 'PUT' : 'POST', form); await load(p.id); await library(); setTask(null); setNotice('项目已保存；修改项目设置会清除原时间线与成片关联');});}}>
      <fieldset disabled={disabled} className="remix-settings"><legend>剪辑设置</legend>
        <label>名称<input required value={form.name} onChange={e => setForm({...form, name: e.target.value})}/></label>
        <label>剪辑要求<textarea value={form.brief_text} onChange={e => setForm({...form, brief_text: e.target.value})}/></label>
        <label>LLM Provider<select value={form.settings.provider_id} onChange={e => setting('provider_id', e.target.value)}><option value="">请选择（仅切镜头时可不选）</option>{providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>目标秒数<input type="number" min="0.5" max="3600" step="0.1" required value={form.target_seconds} onChange={e => setForm({...form, target_seconds: Number(e.target.value)})}/></label>
        <label>宽<input type="number" min="16" max="3840" step="2" value={form.width} onChange={e => setForm({...form, width: Number(e.target.value)})}/></label>
        <label>高<input type="number" min="16" max="3840" step="2" value={form.height} onChange={e => setForm({...form, height: Number(e.target.value)})}/></label>
        <label>BGM 本地绝对路径（可选）<input value={form.settings.bgm_path} onChange={e => setting('bgm_path', e.target.value)}/></label>
        <label>画面填充<select value={form.settings.fit} onChange={e => setting('fit', e.target.value)}><option value="crop">居中裁剪</option><option value="blur">模糊背景</option></select></label>
        <label>转场偏好<select value={form.settings.transition} onChange={e => setting('transition', e.target.value)}>{['none', 'fade', 'crossfade', 'slide'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label>均分回退秒数<input type="number" min="0.25" max="300" step="0.25" value={form.settings.segment_seconds} onChange={e => setting('segment_seconds', Number(e.target.value))}/></label>
        <label>切镜阈值<input type="number" min="1" max="255" value={form.settings.threshold} onChange={e => setting('threshold', Number(e.target.value))}/></label>
        <label>字幕字体文件（可选）<input value={form.settings.font_path} onChange={e => setting('font_path', e.target.value)}/></label>
        <label className="check"><input type="checkbox" checked={form.settings.captions} onChange={e => setting('captions', e.target.checked)}/>生成字幕</label>
        <label className="check"><input type="checkbox" checked={form.settings.allow_repeat} onChange={e => setting('allow_repeat', e.target.checked)}/>允许重复片段</label>
        <button>保存项目设置{project ? '（清除时间线）' : ''}</button>
      </fieldset>
    </form>
    {project && <><p>项目状态：{project.status} · {shots.length} 个镜头（已理解 {shots.filter(s => s.tagged).length} 个）</p><p>{estimate}</p><p>分析和编排使用已保存的项目设置；发送图片可能产生模型费用。</p>
      <label className="check"><input disabled={disabled} type="checkbox" checked={tag} onChange={e => setTag(e.target.checked)}/>分析时发送关键帧给模型（关闭则只切镜头）</label>
      <button disabled={disabled || dirty} onClick={() => void run(async () => {setTask(await api<Task>(root + '/analyze', 'POST', {tag}));})}>分析素材</button>
      <button disabled={disabled || dirty || !shots.length} onClick={() => void run(async () => {setTask(await api<Task>(root + '/plan', 'POST'));})}>生成时间线</button>
      {task && <p role="status">{task.status} <progress value={task.progress} max="100"/>{Math.round(task.progress)}% {working && <button disabled={busy} onClick={() => void run(async () => {setTask(await api<Task>(`/tasks/${task.id}/cancel`, 'POST'));})}>取消任务</button>}</p>}
      {timeline && <><h3>时间线 {timeline.fallback && '· 规则回退'}</h3><p>{timeline.notes}</p><p>编辑后请保存；总时长需在目标 ±5%（至少 ±0.5 秒）内。交叉转场会缩短成片。</p>
        <fieldset disabled={disabled}><legend>片段列表</legend>{timeline.clips.map((c, i) => <article className="remix-clip" key={i}>
          <img src={shots.find(s => s.id === c.shot_id)?.frame_url} alt={`镜头 ${i+1} 关键帧`}/><div><strong>片段 {i+1}</strong> · {c.shot_id.slice(0, 8)}<p>{shots.find(s => s.id === c.shot_id)?.description}</p>
            <div className="filters"><label>起始秒<input type="number" step="0.01" value={c.src_start} onChange={e => edit(i, {src_start: Number(e.target.value)})}/></label><label>结束秒<input type="number" step="0.01" value={c.src_end} onChange={e => edit(i, {src_end: Number(e.target.value)})}/></label>
              <label>速度<input type="number" min="0.25" max="4" step="0.25" value={c.speed} onChange={e => edit(i, {speed: Number(e.target.value)})}/></label>
              <label>转场<select value={c.transition_in.type} onChange={e => edit(i, {transition_in: {type: e.target.value, duration: e.target.value === 'none' ? 0 : .3}})}>{['none', 'fade', 'crossfade', 'slide'].map(v => <option key={v}>{v}</option>)}</select></label><label>转场秒数<input type="number" min="0" max="5" step="0.1" value={c.transition_in.duration} onChange={e => edit(i, {transition_in: {...c.transition_in, duration: Number(e.target.value)}})}/></label></div>
            <label>字幕文本<input value={c.caption?.text ?? ''} onChange={e => edit(i, {caption: e.target.value ? {text: e.target.value, position: c.caption?.position ?? 'bottom', start_offset: c.caption?.start_offset ?? 0, end_offset: c.caption?.end_offset ?? (c.src_end-c.src_start)/c.speed} : null})}/></label>
            <label className="check"><input type="checkbox" checked={c.mute} onChange={e => edit(i, {mute: e.target.checked})}/>静音原声</label>
            <button disabled={i === 0} onClick={() => move(i, -1)}>上移</button><button disabled={i === timeline.clips.length-1} onClick={() => move(i, 1)}>下移</button><button onClick={() => {setTimeline({...timeline, clips: timeline.clips.filter((_, n) => n !== i)}); setDirty(true);}}>删除</button>
          </div></article>)}<button onClick={() => void run(async () => {const p = await api<Project>(root + '/timeline', 'PUT', timeline); setProject(p); setTimeline(p.timeline); setDirty(false); setNotice('时间线已校验并保存');})}>保存时间线{dirty ? '（有未保存修改）' : ''}</button></fieldset>
        <button disabled={disabled || dirty} onClick={() => void run(async () => {setTask(await api<Task>(root + '/render', 'POST'));})}>渲染成片</button>
      </>}
      {project.output_asset_id && <article><h3>成片预览</h3><video className="remix-output" controls src={`/api/remix/output/${project.output_asset_id}`}/><p>用于发布：在发布页选择此素材 ID：<code>{project.output_asset_id}</code></p></article>}
    </>}
  </section>;
}
