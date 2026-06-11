import { useCallback, useEffect, useState } from "react";
import { api, PicksResp } from "../api";

export default function Picks({ onOpenKline }: { onOpenKline: (symbol: string) => void }) {
  const [top, setTop] = useState(10);
  const [data, setData] = useState<PicksResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (n: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<PicksResp>(`/picks?top=${n}`));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(top);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page">
      <div className="toolbar">
        <h2>多因子选股</h2>
        <label>
          Top
          <select value={top} onChange={(e) => setTop(Number(e.target.value))}>
            {[5, 10, 20, 30].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => load(top)} disabled={loading}>
          {loading ? "计算中…" : "重新选股"}
        </button>
        {data && <span className="muted">信号日：{data.date}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {data && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>代码</th>
              <th>名称</th>
              <th>综合得分</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.picks.map((p, i) => (
              <tr key={p.symbol}>
                <td>{i + 1}</td>
                <td className="mono">{p.symbol}</td>
                <td>{p.name ?? "—"}</td>
                <td className={p.score >= 0 ? "up" : "down"}>{p.score.toFixed(3)}</td>
                <td>
                  <button className="link" onClick={() => onOpenKline(p.symbol)}>
                    K线 →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">
        因子：动量20/60 · 反转5 · 低波动60 · 流动性20，截面 z-score 加权；结果仅供研究，不构成投资建议。
      </p>
    </div>
  );
}
