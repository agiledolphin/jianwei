import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { api, BacktestReq, BacktestResp } from "../api";

const METRIC_LABELS: [string, string, (v: number) => string][] = [
  ["total_return", "累计收益", (v) => `${(v * 100).toFixed(2)}%`],
  ["annual_return", "年化收益", (v) => `${(v * 100).toFixed(2)}%`],
  ["benchmark_annual_return", "基准年化", (v) => `${(v * 100).toFixed(2)}%`],
  ["excess_annual_return", "超额年化", (v) => `${(v * 100).toFixed(2)}%`],
  ["max_drawdown", "最大回撤", (v) => `${(v * 100).toFixed(2)}%`],
  ["sharpe", "夏普比率", (v) => v.toFixed(2)],
  ["calmar", "卡玛比率", (v) => v.toFixed(2)],
  ["win_rate", "胜率(平仓)", (v) => `${(v * 100).toFixed(1)}%`],
  ["closed_trades", "平仓笔数", (v) => String(v)],
  ["annual_turnover", "年换手(单边)", (v) => `${v.toFixed(1)}x`],
  ["total_fees", "总费用", (v) => `¥${Math.round(v).toLocaleString()}`],
  ["days", "交易日数", (v) => String(v)],
];

export default function Backtest() {
  const chartRef = useRef<HTMLDivElement>(null);
  const [form, setForm] = useState<BacktestReq>({ start: "2021-01-01", top: 10, rebalance: "W", cash: 1_000_000 });
  const [res, setRes] = useState<BacktestResp | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      setRes(await api<BacktestResp>("/backtest", { method: "POST", body: JSON.stringify(form) }));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    if (!res || !chartRef.current) return;
    const chart = echarts.init(chartRef.current);
    const dates = res.nav.map((p) => p.date);
    const base = res.nav[0]?.nav || 1;
    const series: echarts.SeriesOption[] = [
      {
        name: "策略",
        type: "line",
        showSymbol: false,
        data: res.nav.map((p) => +(p.nav / base).toFixed(4)),
        lineStyle: { width: 2 },
      },
    ];
    if (res.nav[0]?.benchmark != null) {
      const bbase = res.nav[0].benchmark!;
      series.push({
        name: "沪深300",
        type: "line",
        showSymbol: false,
        data: res.nav.map((p) => +((p.benchmark ?? bbase) / bbase).toFixed(4)),
        lineStyle: { width: 1.5, type: "dashed" },
      });
    }
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { top: 4 },
      grid: { left: 56, right: 16, top: 36, bottom: 28 },
      xAxis: { type: "category", data: dates },
      yAxis: { type: "value", scale: true, name: "净值" },
      series,
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [res]);

  return (
    <div className="page">
      <div className="toolbar">
        <h2>回测</h2>
        <label>
          开始
          <input
            type="date"
            value={form.start ?? ""}
            onChange={(e) => setForm({ ...form, start: e.target.value || undefined })}
          />
        </label>
        <label>
          结束
          <input
            type="date"
            value={form.end ?? ""}
            onChange={(e) => setForm({ ...form, end: e.target.value || undefined })}
          />
        </label>
        <label>
          Top
          <select value={form.top} onChange={(e) => setForm({ ...form, top: Number(e.target.value) })}>
            {[5, 10, 20, 30].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          调仓
          <select
            value={form.rebalance}
            onChange={(e) => setForm({ ...form, rebalance: e.target.value as "W" | "M" })}
          >
            <option value="W">每周</option>
            <option value="M">每月</option>
          </select>
        </label>
        <button onClick={run} disabled={running}>
          {running ? "回测中…" : "运行回测"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {res && (
        <>
          <div className="metrics-grid">
            {METRIC_LABELS.filter(([k]) => res.metrics[k] != null).map(([k, label, fmt]) => (
              <div key={k} className="metric">
                <span className="metric-label">{label}</span>
                <span
                  className={
                    "metric-value" +
                    (k.includes("return") || k === "win_rate" ? (res.metrics[k] >= 0 ? " up" : " down") : "")
                  }
                >
                  {fmt(res.metrics[k])}
                </span>
              </div>
            ))}
          </div>
          <div ref={chartRef} className="chart-nav" />
          <p className="muted small">
            已记录 run_id={res.run_id} · 含 T+1 / 涨跌停 / 佣金印花税 / 滑点 / 停牌 / 整手约束 ·
            成分股为当前快照，存在幸存者偏差，结果偏乐观。
          </p>
        </>
      )}
      {!res && !running && <p className="muted">设定参数后运行回测，结果将与沪深300 基准对比。</p>}
    </div>
  );
}
