import {useEffect, useState} from 'react';

type Group = {id: string; name: string};
type Proxy = Group & {server: string; username: string; password_status: string};
type Account = Group & {group_id: string | null; proxy_id: string | null; status: string; last_error: string | null; last_checked_at: string | null; browser_locale: string | null; browser_timezone: string | null; browser_width: number | null; browser_height: number | null};
type Asset = {id: string; path: string};
type Job = {id: string; account_id: string; caption: string; scheduled_at: string; jitter_seconds: number; status: string; attempts: number; error: string | null; result_url: string | null; warnings?: string[]};
type Log = {id: string; ts: string; level: string; message: string};
type Detail = Job & {logs: Log[]};
const labels: Record<string, string> = {unknown: '未检查', logged_in: '已登录', login_required: '需要登录', banned_suspected: '疑似受限', disabled: '已停用', scheduled: '等待发布', running: '发布中', succeeded: '已完成', failed: '失败', cancelled: '已取消', needs_attention: '需人工处理', missed: '已错过'};
const jobStates = ['scheduled', 'running', 'succeeded', 'failed', 'cancelled', 'needs_attention', 'missed'];
const emptyAccount = {name: '', group_id: '', proxy_id: '', status: 'unknown', browser_locale: '', browser_timezone: '', browser_width: '', browser_height: ''};

async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败');
  }
  return response.status === 204 ? undefined as T : response.json();
}

