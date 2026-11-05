import React, {useCallback, useEffect, useState} from 'react';

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '请求失败');
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export type License = {licensed: boolean; machine_code: string; name: string | null; expires: string | null; message: string};
export type Env = {
  app: string; version: string; platform: string; data_dir: string;
  ffmpeg: {ok: boolean; path: string | null; source: string | null};
  ffprobe: {ok: boolean; path: string | null; source: string | null};
  chromium: {ok: boolean; path: string | null};
  install: {state: 'idle' | 'running' | 'done' | 'failed'; log: string[]};
};

/** Blocks the app behind the license screen until a valid code is activated. */
export function Gate({children}: {children: React.ReactNode}) {
  const [license, setLicense] = useState<License | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const load = useCallback(() => api<License>('/license').then(setLicense).catch(e => setError(e.message)), []);
  useEffect(() => {void load();}, [load]);
  if (!license) return <main><p>{error || '正在检查授权…'}</p></main>;
  if (license.licensed) return <>{children}</>;
  return <main>
    <header><h1><img src="/favicon.svg" alt="" width="36" height="36" style={{verticalAlign: 'middle', marginRight: '.5rem'}}/>MAVO BOX <small>激活</small></h1>
      <p>本软件仅限学员使用。请把下面的机器码发给老师，获取你的授权码后粘贴到这里。</p></header>
    <section>
      <h2>① 你的机器码</h2>
      <p><code style={{fontSize: '1.4rem'}}>{license.machine_code}</code>{' '}
        <button type="button" onClick={() => {void navigator.clipboard?.writeText(license.machine_code).then(() => setCopied(true)); }}>{copied ? '已复制' : '复制'}</button></p>
      <h2>② 粘贴授权码</h2>
      {license.message !== '尚未激活' && <p className="error">{license.message}</p>}
      <form onSubmit={e => {e.preventDefault(); setError(''); void api<License>('/license', 'POST', {code}).then(l => {setLicense(l); setCode('');}).catch(err => setError(err.message));}}>
        <label>授权码<textarea required rows={4} value={code} onChange={e => setCode(e.target.value)} placeholder="MAVO1.…"/></label>
        <button>激活</button>
      </form>
      {error && <p role="alert" className="error">{error}</p>}
    </section>
  </main>;
}

