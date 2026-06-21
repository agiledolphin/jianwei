"""行情数据源封装：三源自动选择，统一列名、带重试。

数据源优先级（可通过 JIANWEI_DATA_SOURCE 环境变量覆盖）：
  tx  腾讯财经（默认）——无 WAF，稳定；缺成交额/换手率（以收盘价×成交量近似）
  bs  Baostock ———有真实成交额/换手率，专为量化设计，无 WAF
  em  东方财富（可选）——字段最全，但部分网络下被 WAF 拦截

em 模式下每会话探测一次，探测失败或请求中途断连均自动切 tx。
"""

from __future__ import annotations

import os
import time

import pandas as pd

DAILY_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]

_kline_source: str | None = None  # "tx" | "bs" | "em"
_bs_logged_in: bool = False


def _retry(fn, attempts: int = 3, wait: float = 1.0):
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"数据源请求重试 {attempts} 次后仍失败: {last}") from last


# ---------------------------------------------------------------------------
# 数据源探测与选择
# ---------------------------------------------------------------------------

def kline_source() -> str:
    """返回本会话 K 线数据源：tx | bs | em。

    读取 JIANWEI_DATA_SOURCE 环境变量（默认 tx）。
    em 模式下探测东财可用性，探测失败自动降级到 tx。
    """
    global _kline_source
    if _kline_source is None:
        src = os.environ.get("JIANWEI_DATA_SOURCE", "tx").lower()
        if src == "em":
            from jianwei.data.eastmoney import fetch_kline
            try:
                fetch_kline("000300", "20240101", "20240110", is_index=True, attempts=1)
                _kline_source = "em"
            except RuntimeError:
                _kline_source = "tx"
        elif src == "bs":
            _kline_source = "bs"
        else:
            _kline_source = "tx"
    return _kline_source


# ---------------------------------------------------------------------------
# 成分股 / 股票列表
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 腾讯源（tx）
# ---------------------------------------------------------------------------

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
        except IndexError:  # 空日期区间抛 IndexError
            return None

    df = _retry(call)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"amount": "volume"})  # 腾讯该列实为成交量（手）
    df["date"] = pd.to_datetime(df["date"])
    df["volume"] = df["volume"] * 100
    df["amount"] = df["close"] * df["volume"]  # 近似成交额
    df["pct_chg"] = df["close"].pct_change() * 100
    df["turnover"] = float("nan")
    return df


def _fetch_index_tx(symbol: str, start: str, end: str) -> pd.DataFrame:
    """腾讯 fqkline 原始接口取指数日线，按两年窗口分页（单次上限 640 根）。"""
    import requests

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


# ---------------------------------------------------------------------------
# Baostock 源（bs）
# ---------------------------------------------------------------------------

def _bs_login() -> None:
    global _bs_logged_in
    if not _bs_logged_in:
        import baostock as bs
        result = bs.login()
        if result.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {result.error_msg}")
        _bs_logged_in = True


def _bs_symbol(symbol: str) -> str:
    """转换为 baostock 格式：sh.600519 / sz.000001"""
    if symbol.startswith(("5", "6", "9")):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def _bs_to_df(rs, fields: list[str]) -> pd.DataFrame:
    """baostock 0.9.x 与 pandas 2.x 不兼容（内部用了已删除的 df.append）。
    手动迭代行，避开 rs.get_data()。"""
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=fields) if rows else pd.DataFrame(columns=fields)


def _fetch_daily_bs(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Baostock 前复权日线——有真实成交额和换手率。"""
    import baostock as bs

    _bs_login()
    fields = ["date", "open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
    rs = bs.query_history_k_data_plus(
        _bs_symbol(symbol),
        ",".join(fields),
        start_date=pd.Timestamp(start).strftime("%Y-%m-%d"),
        end_date=pd.Timestamp(end).strftime("%Y-%m-%d"),
        frequency="d",
        adjustflag="2",  # 前复权
    )
    if rs.error_code != "0":
        return pd.DataFrame()

    df = _bs_to_df(rs, fields)
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "amount", "pctChg", "turn"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100  # 手→股
    df["pct_chg"] = df["pctChg"]
    df["turnover"] = df["turn"]
    # 过滤停牌日（open 为 0 或 NaN）
    df = df[df["open"].notna() & (df["open"] > 0)]
    return df[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]]


def _fetch_index_bs(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Baostock 取指数日线。"""
    import baostock as bs

    _bs_login()
    fields = ["date", "close"]
    rs = bs.query_history_k_data_plus(
        f"sh.{symbol}",
        ",".join(fields),
        start_date=pd.Timestamp(start).strftime("%Y-%m-%d"),
        end_date=pd.Timestamp(end).strftime("%Y-%m-%d"),
        frequency="d",
    )
    if rs.error_code != "0":
        return pd.DataFrame(columns=fields)
    df = _bs_to_df(rs, fields)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df[["date", "close"]].dropna()


# ---------------------------------------------------------------------------
# 统一出口
# ---------------------------------------------------------------------------

def fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """单只股票前复权日线。start/end 形如 '20200101'。"""
    global _kline_source
    src = kline_source()

    if src == "em":
        from jianwei.data.eastmoney import fetch_kline
        try:
            df = fetch_kline(symbol, start, end)
        except RuntimeError:
            _kline_source = "tx"  # 东财 WAF 触发，永久切换腾讯
            df = _fetch_daily_tx(symbol, start, end)
    elif src == "bs":
        df = _fetch_daily_bs(symbol, start, end)
    else:
        df = _fetch_daily_tx(symbol, start, end)

    if df.empty:
        return pd.DataFrame()
    df["symbol"] = symbol
    return df[DAILY_COLS]


def fetch_index_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """指数日线（如沪深300=000300）-> [date, close]"""
    src = kline_source()
    if src == "em":
        from jianwei.data.eastmoney import fetch_kline
        df = fetch_kline(symbol, start, end, is_index=True)
    elif src == "bs":
        df = _fetch_index_bs(symbol, start, end)
    else:
        df = _fetch_index_tx(symbol, start, end)
    return df[["date", "close"]]