function dateInput() {
  const d = new Date(Date.now() + 10 * 60000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

const NOT_POSTED_PROMPT = '该任务结果未知，可能已经发布。请先到 TikTok 确认视频未发布；确认后点“确定”继续。';

export function Publisher({page}: {page: '账号' | '发布'}) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [groupName, setGroupName] = useState('');
  const [groupEdit, setGroupEdit] = useState<string | null>(null);
  const [proxyEdit, setProxyEdit] = useState<string | null>(null);
  const [proxy, setProxy] = useState({name: '', server: '', username: ''});
  const [password, setPassword] = useState('');
  const [clearPassword, setClearPassword] = useState(false);
  const [accountEdit, setAccountEdit] = useState<string | null>(null);
  const [account, setAccount] = useState(emptyAccount);
  const [selected, setSelected] = useState<string[]>([]);
  const [groupFilter, setGroupFilter] = useState('');
  const [assetId, setAssetId] = useState('');
  const [path, setPath] = useState('');
  const [caption, setCaption] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [privacy, setPrivacy] = useState('private');
  const [ai, setAi] = useState(false);
  const [comment, setComment] = useState(true);
  const [duet, setDuet] = useState(false);
  const [stitch, setStitch] = useState(false);
  const [start, setStart] = useState(dateInput);
  const [stagger, setStagger] = useState(5);
  const [jitter, setJitter] = useState(60);
  const [status, setStatus] = useState('');
  const [filterAccount, setFilterAccount] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [reschedule, setReschedule] = useState<string | null>(null);
  const [newDate, setNewDate] = useState(dateInput);

  async function refresh() {
    const query = new URLSearchParams();
    if (status) query.set('status', status);
    if (filterAccount) query.set('account_id', filterAccount);
    if (from) query.set('date_from', new Date(from).toISOString());
    if (to) query.set('date_to', new Date(to).toISOString());
    const [g, p, a, v, j] = await Promise.all([api<Group[]>('/account-groups'), api<Proxy[]>('/proxies'), api<Account[]>('/accounts'), api<Asset[]>('/assets'), api<Job[]>(`/publish-jobs?${query}`)]);
    setGroups(g); setProxies(p); setAccounts(a); setAssets(v); setJobs(j);
  }
  useEffect(() => {
    let active = true;
    const poll = () => {if (active) void refresh().catch(() => {if (active) setError('无法读取发布数据，请检查本地服务');});};
    poll(); const timer = window.setInterval(poll, 3000);
    return () => {active = false; window.clearInterval(timer);};
  }, [status, filterAccount, from, to]);
  useEffect(() => {setPassword('');}, [page]);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try {await fn(); await refresh();}
    catch (e) {setError(e instanceof Error ? e.message : '操作失败');}
    finally {setBusy(false);}
  }
  const action = (id: string, name: string, body?: unknown) => run(async () => {
    const result = await api<Detail>(`/publish-jobs/${id}/${name}`, 'POST', body);
    if (detail?.id === id) setDetail(result);
  });
  const filteredAccounts = accounts.filter(a => !groupFilter || a.group_id === groupFilter);

  return <section>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    {page === '账号' ? <>
      <h2>账号</h2><p>每个账号使用独立浏览器 Profile；登录窗口由你手动完成登录或验证码。关闭窗口后可点击“检查”。</p>
      <form onSubmit={e => {e.preventDefault(); void run(async () => {
        await api(accountEdit ? `/accounts/${accountEdit}` : '/accounts', accountEdit ? 'PUT' : 'POST', {...account, group_id: account.group_id || null, proxy_id: account.proxy_id || null, browser_locale: account.browser_locale || null, browser_timezone: account.browser_timezone || null, browser_width: account.browser_width ? Number(account.browser_width) : null, browser_height: account.browser_height ? Number(account.browser_height) : null});
        setAccountEdit(null); setAccount(emptyAccount);
      });}}>
        <label>账号名称<input required maxLength={200} value={account.name} onChange={e => setAccount({...account, name: e.target.value})}/></label>
        <label>分组<select value={account.group_id} onChange={e => setAccount({...account, group_id: e.target.value})}><option value="">未分组</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
        <label>代理<select value={account.proxy_id} onChange={e => setAccount({...account, proxy_id: e.target.value})}><option value="">直连</option>{proxies.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>状态<select value={account.status} onChange={e => setAccount({...account, status: e.target.value})}>{['unknown', 'logged_in', 'login_required', 'banned_suspected', 'disabled'].map(s => <option key={s} value={s}>{labels[s]}</option>)}</select></label>
        <label>语言（留空继承）<input placeholder="en-US" value={account.browser_locale} onChange={e => setAccount({...account, browser_locale: e.target.value})}/></label>
        <label>时区（留空继承）<input placeholder="America/New_York" value={account.browser_timezone} onChange={e => setAccount({...account, browser_timezone: e.target.value})}/></label>
        <label>视口宽度（留空继承）<input type="number" min={320} max={7680} value={account.browser_width} onChange={e => setAccount({...account, browser_width: e.target.value})}/></label>
        <label>视口高度（留空继承）<input type="number" min={320} max={4320} value={account.browser_height} onChange={e => setAccount({...account, browser_height: e.target.value})}/></label>
        <button disabled={busy}>{accountEdit ? '保存账号' : '添加账号'}</button><button type="button" onClick={() => {setAccountEdit(null); setAccount(emptyAccount);}}>清空</button>
      </form>
      <label>筛选分组<select value={groupFilter} onChange={e => setGroupFilter(e.target.value)}><option value="">全部</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
      {filteredAccounts.map(a => <article key={a.id}><strong>{a.name}</strong> <span className={`badge ${a.status}`}>{labels[a.status]}</span>
        <p>{groups.find(g => g.id === a.group_id)?.name || '未分组'} · {proxies.find(p => p.id === a.proxy_id)?.name || '直连'} · 上次检查 {a.last_checked_at ? new Date(a.last_checked_at).toLocaleString() : '—'}</p>
        {a.last_error && <p className="error">{a.last_error}</p>}
        <button disabled={busy || a.status === 'disabled'} onClick={() => void run(async () => {const task = await api<{id: string}>(`/accounts/${a.id}/login`, 'POST'); setNotice(`登录任务 ${task.id.slice(0, 8)} 已提交，等待浏览器窗口。进度见下载页任务列表。`);})}>登录 / 人工接管</button>
        <button disabled={busy || a.status === 'disabled'} onClick={() => void run(async () => {await api(`/accounts/${a.id}/check`, 'POST');})}>检查登录态</button>
        <button disabled={busy} onClick={() => {setAccountEdit(a.id); setAccount({name: a.name, group_id: a.group_id || '', proxy_id: a.proxy_id || '', status: a.status, browser_locale: a.browser_locale || '', browser_timezone: a.browser_timezone || '', browser_width: a.browser_width?.toString() || '', browser_height: a.browser_height?.toString() || ''});}}>编辑</button>
        <button disabled={busy} onClick={() => void run(async () => {await api(`/accounts/${a.id}`, 'DELETE');})}>删除</button>
      </article>)}{!filteredAccounts.length && <p>暂无账号</p>}
      <h2>分组</h2><form onSubmit={e => {e.preventDefault(); void run(async () => {await api(groupEdit ? `/account-groups/${groupEdit}` : '/account-groups', groupEdit ? 'PUT' : 'POST', {name: groupName}); setGroupName(''); setGroupEdit(null);});}}>
        <label>名称<input required maxLength={200} value={groupName} onChange={e => setGroupName(e.target.value)}/></label><button disabled={busy}>{groupEdit ? '保存分组' : '添加分组'}</button>
      </form>{groups.map(g => <article key={g.id}>{g.name} <button disabled={busy} onClick={() => {setGroupEdit(g.id); setGroupName(g.name);}}>编辑</button><button disabled={busy} onClick={() => void run(async () => {await api(`/account-groups/${g.id}`, 'DELETE');})}>删除</button></article>)}
      <h2>代理</h2><form onSubmit={e => {e.preventDefault(); void run(async () => {
        const entered = password; setPassword('');
        const saved = await api<Proxy>(proxyEdit ? `/proxies/${proxyEdit}` : '/proxies', proxyEdit ? 'PUT' : 'POST', {...proxy, password: clearPassword ? '' : entered || null});
        setNotice(`代理 ${saved.name} 已保存`); setProxyEdit(null); setProxy({name: '', server: '', username: ''}); setClearPassword(false);
      });}}>
        <label>名称<input required value={proxy.name} onChange={e => setProxy({...proxy, name: e.target.value})}/></label>
        <label>代理地址<input required placeholder="http://host:port 或 socks5://host:port" value={proxy.server} onChange={e => setProxy({...proxy, server: e.target.value})}/></label>
        <label>用户名<input autoComplete="off" value={proxy.username} onChange={e => setProxy({...proxy, username: e.target.value})}/></label>
        <label>密码（留空保留）<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)}/></label>
        <label className="check"><input type="checkbox" checked={clearPassword} onChange={e => setClearPassword(e.target.checked)}/>清除已存密码</label>
        <button disabled={busy}>{proxyEdit ? '保存代理' : '添加代理'}</button><button type="button" onClick={() => {setProxyEdit(null); setProxy({name: '', server: '', username: ''}); setPassword(''); setClearPassword(false);}}>清空</button>
      </form>{proxies.map(p => <article key={p.id}><strong>{p.name}</strong> · {p.server} · 密码{p.password_status}
        <button disabled={busy} onClick={() => {setProxyEdit(p.id); setProxy({name: p.name, server: p.server, username: p.username}); setPassword(''); setClearPassword(false);}}>编辑</button>
        <button disabled={busy} onClick={() => void run(async () => {await api(`/proxies/${p.id}`, 'DELETE');})}>删除</button>
      </article>)}
    </> : <>
      <h2>新建定时发布</h2><p>使用本机时间输入，保存为 UTC。到点后本地浏览器执行上传，电脑及应用需要保持运行。文案支持 {'{account_name}'} 和 {'{index}'}。</p>
      <form onSubmit={e => {e.preventDefault(); void run(async () => {
        if (!selected.length) throw new Error('请至少选择一个账号');
        const result = await api<Job[]>('/publish-jobs/batch', 'POST', {account_ids: selected, ...(assetId ? {asset_id: assetId} : {video_path: path}), caption, hashtags: hashtags.split(/[\s,，]+/).filter(Boolean), privacy, ai_generated: ai, allow_comment: comment, allow_duet: duet, allow_stitch: stitch, scheduled_at: new Date(start).toISOString(), stagger_minutes: stagger, jitter_window_seconds: jitter});
        const warnings = [...new Set(result.flatMap(j => j.warnings || []))];
        setNotice(`已创建 ${result.length} 条任务${warnings.length ? '；提示：' + warnings.join('；') : ''}`);
      });}}>
        <fieldset><legend>选择账号（可多选）</legend>{accounts.filter(a => a.status !== 'disabled').map(a => <label className="check" key={a.id}><input type="checkbox" checked={selected.includes(a.id)} onChange={e => setSelected(e.target.checked ? [...selected, a.id] : selected.filter(id => id !== a.id))}/>{a.name} · {labels[a.status]}</label>)}{!accounts.length && <p>请先添加账号</p>}</fieldset>
        <label>素材<select value={assetId} onChange={e => setAssetId(e.target.value)}><option value="">使用本地路径</option>{assets.map(a => <option key={a.id} value={a.id}>{a.path}</option>)}</select></label>
        {!assetId && <label>视频绝对路径<input required value={path} onChange={e => setPath(e.target.value)} placeholder="D:\Videos\clip.mp4"/></label>}
        <label>文案模板<textarea maxLength={2200} value={caption} onChange={e => setCaption(e.target.value)}/></label>
        <label>话题（空格或逗号分隔）<input value={hashtags} onChange={e => setHashtags(e.target.value)}/></label>
        <label>可见性<select value={privacy} onChange={e => setPrivacy(e.target.value)}><option value="private">仅自己</option><option value="friends">朋友</option><option value="public">公开</option></select></label>
        <label className="check"><input type="checkbox" checked={ai} onChange={e => setAi(e.target.checked)}/>AI 生成内容标注</label>
        <label className="check"><input type="checkbox" checked={comment} onChange={e => setComment(e.target.checked)}/>允许评论</label>
        <label className="check"><input type="checkbox" checked={duet} onChange={e => setDuet(e.target.checked)}/>允许合拍</label>
        <label className="check"><input type="checkbox" checked={stitch} onChange={e => setStitch(e.target.checked)}/>允许拼接</label>
        <label>起始时间（本机时区）<input required type="datetime-local" value={start} onChange={e => setStart(e.target.value)}/></label>
        <label>账号间错峰（分钟）<input type="number" min={0} max={10080} step="0.1" value={stagger} onChange={e => setStagger(Number(e.target.value))}/></label>
        <label>随机延后窗口（秒）<input type="number" min={0} max={86400} value={jitter} onChange={e => setJitter(Number(e.target.value))}/></label>
        <button disabled={busy || !selected.length}>创建发布任务</button>
      </form>
      <h2>发布任务</h2><div className="filters">
        <label>状态<select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部</option>{jobStates.map(s => <option key={s} value={s}>{labels[s]}</option>)}</select></label>
        <label>账号<select value={filterAccount} onChange={e => setFilterAccount(e.target.value)}><option value="">全部</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <label>起始日期<input type="datetime-local" value={from} onChange={e => setFrom(e.target.value)}/></label><label>截止日期<input type="datetime-local" value={to} onChange={e => setTo(e.target.value)}/></label>
      </div>
      {jobs.map(j => <article key={j.id}><strong>{accounts.find(a => a.id === j.account_id)?.name || j.account_id}</strong> <span className={`badge ${j.status}`}>{labels[j.status]}</span>
        <p>{new Date(j.scheduled_at).toLocaleString()} + 随机 {j.jitter_seconds} 秒 · 已尝试 {j.attempts} 次</p><p>{j.caption}</p>{j.error && <p className="error">{j.error}</p>}
        {j.result_url && <a href={j.result_url} target="_blank" rel="noreferrer">查看视频</a>}
        {j.status === 'needs_attention' && <p>请先在账号页登录 / 人工接管，并查看数据目录 screenshots 中以任务 ID 开头的截图。确认是否已发布后，再标记完成或重试，避免重复发布。</p>}
        <button disabled={busy} onClick={() => void run(async () => {setDetail(await api<Detail>(`/publish-jobs/${j.id}`));})}>日志 / 详情</button>
        {['scheduled', 'needs_attention'].includes(j.status) && <button disabled={busy} onClick={() => void action(j.id, 'cancel')}>取消</button>}
        {['failed', 'cancelled', 'missed', 'needs_attention'].includes(j.status) && <button disabled={busy} onClick={() => void action(j.id, 'retry', j.status === 'needs_attention' ? {confirm_not_posted: window.confirm(NOT_POSTED_PROMPT)} : undefined)}>重试</button>}
        {!['running', 'succeeded'].includes(j.status) && <button disabled={busy} onClick={() => {setReschedule(j.id); setNewDate(dateInput());}}>改期</button>}
        {j.status === 'needs_attention' && <button disabled={busy} onClick={() => void action(j.id, 'complete', {})}>人工确认已完成</button>}
        {reschedule === j.id && <form onSubmit={e => {e.preventDefault(); void run(async () => {await api(`/publish-jobs/${j.id}/reschedule`, 'POST', {scheduled_at: new Date(newDate).toISOString(), confirm_not_posted: j.status === 'needs_attention' ? window.confirm(NOT_POSTED_PROMPT) : false}); setReschedule(null);});}}><label>新的本机时间<input required type="datetime-local" value={newDate} onChange={e => setNewDate(e.target.value)}/></label><button disabled={busy}>保存改期</button></form>}
      </article>)}{!jobs.length && <p>暂无符合条件的发布任务</p>}
      {detail && <aside aria-label="发布任务日志"><h3>任务 {detail.id}</h3><p>{labels[detail.status]}</p>{detail.logs.map(l => <p key={l.id}>{new Date(l.ts).toLocaleString()} [{l.level}] {l.message}</p>)}<button onClick={() => setDetail(null)}>关闭日志</button></aside>}
    </>}
  </section>;
}