const presets = [
  {label: '大模型 LLM（OpenAI 兼容）', type: 'llm', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', api_style: 'openai_compat', hint: '用于视频理解、脚本与混剪编排；需支持图片输入。可换成任何 OpenAI 兼容服务（改地址和模型名）。'},
  {label: '语音转写 ASR', type: 'asr', base_url: 'https://api.openai.com/v1', model: 'whisper-1', api_style: 'openai_compat', hint: '给参考视频和配音生成词级时间。'},
  {label: '配音 TTS', type: 'tts', base_url: 'https://api.openai.com/v1', model: 'tts-1', api_style: 'openai_compat', hint: '把新台词合成为语音。'},
  {label: '图片生成', type: 'image_gen', base_url: 'https://api.openai.com/v1', model: 'gpt-image-1', api_style: 'openai_compat', hint: '为人物/产品生成一致的定妆图。'},
  {label: '视频生成（fal.ai 队列）', type: 'video_gen', base_url: 'https://queue.fal.run', model: 'fal-ai/kling-video/v2.1/standard/image-to-video', api_style: 'fal_queue', hint: '模型 ID 仅为示例，请以 fal.ai 官网文档为准；单价请在「设置」的高级配置里填写 pricing，否则费用显示为未知。'},
  {label: '视频生成（Replicate）', type: 'video_gen', base_url: 'https://api.replicate.com', model: '', api_style: 'replicate', hint: '模型版本号请到 Replicate 模型页复制。'},
] as const;

function Row({ok, label, detail}: {ok: boolean; label: string; detail?: string | null}) {
  return <p>{ok ? '✅' : '❌'} <strong>{label}</strong>{detail ? <small> — {detail}</small> : null}</p>;
}

/** First-run wizard: environment check, one-click browser install, provider presets. */
export function Setup({onDone}: {onDone?: () => void}) {
  const [env, setEnv] = useState<Env | null>(null);
  const [error, setError] = useState('');
  const [mirror, setMirror] = useState('');
  const [sel, setSel] = useState(0);
  const [form, setForm] = useState<{base_url: string; model: string; key: string}>({base_url: presets[0].base_url, model: presets[0].model, key: ''});
  const [notice, setNotice] = useState('');
  const refresh = useCallback(() => api<Env>('/env').then(setEnv).catch(e => setError(e.message)), []);
  useEffect(() => {void refresh();}, [refresh]);
  useEffect(() => {
    if (env?.install.state !== 'running') return;
    const timer = setInterval(() => void refresh(), 1500);
    return () => clearInterval(timer);
  }, [env?.install.state, refresh]);

  const preset = presets[sel];
  const pick = (index: number) => {setSel(index); setForm({base_url: presets[index].base_url, model: presets[index].model, key: ''});};
  const ready = !!env && env.ffmpeg.ok && env.ffprobe.ok && env.chromium.ok;

  async function saveProvider() {
    setError(''); setNotice('');
    try {
      const provider = await api<{id: string}>('/providers', 'POST', {type: preset.type, name: preset.label, base_url: form.base_url, model: form.model, api_style: preset.api_style, extra: {}});
      if (form.key) await api(`/secrets/${provider.id}`, 'PUT', {key: form.key});
      setForm({...form, key: ''}); setNotice(`已保存「${preset.label}」。Key 存放在系统凭据管理器，不会显示或上传。`);
    } catch (e) {setError((e as Error).message);}
  }

  return <section>
    <h2>首次使用向导</h2>
    {error && <p role="alert" className="error">{error}</p>}
    <h3>① 环境检查</h3>
    {!env ? <p>检查中…</p> : <>
      <Row ok={env.ffmpeg.ok && env.ffprobe.ok} label="视频处理组件 ffmpeg" detail={env.ffmpeg.ok ? (env.ffmpeg.source === 'bundled' ? '已内置' : '使用系统已安装版本') : '未找到。安装包应已内置；请重新安装或联系老师'}/>
      <Row ok={env.chromium.ok} label="发布用浏览器组件 Chromium" detail={env.chromium.ok ? '已安装' : '未安装（约 150MB，仅「账号 / 发布」功能需要）'}/>
      {!env.chromium.ok && <>
        <label>下载镜像（可选，下载很慢或失败时填写；留空使用官方源）<input value={mirror} onChange={e => setMirror(e.target.value)} placeholder="https://…"/></label>
        <button disabled={env.install.state === 'running'} onClick={() => {setError(''); void api('/env/install-browser', 'POST', {mirror: mirror || null}).then(() => refresh()).catch(e => setError(e.message));}}>
          {env.install.state === 'running' ? '安装中…' : env.install.state === 'failed' ? '重试安装' : '一键安装浏览器组件'}</button>
        {env.install.log.length > 0 && <pre style={{maxHeight: '10rem', overflow: 'auto'}}>{env.install.log.slice(-12).join('\n')}</pre>}
      </>}
      <p><small>数据目录：{env.data_dir} · 版本 {env.version} · {env.platform}</small></p>
    </>}
    <h3>② 配置 AI 服务（按需，可稍后在「设置」里改）</h3>
    <p>Key 只保存在你自己的电脑里。没有某项服务可以先跳过，对应功能暂时不可用。</p>
    <label>服务类型<select value={sel} onChange={e => pick(Number(e.target.value))}>{presets.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}</select></label>
    <p><small>{preset.hint}</small></p>
    <form onSubmit={e => {e.preventDefault(); void saveProvider();}}>
      <label>接口地址<input required value={form.base_url} onChange={e => setForm({...form, base_url: e.target.value})}/></label>
      <label>模型<input value={form.model} onChange={e => setForm({...form, model: e.target.value})}/></label>
      <label>API Key<input type="password" autoComplete="off" value={form.key} onChange={e => setForm({...form, key: e.target.value})}/></label>
      <button>保存</button>
    </form>
    {notice && <p role="status">{notice}</p>}
    <h3>③ 开始使用</h3>
    <button disabled={!ready && !env?.ffmpeg.ok} onClick={onDone}>{ready ? '一切就绪，开始使用' : '先跳过，进入应用'}</button>
  </section>;
}

export async function needsSetup(): Promise<boolean> {
  try {
    const env = await api<Env>('/env');
    return !(env.ffmpeg.ok && env.ffprobe.ok);
  } catch {return false;}
}
