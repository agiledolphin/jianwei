/** 引擎 HTTP 客户端：经 Tauri 命令取得 sidecar 的 {port, token} 后直连。 */

import { invoke } from "@tauri-apps/api/core";

type EngineInfo = { port: number; token: string };

let cached: EngineInfo | null = null;

async function engineInfo(): Promise<EngineInfo> {
  if (!cached) {
    try {
      cached = await invoke<EngineInfo>("engine_info");
    } catch {
      // 浏览器直跑 vite（无 Tauri）时的开发兜底：手动 `jianwei serve --port 8765`
      cached = { port: 8765, token: "" };
    }
  }
  return cached;
}

export async function waitReady(timeoutMs = 30_000): Promise<void> {
  const { port } = await engineInfo();
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/health`);
      if (r.ok) return;
    } catch {
      /* 引擎尚未就绪 */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("引擎启动超时");
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const { port, token } = await engineInfo();
  const r = await fetch(`http://127.0.0.1:${port}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(detail);
  }
  return r.json();
}

// -- 类型 --------------------------------------------------------------------

export interface StockMeta {
  symbol: string;
  name: string;
  board: string;
}

export interface PickRow {
  symbol: string;
  name: string | null;
  score: number;
}

export interface PicksResp {
  date: string;
  picks: PickRow[];
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number | null;
}

export interface KlineResp {
  symbol: string;
  name: string;
  bars: Bar[];
}

export interface BacktestReq {
  start?: string;
  end?: string;
  top: number;
  rebalance: "W" | "M";
  cash: number;
}

export interface NavPoint {
  date: string;
  nav: number;
  benchmark?: number;
}

export interface BacktestResp {
  run_id: number;
  metrics: Record<string, number>;
  nav: NavPoint[];
  trades: Record<string, unknown>[];
}

export interface SyncStatus {
  running: boolean;
  log: string[];
  result: { stocks: number; rows: number; failed: string[] } | null;
  error: string | null;
}

export interface QualityResp {
  stocks: number;
  stale: number;
  rows: { symbol: string; first_date: string; last_date: string; bars: number; lag_days: number }[];
}
