import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import {Publisher} from './publisher';
import {Remix} from './remix';
import {Clone} from './clone';
import {Gate, Setup, needsSetup} from './onboarding';

type Task = {id: string; status: string; progress: number; error: string | null; attempts: number};
type Asset = {id: string; path: string; type: string; duration: number; width: number | null; height: number | null};
type Provider = {id: string; type: 'llm' | 'video_gen' | 'asr' | 'image_gen' | 'tts'; name: string; base_url: string; model: string; key_status: string; api_style: string; extra: Record<string, unknown>};
type ProviderForm = Omit<Provider, 'id' | 'key_status'>;
const empty: ProviderForm = {type: 'llm', name: '', base_url: '', model: '', api_style: 'openai_compat', extra: {}};

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '请求失败');
  }
  return response.status === 204 ? undefined as T : response.json();
}

function App() {
  const [page, setPage] = useState('下载');
  useEffect(() => {void needsSetup().then(n => {if (n) setPage('向导');});}, []);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [url, setUrl] = useState('');
  const [seconds, setSeconds] = useState(5);
  const [path, setPath] = useState('');
  const [form, setForm] = useState<ProviderForm>(empty);
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState('');
  const [extra, setExtra] = useState('{}');
  const refresh = async () => {
    const [t, a, p] = await Promise.all([api<Task[]>('/tasks'), api<Asset[]>('/assets'), api<Provider[]>('/providers')]);
    setTasks(t); setAssets(a); setProviders(p);
  };
  useEffect(() => {
    let active = true;
    const poll = () => { if (active) void refresh().catch(() => { if (active) setError('后端连接失败，请确认服务已启动'); }); };
    poll(); const timer = window.setInterval(poll, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); }
    finally { setBusy(false); }
  }
  async function saveProvider() {
    const provider = await api<Provider>(editing ? `/providers/${editing}` : '/providers', editing ? 'PUT' : 'POST', {...form, extra: JSON.parse(extra)});
    setEditing(provider.id); // Keep the created ID if the credential store fails; retry updates it.
    const entered = key; setKey('');
    if (entered) await api(`/secrets/${provider.id}`, 'PUT', {key: entered});
    setForm(empty); setExtra('{}'); setEditing(null); setNotice('配置已保存');
  }
  return <main>
    <header><h1><img src="/favicon.svg" alt="" width="36" height="36" style={{verticalAlign: 'middle', marginRight: '.5rem'}}/>MAVO BOX <small>本地视频工具</small></h1><p>下载片段、爆款克隆、智能混剪与多账号定时发布</p></header>
    <nav>{['下载', '素材库', '混剪', '爆款克隆', '账号', '发布', '设置', '向导'].map(p => <button key={p} aria-current={page === p ? 'page' : undefined} onClick={() => {setPage(p); setKey('');}}>{p}</button>)}</nav>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {(page === '账号' || page === '发布') && <Publisher page={page}/>}
    {page === '向导' && <Setup onDone={() => setPage('下载')}/>}
    {page === '混剪' && <Remix/>}
    {page === '爆款克隆' && <Clone/>}
    {page === '下载' && <section><h2>下载前 N 秒</h2>
      <form onSubmit={e => {e.preventDefault(); void run(async () => {await api('/downloads', 'POST', {url, seconds}); setUrl('');});}}>
        <label>TikTok 链接<input required type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.tiktok.com/@…/video/…"/></label>
        <label>截取秒数<input required type="number" min="0.1" max="3600" step="0.1" value={seconds} onChange={e => setSeconds(Number(e.target.value))}/></label>
        <button disabled={busy}>提交下载</button>
      </form><h2>任务</h2>{!tasks.length && <p>暂无任务</p>}
      {tasks.map(t => <article key={t.id}><code>{t.id.slice(0, 8)}</code> · {t.status} · 第 {t.attempts} 次
        <progress value={t.progress} max="100" aria-label="任务进度"/> {Math.round(t.progress)}%
        {t.error && <p>{t.error}</p>}
        {['pending', 'running'].includes(t.status) && <button disabled={busy} onClick={() => void run(async () => {await api(`/tasks/${t.id}/cancel`, 'POST');})}>取消</button>}
        {t.status === 'failed' && <button disabled={busy} onClick={() => void run(async () => {await api(`/tasks/${t.id}/retry`, 'POST');})}>重试</button>}
      </article>)}</section>}
    {page === '素材库' && <section><h2>素材库</h2><form onSubmit={e => {e.preventDefault(); void run(async () => {await api('/assets', 'POST', {path}); setPath('');});}}>
      <label>本地文件绝对路径<input required value={path} onChange={e => setPath(e.target.value)} placeholder="D:\Videos\clip.mp4"/></label><button disabled={busy}>添加素材</button>
    </form><div className="table"><table><thead><tr><th>路径</th><th>类型</th><th>时长</th><th>尺寸</th></tr></thead><tbody>
      {assets.map(a => <tr key={a.id}><td>{a.path}</td><td>{a.type}</td><td>{a.duration.toFixed(2)}s</td><td>{a.width ?? '—'} × {a.height ?? '—'}</td></tr>)}
    </tbody></table></div>{!assets.length && <p>暂无素材</p>}</section>}
    {page === '设置' && <section><h2>{editing ? '编辑 Provider' : '新增 Provider'}</h2>
      <form onSubmit={e => {e.preventDefault(); void run(saveProvider);}}>
        <label>类型<select value={form.type} onChange={e => setForm({...form, type: e.target.value as ProviderForm['type']})}>{Object.entries({llm:'LLM',video_gen:'视频生成',asr:'语音转写',image_gen:'图片生成',tts:'配音'}).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        <label>接口风格<select value={form.api_style} onChange={e=>setForm({...form,api_style:e.target.value})}>{['openai_compat','fal_queue','replicate','custom_http'].map(v=><option key={v}>{v}</option>)}</select></label>
        <label>高级配置 JSON（单价、模型时长、端点模板；禁止填写密钥）<textarea value={extra} onChange={e=>setExtra(e.target.value)} rows={7}/></label>
        {(['name', 'base_url', 'model'] as const).map(field => <label key={field}>{({name: '名称', base_url: 'Base URL', model: '模型'})[field]}<input required type={field === 'base_url' ? 'url' : 'text'} value={form[field]} onChange={e => setForm({...form, [field]: e.target.value})}/></label>)}
        <label>API Key（留空保留原值）<input type="password" autoComplete="new-password" value={key} onChange={e => setKey(e.target.value)}/></label>
        <button disabled={busy}>保存</button><button type="button" onClick={() => {setEditing(null); setForm(empty); setExtra('{}'); setKey('');}}>清空表单</button>
      </form><p>Key 保存至系统凭据管理器，保存后不回显。混剪分析和编排会调用选定的 LLM。</p>
      {providers.map(p => <article key={p.id}><strong>{p.name}</strong> · {p.type} · {p.model}<p>{p.base_url} · Key {p.key_status}</p>
        <button disabled={busy} onClick={() => {setEditing(p.id); setForm({type: p.type, name: p.name, base_url: p.base_url, model: p.model,api_style:p.api_style,extra:p.extra}); setExtra(JSON.stringify(p.extra,null,2)); setKey('');}}>编辑</button>
        <button disabled={busy} onClick={() => void run(async () => {await api(`/providers/${p.id}`, 'DELETE'); if (editing === p.id) {setEditing(null); setForm(empty); setKey('');}})}>删除</button>
      </article>)}</section>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><Gate><App/></Gate></React.StrictMode>);
