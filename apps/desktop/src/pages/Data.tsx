import { useCallback, useEffect, useRef, useState } from "react";
import { api, QualityResp, SyncStatus } from "../api";

export default function Data() {
  const [quality, setQuality] = useState<QualityResp | null>(null);
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const loadQuality = useCallback(() => {
    api<QualityResp>("/quality").then(setQuality).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const poll = useCallback(async () => {
    try {
      const s = await api<SyncStatus>("/sync/status");
      setStatus(s);
      if (s.running) {
        timer.current = window.setTimeout(poll, 2000);
      } else {
        loadQuality();
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, [loadQuality]);

  useEffect(() => {
    loadQuality();
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [loadQuality, poll]);

  async function startSync() {
    setError(null);
    try {
      await api("/sync", { method: "POST", body: JSON.stringify({ universe: "csi300" }) });
      poll();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <div className="page">
      <div className="toolbar">
        <h2>数据</h2>
        <button onClick={startSync} disabled={status?.running ?? false}>
          {status?.running ? "同步中…" : "同步沪深300"}
        </button>
        <button onClick={loadQuality}>刷新</button>
      </div>
      {error && <p className="error">{error}</p>}
      {status?.running && (
        <pre className="sync-log">{status.log.join("\n") || "启动中…"}</pre>
      )}
      {status?.error && <p className="error">上次同步失败：{status.error}</p>}
      {status?.result && !status.running && (
        <p className="muted">
          上次同步：{status.result.stocks} 只 / 新增 {status.result.rows} 行 / 失败 {status.result.failed.length} 只
        </p>
      )}
      {quality && (
        <>
          <p>
            本地覆盖 <b>{quality.stocks}</b> 只，其中滞后超过 5 天的 <b>{quality.stale}</b> 只。
          </p>
          {quality.rows.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>代码</th>
                  <th>起始</th>
                  <th>最新</th>
                  <th>bar 数</th>
                  <th>滞后(天)</th>
                </tr>
              </thead>
              <tbody>
                {quality.rows.slice(0, 15).map((r) => (
                  <tr key={r.symbol}>
                    <td className="mono">{r.symbol}</td>
                    <td>{r.first_date}</td>
                    <td>{r.last_date}</td>
                    <td>{r.bars}</td>
                    <td className={r.lag_days > 5 ? "down" : ""}>{r.lag_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {quality.rows.length > 15 && (
            <p className="muted small">仅显示滞后最严重的 15 只，共 {quality.rows.length} 只。</p>
          )}
        </>
      )}
    </div>
  );
}
