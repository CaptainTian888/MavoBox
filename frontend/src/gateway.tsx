import React, {useCallback, useEffect, useState} from 'react';

type Status = {
  available: boolean; enabled: boolean; running: boolean; managed: boolean; port: number; url: string;
  openai_base_url: string; version: string | null; source: string | null; log: string;
};

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '请求失败');
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

/** Built-in local AI gateway (GPT-Load): enable it, sign in to its console, then connect it as the LLM provider. */
export function Gateway() {
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [accessKey, setAccessKey] = useState('');
  const [model, setModel] = useState('gpt-4o-mini');
  const refresh = useCallback(() => api<Status>('/gateway').then(setSt).catch(e => setError((e as Error).message)), []);
  useEffect(() => {void refresh(); const t = setInterval(() => void refresh(), 4000); return () => clearInterval(t);}, [refresh]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy(true); setError(''); setNotice('');
    try {await action(); if (done) setNotice(done); await refresh();} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }

  if (!st) return <section><p>{error || '读取中…'}</p></section>;
  return <section>
    <h2>AI 订阅 / 本地网关</h2>
    <p role="note" className="clone-policy">
      内置的是开源网关 <b>GPT-Load</b>：把你自己的 <b>API Key</b> 或<b>订阅账号</b>统一成一个本机地址，再接给 MAVO BOX 当大模型用。
      <br/><b>使用订阅账号可能违反对应服务商的使用条款，存在限流或封号风险</b>，请只用你自己的账号并自行评估；想要稳定和可核算的费用，优先用官方 API Key。详见 docs/接入AI订阅与网关.md。
    </p>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!st.available ? <p className="error">这个安装包里没有内置网关程序，请重新下载最新的安装包。</p> : <>
      <article>
        <p>内置网关版本：<b>{st.version ?? '未知'}</b> · 地址 <code>{st.url}</code> · 状态：<b>{st.running ? '🟢 运行中' : st.enabled ? '🟡 已启用，未运行' : '⚪ 未启用'}</b></p>
        <button disabled={busy} onClick={() => void run(() => api('/gateway/enable', 'POST', {enabled: !st.enabled}), st.enabled ? '已停用内置网关' : '内置网关已启用，之后会随软件自动启动')}>
          {st.enabled ? '停用内置网关' : '启用内置网关（随软件启动）'}</button>{' '}
        {st.enabled && <button disabled={busy} onClick={() => void run(() => api('/gateway/restart', 'POST'), '已重启')}>重启</button>}
        {st.running && <small> · 数据保存在数据目录的 gateway 文件夹，备份时 encryption.key 与数据库必须一起保管</small>}
      </article>
      {st.running && <>
        <h3>① 登录网关控制台，添加渠道并创建 AccessKey</h3>
        <ol>
          <li>点下面的按钮打开控制台，用<b>管理 Key</b>登录。</li>
          <li>依次：<b>添加渠道</b>（填 API Key，或为订阅渠道完成授权）→ <b>创建分组</b>（选渠道、配置模型）→ <b>创建 AccessKey</b>，并复制它。</li>
        </ol>
        <button disabled={busy} onClick={() => openConsole(st.url)}>打开网关控制台（应用内窗口）</button>{' '}
        <button disabled={busy} onClick={() => void run(() => api('/gateway/open-console', 'POST'), '已在系统浏览器中打开控制台')}>用系统浏览器打开</button>{' '}
        <button disabled={busy} onClick={() => void run(async () => {
          const r = await api<{key: string}>('/gateway/admin-key', 'POST');
          await navigator.clipboard.writeText(r.key);
        }, '管理 Key 已复制到剪贴板（粘贴到控制台登录页，不要发给任何人）')}>复制管理 Key</button>
        <h3>② 把 AccessKey 接到 MAVO BOX</h3>
        <form onSubmit={e => {e.preventDefault(); void run(async () => {
          const r = await api<{created: boolean}>('/gateway/connect', 'POST', {access_key: accessKey, model});
          setAccessKey(''); return r;
        }, '已连接：到「设置」页点“测试连接 / 获取模型”，选一个网关里可用的模型');}}>
          <label>AccessKey（网关控制台里创建的）<input required type="password" autoComplete="off" value={accessKey} onChange={e => setAccessKey(e.target.value)}/></label>
          <label>模型名（可稍后在设置里点选）<input required value={model} onChange={e => setModel(e.target.value)}/></label>
          <button disabled={busy}>连接到 MAVO BOX</button>
        </form>
        <p><small>连接后，「设置」里会出现“本机网关（内置 GPT-Load）”，它会被当作<b>大模型 LLM</b>使用；语音转写和视频生成仍需另外配置。</small></p>
      </>}
    </>}
  </section>;
}

/** Opens the console in an in-app window (the desktop shell intercepts window.open for the gateway address). */
export function openConsole(url: string) {
  window.open(url, '_blank');
}

/** Always-visible switch in the app header: turn the built-in GPT-Load on/off and open its console. */
export function GatewaySwitch() {
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const refresh = useCallback(() => api<Status>('/gateway').then(setSt).catch(() => setSt(null)), []);
  useEffect(() => {void refresh(); const t = setInterval(() => void refresh(), 5000); return () => clearInterval(t);}, [refresh]);
  if (!st || !st.available) return null;   // this build has no bundled gateway
  const toggle = async () => {
    setBusy(true); setError('');
    try {await api('/gateway/enable', 'POST', {enabled: !st.enabled}); await refresh();}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const state = busy ? (st.enabled ? '正在停止…' : '正在启动…（最多约 40 秒）') : st.running ? `运行中 · ${st.version ?? ''}` : st.enabled ? '已启用，未运行' : '已关闭';
  return <div className="gw-switch" aria-label="内置 AI 网关">
    <label><input type="checkbox" role="switch" checked={st.enabled} disabled={busy} onChange={() => void toggle()}/> GPT-Load 网关</label>
    <span className="gw-state">{state}</span>
    <button type="button" disabled={!st.running || busy} onClick={() => openConsole(st.url)}>打开 GPT-Load</button>
    <button type="button" disabled={!st.running || busy} title="登录控制台时粘贴；不要发给任何人" onClick={() => {
      setError('');
      api<{key: string}>('/gateway/admin-key', 'POST').then(r => navigator.clipboard.writeText(r.key)).then(() => setCopied(true), e => setError((e as Error).message));
    }}>{copied ? '已复制登录 Key' : '复制登录 Key'}</button>
    {error && <span role="alert" className="error"> {error}</span>}
  </div>;
}
