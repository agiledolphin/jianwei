"""行情数据源封装：东财（主，字段全）+ 腾讯（兜底），统一列名、带重试。

东财 push2his 在部分网络下被 WAF 拦截（HTTP/1.1 直接断连，频繁探测会升级为封 IP），
故会话首次取数时探测一次：通则全程用东财，不通则切换腾讯源。
腾讯源缺成交额/换手率：成交额以 收盘价×成交量 近似，换手率置空（当前因子未使用）。
"""

from __future__ import annotations

import time

import pandas as pd

DAILY_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]

_kline_source: str | None = None  # "em" | "tx"


def _retry(fn, attempts: int = 3, wait: float = 1.0):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # akshare 异常类型不稳定，统一兜底重试
            last = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"数据源请求重试 {attempts} 次后仍失败: {last}") from last


def kline_source() -> str:
    """探测并缓存本会话可用的 K 线源。"""
    global _kline_source
    if _kline_source is None:
        from jianwei.data.eastmoney import fetch_kline

        try:
            fetch_kline("000300", "20240101", "20240110", is_index=True, attempts=1)
            _kline_source = "em"
        except RuntimeError:
            _kline_source = "tx"
    return _kline_source


def list_stocks() -> pd.DataFrame:
    """全部 A 股代码与名称 -> [symbol, name]"""
    import akshare as ak

    df = _retry(ak.stock_info_a_code_name)
    return df.rename(columns={"code": "symbol"})[["symbol", "name"]]


def csi300_symbols() -> pd.DataFrame:
    """沪深300成分 -> [symbol, name]

    主源为中证指数官网（当前成分、名称最新）；新浪源仅兜底——它返回的
    是含重复的纳入历史列表（如 2005 年的"深发展A"），名称与成分均可能过时。
    """
    import akshare as ak

    try:
        df = _retry(lambda: ak.index_stock_cons_csindex(symbol="000300"), attempts=2)
        df = df.rename(columns={"成分券代码": "symbol", "成分券名称": "name"})
    except RuntimeError:
        df = _retry(lambda: ak.index_stock_cons(symbol="000300"))
        df = df.rename(columns={"品种代码": "symbol", "品种名称": "name"})
    return df[["symbol", "name"]].drop_duplicates("symbol").reset_index(drop=True)


def _tx_symbol(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _fetch_daily_tx(symbol: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak

    def call():
        try:
            return ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(symbol), start_date=start, end_date=end, adjust="qfq"
            )
        except IndexError:  # 腾讯源对空区间（如增量同步当日无新 bar）抛 IndexError
            return None

    df = _retry(call)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"amount": "volume"})  # 腾讯源该列实为成交量（手）
    df["date"] = pd.to_datetime(df["date"])
    df["volume"] = df["volume"] * 100
    df["amount"] = df["close"] * df["volume"]
    df["pct_chg"] = df["close"].pct_change() * 100
    df["turnover"] = float("nan")
    return df


def fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """单只股票前复权日线。start/end 形如 '20200101'。"""
    if kline_source() == "em":
        from jianwei.data.eastmoney import fetch_kline

        df = fetch_kline(symbol, start, end)
    else:
        df = _fetch_daily_tx(symbol, start, end)
    if df.empty:
        return pd.DataFrame()
    df["symbol"] = symbol
    return df[DAILY_COLS]


def _fetch_index_tx(symbol: str, start: str, end: str) -> pd.DataFrame:
    """腾讯 fqkline 原始接口取指数日线，按两年窗口分页（单次上限 640 根）。

    不走 akshare 的 stock_zh_index_daily_tx：其内部 get_tx_start_year
    辅助请求在连续调用场景下不稳定（IndexError）。
    """
    import requests

    # 指数前缀规则与个股不同：000xxx 为上证系指数，399xxx 为深证系
    sym = ("sh" if symbol.startswith("000") else "sz") + symbol
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    rows: list[list] = []
    cur = s
    while cur <= e:
        chunk_end = min(cur + pd.DateOffset(years=2), e)
        param = f"{sym},day,{cur:%Y-%m-%d},{chunk_end:%Y-%m-%d},640,qfq"

        def call(p=param):
            r = requests.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": p},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()["data"][sym]
            return d.get("qfqday") or d.get("day") or []

        rows += _retry(call)
        cur = chunk_end + pd.Timedelta(days=1)
        time.sleep(0.2)
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"])
    return df.drop_duplicates("date").sort_values("date")


def fetch_index_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """指数日线（如沪深300=000300）-> [date, close]"""
    if kline_source() == "em":
        from jianwei.data.eastmoney import fetch_kline

        df = fetch_kline(symbol, start, end, is_index=True)
    else:
        df = _fetch_index_tx(symbol, start, end)
    return df[["date", "close"]]
