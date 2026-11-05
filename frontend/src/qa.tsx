import React, {useState} from 'react';

type Finding = {level: 'error' | 'warn' | 'info'; code: string; message: string; at?: number};
type Report = {
  status: 'ok' | 'warn' | 'error'; findings: Finding[]; sheet: boolean; sheet_url: string | null;
  info: {duration: number; width: number | null; height: number | null; fps: number; has_audio: boolean; lufs: number | null; true_peak: number | null; size_mb: number | null};
};

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : '请求失败');
  }
  return response.json();
}

/** One-click quality check of a finished video: format, black/frozen frames, silence, loudness + a contact sheet. */
export function QACheck({assetId, targetSeconds}: {assetId: string; targetSeconds?: number}) {
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = () => {
    setBusy(true); setError('');
    request<Report>(`/qa/${assetId}`, 'POST', targetSeconds ? {target_seconds: targetSeconds} : {})
      .then(r => setReport({...r, sheet_url: r.sheet_url ? `${r.sheet_url}?t=${Date.now()}` : null}))
      .catch(e => setError((e as Error).message)).finally(() => setBusy(false));
  };
  const badge = report ? {ok: '✅ 未发现问题', warn: '⚠️ 有需要注意的地方', error: '❌ 有严重问题'}[report.status] : '';
  return <div className="qa-check">
    <button type="button" disabled={busy} onClick={run}>{busy ? '质检中…' : report ? '重新质检' : '成片质检'}</button>
    {error && <p role="alert" className="error">{error}</p>}
    {report && <>
      <p><strong>{badge}</strong>
        <small> · {report.info.width}×{report.info.height} · {report.info.duration}s · {report.info.fps}fps
          {report.info.lufs !== null && ` · 响度 ${report.info.lufs} LUFS`}{report.info.size_mb !== null && ` · ${report.info.size_mb}MB`}</small></p>
      {report.findings.length > 0 && <ul>{report.findings.map((f, i) => <li key={i}>{f.level === 'error' ? '❌' : '⚠️'} {f.message}</li>)}</ul>}
      {report.sheet_url && <img src={report.sheet_url} alt="成片缩略图联系表" style={{maxWidth: '100%'}}/>}
      <p><small>缩略图按时间均匀取 12 帧，方便你快速浏览整条成片；检查只是辅助，最终效果请以你的观看为准。</small></p>
    </>}
  </div>;
}
