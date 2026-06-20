import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

interface EvolveResult {
  promoted: boolean;
  best_params: Record<string, number>;
  train_sharpe: number;
  val_metrics: Record<string, number>;
  train_period: [string, string];
  val_period: [string, string];
  n_trials: number;
  error?: string;
}

interface Champion {
  id: number;
  params: Record<string, number | boolean | object>;
  created_at: string;
}

const FACTOR_LABELS: Record<string, string> = {
  momentum_20: "动量20日",
  momentum_60: "动量60日",
  reversal_5: "反转5日",
  low_volatility_60: "低波动60日",
  liquidity_20: "流动性20日",
};

function fmt(v: number | boolean | object | undefined): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") return v < 1 ? v.toFixed(3) : v.toFixed(1);
  return JSON.stringify(v);
}

export default function Evolve() {
  const [form, setForm] = useState({ n_trials: 50, train_years: 3.0, val_years: 1.0 });
  const [status, setStatus] = useState<{ running: boolean; result: EvolveResult | null; error: string | null } | null>(null);
  const [champion, setChampion] = useState<Champion | null>(null);
  const [pollTimer, setPollTimer] = useState<number | null>(null);

  const loadChampion = useCallback(() => {
    api<{ champion: Champion | null }>("/evolve/champion")
      .then((r) => setChampion(r.champion))
      .catch(() => {});
  }, []);

  const pollStatus = useCallback(() => {
    api<{ running: boolean; result: EvolveResult | null; error: string | null }>("/evolve/status")
      .then((s) => {
        setStatus(s);
        if (s.running) {
          const t = window.setTimeout(pollStatus, 3000);
          setPollTimer(t);
        } else if (s.result?.promoted) {
          loadChampion();
        }
      })
      .catch(() => {});
  }, [loadChampion]);

  useEffect(() => {
    loadChampion();
    pollStatus();
    return () => { if (pollTimer) clearTimeout(pollTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startEvolve() {
    try {
      await api("/evolve/start", { method: "POST", body: JSON.stringify(form) });
      pollStatus();
    } catch (e) {
      alert(String(e instanceof Error ? e.message : e));
    }
  }

  const res = status?.result;

  return (
    <div className="page">
      <div className="toolbar">
        <h2>策略进化</h2>
        <label>
          Trials
          <select value={form.n_trials} onChange={(e) => setForm({ ...form, n_trials: Number(e.target.value) })}>
            {[20, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>
          训练（年）
          <select value={form.train_years} onChange={(e) => setForm({ ...form, train_years: Number(e.target.value) })}>
            {[2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>
          验证（年）
          <select value={form.val_years} onChange={(e) => setForm({ ...form, val_years: Number(e.target.value) })}>
            {[0.5, 1, 1.5, 2].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button onClick={startEvolve} disabled={status?.running ?? false}>
          {status?.running ? "进化中…" : "开始进化"}
        </button>
        <button onClick={loadChampion}>刷新 champion</button>
      </div>

      {status?.running && (
        <p className="muted">正在运行 Optuna 寻优，请稍候（每 3 秒刷新）…</p>
      )}
      {status?.error && <p className="error">进化失败：{status.error}</p>}

      {res && !res.error && (
        <div style={{ marginBottom: 16 }}>
          <p>
            <b className={res.promoted ? "up" : "muted"}>
              {res.promoted ? "✓ 新 champion 已晋升" : "未晋升（challenger 未超过 champion）"}
            </b>
            &nbsp;·&nbsp;训练期 {res.train_period[0]} ~ {res.train_period[1]}
            &nbsp;·&nbsp;验证期 {res.val_period[0]} ~ {res.val_period[1]}
          </p>
          <div className="metrics-grid">
            <div className="metric">
              <span className="metric-label">训练期夏普</span>
              <span className="metric-value">{res.train_sharpe.toFixed(2)}</span>
            </div>
            {Object.entries({
              annual_return: ["验证期年化", true],
              excess_annual_return: ["超额年化", true],
              max_drawdown: ["最大回撤", false],
              sharpe: ["验证期夏普", false],
            }).map(([k, [label, pct]]) =>
              res.val_metrics[k] != null ? (
                <div key={k} className="metric">
                  <span className="metric-label">{label as string}</span>
                  <span className={`metric-value ${pct && res.val_metrics[k] >= 0 ? "up" : pct && res.val_metrics[k] < 0 ? "down" : ""}`}>
                    {pct ? `${(res.val_metrics[k] * 100).toFixed(2)}%` : res.val_metrics[k].toFixed(2)}
                  </span>
                </div>
              ) : null
            )}
          </div>
          <h3 style={{ fontSize: 14, color: "#9aa0aa", margin: "12px 0 6px" }}>最优参数（{res.n_trials} trials）</h3>
          <table style={{ maxWidth: 420 }}>
            <tbody>
              {Object.entries(FACTOR_LABELS).map(([k, label]) => (
                <tr key={k}>
                  <td className="muted">{label}</td>
                  <td><b>{res.best_params[`w_${k}`]?.toFixed(3) ?? "—"}</b></td>
                </tr>
              ))}
              <tr><td className="muted">持仓数量（Top N）</td><td><b>{res.best_params.top_n}</b></td></tr>
              <tr>
                <td className="muted">流动性门槛（万元）</td>
                <td><b>{res.best_params.min_amount_20d ? (res.best_params.min_amount_20d / 1e4).toFixed(0) : "—"}</b></td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {champion && (
        <>
          <h3 style={{ fontSize: 14, color: "#9aa0aa", margin: "12px 0 6px" }}>
            当前 Champion <span className="muted small">（晋升于 {new Date(champion.created_at).toLocaleString("zh-CN")}）</span>
          </h3>
          <table style={{ maxWidth: 420 }}>
            <tbody>
              {Object.entries(FACTOR_LABELS).map(([k, label]) => (
                <tr key={k}>
                  <td className="muted">{label}</td>
                  <td><b>{fmt(champion.params[`w_${k}`] as number)}</b></td>
                </tr>
              ))}
              <tr><td className="muted">Top N</td><td><b>{fmt(champion.params.top_n as number)}</b></td></tr>
              <tr>
                <td className="muted">验证期超额年化</td>
                <td className="up"><b>{champion.params.val_excess != null ? `${((champion.params.val_excess as number) * 100).toFixed(2)}%` : "—"}</b></td>
              </tr>
            </tbody>
          </table>
        </>
      )}
      {!champion && <p className="muted">尚无 champion，运行一次进化以建立基线。</p>}

      <p className="muted small" style={{ marginTop: 16 }}>
        Optuna 在训练期内最大化夏普，验证期超额年化超过当前 champion 才晋升 ·
        注意：当前成分快照存在幸存者偏差，相对比较有参考意义，绝对数字偏乐观
      </p>
    </div>
  );
}
