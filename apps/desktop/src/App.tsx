import { useEffect, useState } from "react";
import { waitReady } from "./api";
import Picks from "./pages/Picks";
import Kline from "./pages/Kline";
import Backtest from "./pages/Backtest";
import Data from "./pages/Data";
import "./App.css";

type Page = "picks" | "kline" | "backtest" | "data";

const NAV: { key: Page; label: string }[] = [
  { key: "picks", label: "选股" },
  { key: "kline", label: "行情" },
  { key: "backtest", label: "回测" },
  { key: "data", label: "数据" },
];

export default function App() {
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("picks");
  const [klineSymbol, setKlineSymbol] = useState("");

  useEffect(() => {
    waitReady()
      .then(() => setReady(true))
      .catch((e) => setBootError(String(e instanceof Error ? e.message : e)));
  }, []);

  if (bootError) {
    return (
      <main className="boot">
        <h1>见微 Jianwei</h1>
        <p className="error">引擎连接失败：{bootError}</p>
      </main>
    );
  }
  if (!ready) {
    return (
      <main className="boot">
        <h1>见微 Jianwei</h1>
        <p className="muted">正在启动引擎…</p>
      </main>
    );
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1 className="brand">
          见微 <span className="muted">Jianwei</span>
        </h1>
        <nav>
          {NAV.map((n) => (
            <button
              key={n.key}
              className={page === n.key ? "nav-item active" : "nav-item"}
              onClick={() => setPage(n.key)}
            >
              {n.label}
            </button>
          ))}
        </nav>
        <footer className="muted small">仅供研究 · 不构成投资建议</footer>
      </aside>
      <main className="content">
        {page === "picks" && (
          <Picks
            onOpenKline={(s) => {
              setKlineSymbol(s);
              setPage("kline");
            }}
          />
        )}
        {page === "kline" && <Kline symbol={klineSymbol} onSymbolChange={setKlineSymbol} />}
        {page === "backtest" && <Backtest />}
        {page === "data" && <Data />}
      </main>
    </div>
  );
}
