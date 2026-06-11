"""东方财富 K 线接口直连客户端。

为何不经 akshare：东财 push2his 在部分网络链路上只接受 HTTP/2，
requests/urllib（HTTP/1.1）会被直接断连，httpx(http2) 则正常。
字段与 akshare stock_zh_a_hist 一致（同一上游接口）。
"""

from __future__ import annotations

import time

import httpx
import pandas as pd

_BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
# f51日期 f52开 f53收 f54高 f55低 f56量 f57额 f58振幅 f59涨跌幅 f60涨跌额 f61换手
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
_COLS = ["date", "open", "close", "high", "low", "volume", "amount",
         "amplitude", "pct_chg", "chg", "turnover"]

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            http2=True,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
    return _client


def _secid(symbol: str, is_index: bool = False) -> str:
    if is_index:
        return ("1." if symbol.startswith("0") else "0.") + symbol  # 000300 -> 1.000300
    market = "1" if symbol.startswith(("5", "6", "9")) else "0"
    return f"{market}.{symbol}"


def fetch_kline(
    symbol: str, start: str, end: str, *, is_index: bool = False, attempts: int = 3
) -> pd.DataFrame:
    """前复权日线。start/end 形如 '20240101'。返回空表表示无数据。"""
    params = {
        "secid": _secid(symbol, is_index),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": _FIELDS2,
        "klt": "101",          # 日线
        "fqt": "0" if is_index else "1",  # 个股前复权，指数不复权
        "beg": start,
        "end": end,
    }
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = _get_client().get(_BASE, params=params)
            r.raise_for_status()
            data = r.json().get("data")
            break
        except Exception as e:
            last = e
            time.sleep(1.0 * (i + 1))
    else:
        raise RuntimeError(f"eastmoney 请求重试 {attempts} 次后仍失败: {last}") from last

    if not data or not data.get("klines"):
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame([k.split(",") for k in data["klines"]], columns=_COLS)
    df["date"] = pd.to_datetime(df["date"])
    for c in _COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
