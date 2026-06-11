import { useEffect, useRef, useState } from "react";
import { dispose, init } from "klinecharts";
import { api, KlineResp, StockMeta } from "../api";

export default function Kline({ symbol, onSymbolChange }: { symbol: string; onSymbolChange: (s: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [stocks, setStocks] = useState<StockMeta[]>([]);
  const [input, setInput] = useState(symbol);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<StockMeta[]>("/stocks").then(setStocks).catch(() => {});
  }, []);

  useEffect(() => {
    setInput(symbol);
    if (!symbol || !ref.current) return;
    const el = ref.current;
    const chart = init(el);
    setError(null);
    api<KlineResp>(`/kline/${symbol}`)
      .then((d) => {
        setTitle(`${d.symbol} ${d.name}`);
        chart?.applyNewData(
          d.bars.map((b) => ({
            timestamp: Date.parse(b.date),
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
            volume: b.volume,
            turnover: b.amount ?? undefined,
          })),
        );
        chart?.createIndicator("MA", false, { id: "candle_pane" });
        chart?.createIndicator("VOL");
      })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
    return () => dispose(el);
  }, [symbol]);

  return (
    <div className="page fill">
      <div className="toolbar">
        <h2>行情 {title && <span className="muted">· {title}</span>}</h2>
        <input
          list="stock-list"
          value={input}
          placeholder="股票代码，如 600519"
          onChange={(e) => setInput(e.target.value.trim())}
          onKeyDown={(e) => e.key === "Enter" && input && onSymbolChange(input)}
        />
        <datalist id="stock-list">
          {stocks.map((s) => (
            <option key={s.symbol} value={s.symbol}>
              {s.name}
            </option>
          ))}
        </datalist>
        <button onClick={() => input && onSymbolChange(input)}>查看</button>
      </div>
      {error && <p className="error">{error}</p>}
      {!symbol && <p className="muted">输入代码或从「选股」页跳转查看 K 线。</p>}
      <div ref={ref} className="chart-fill" />
    </div>
  );
}
