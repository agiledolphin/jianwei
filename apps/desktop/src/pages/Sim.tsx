import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

interface SimNav {
  cash: number;
  market_value: number;
  nav: number;
  positions: {
    symbol: string;
    shares: number;
    buy_date: string;
    avg_cost: number;
    last_price?: number;
    market_value?: number;
    pnl?: number;
  }[];
}

interface SimTrade {
  id: number;
  date: string;
  symbol: string;
  side: string;
  shares: number;
  price: number;
  fee: number;
  realized_pnl: number | null;
}

interface ScheduleStatus {
  running: boolean;
  next_run: string | null;
  jobs: string[];
}

export default function Sim({ onOpenKline }: { onOpenKline: (s: string) => void }) {
  const [nav, setNav] = useState<SimNav | null>(null);
  const [trades, setTrades] = useState<SimTrade[]>([]);
  const [schedule, setSchedule] = useState<ScheduleStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const [n, t, s] = await Promise.all([
        api<SimNav>("/sim/nav"),
        api<SimTrade[]>("/sim/trades?limit=20"),
        api<ScheduleStatus>("/schedule/status"),
      ]);
      setNav(n);
      setTrades(t);
      setSchedule(s);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function execute() {
    setLoading(true);
    setError(null);
    try {
      await api("/sim/execute", { method: "POST" });
      await load();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  async function trigger() {
    setError(null);
    try {
      await api("/schedule/trigger", { method: "POST" });
      setError(null);
      setTimeout(load, 3000);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function reset() {
    if (!window.confirm("确认重置模拟盘？将清空所有持仓和流水。")) return;
    try {
      await api("/sim/reset", { method: "POST" });
      await load();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  const initCash = 1_000_000;
  const totalReturn = nav ? (nav.nav - initCash) / initCash : null;

  return (
    <div className="page">
      <div className="toolbar">
        <h2>模拟盘</h2>
        <button onClick={execute} disabled={loading}>{loading ? "撮合中…" : "立即撮合"}</button>
        <button onClick={trigger}>触发每日任务</button>
        <button onClick={load}>刷新</button>
        <button onClick={reset} className="link">重置</button>
      </div>
      {error && <p className="error">{error}</p>}

      {schedule && (
        <p className="muted small">
          调度器：{schedule.running ? "运行中" : "未启动"} ·
          下次自动触发：{schedule.next_run ? new Date(schedule.next_run).toLocaleString("zh-CN") : "—"}
          （每日 15:35）
        </p>
      )}

      {nav && (
        <div className="metrics-grid" style={{ marginBottom: 16 }}>
          <div className="metric">
            <span className="metric-label">总资产（NAV）</span>
            <span className="metric-value">¥{nav.nav.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}</span>
          </div>
          <div className="metric">
            <span className="metric-label">累计收益</span>
            <span className={`metric-value ${totalReturn != null && totalReturn >= 0 ? "up" : "down"}`}>
              {totalReturn != null ? `${totalReturn >= 0 ? "+" : ""}${(totalReturn * 100).toFixed(2)}%` : "—"}
            </span>
          </div>
          <div className="metric">
            <span className="metric-label">持仓市值</span>
            <span className="metric-value">¥{nav.market_value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}</span>
          </div>
          <div className="metric">
            <span className="metric-label">可用现金</span>
            <span className="metric-value">¥{nav.cash.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}</span>
          </div>
        </div>
      )}

      {nav && nav.positions.length > 0 && (
        <>
          <h3 style={{ fontSize: 14, color: "#9aa0aa", margin: "0 0 8px" }}>当前持仓</h3>
          <table>
            <thead>
              <tr>
                <th>代码</th>
                <th>股数</th>
                <th>成本</th>
                <th>最新价</th>
                <th>市值</th>
                <th>浮动盈亏</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {nav.positions.map((p) => (
                <tr key={p.symbol}>
                  <td className="mono">{p.symbol}</td>
                  <td>{p.shares}</td>
                  <td>{p.avg_cost.toFixed(2)}</td>
                  <td>{p.last_price?.toFixed(2) ?? "—"}</td>
                  <td>{p.market_value != null ? `¥${p.market_value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}` : "—"}</td>
                  <td className={p.pnl != null && p.pnl >= 0 ? "up" : "down"}>
                    {p.pnl != null ? `${p.pnl >= 0 ? "+" : ""}¥${p.pnl.toFixed(0)}` : "—"}
                  </td>
                  <td>
                    <button className="link" onClick={() => onOpenKline(p.symbol)}>K线 →</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {nav && nav.positions.length === 0 && (
        <p className="muted">暂无持仓，点击「立即撮合」按最新选股建仓。</p>
      )}

      {trades.length > 0 && (
        <>
          <h3 style={{ fontSize: 14, color: "#9aa0aa", margin: "16px 0 8px" }}>最近交易流水</h3>
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>代码</th>
                <th>方向</th>
                <th>股数</th>
                <th>价格</th>
                <th>手续费</th>
                <th>实现盈亏</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td>{t.date}</td>
                  <td className="mono">{t.symbol}</td>
                  <td className={t.side === "buy" ? "up" : "down"}>{t.side === "buy" ? "买入" : "卖出"}</td>
                  <td>{t.shares}</td>
                  <td>{t.price.toFixed(2)}</td>
                  <td>¥{t.fee.toFixed(2)}</td>
                  <td className={t.realized_pnl != null && t.realized_pnl >= 0 ? "up" : "down"}>
                    {t.realized_pnl != null && t.realized_pnl !== 0
                      ? `${t.realized_pnl >= 0 ? "+" : ""}¥${t.realized_pnl.toFixed(0)}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <p className="muted small" style={{ marginTop: 12 }}>
        用收盘价近似撮合价（真实盘须次日开盘后按实际价格撮合）·
        含 T+1 / 涨跌停 / 佣金印花税 / 整手约束 · 仅供学习研究
      </p>
    </div>
  );
}
