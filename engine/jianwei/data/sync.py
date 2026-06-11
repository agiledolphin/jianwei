"""增量同步：AkShare -> DuckDB。"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from jianwei.data import source_akshare as src
from jianwei.data.store import MarketStore

DEFAULT_START = "20190101"
BENCH_INDEX = "000300"  # 沪深300 作为缺省基准


def _fmt(d) -> str:
    return pd.Timestamp(d).strftime("%Y%m%d")


def sync(
    store: MarketStore,
    universe: str = "csi300",
    start: str = DEFAULT_START,
    end: str | None = None,
    limit: int | None = None,
    log=print,
) -> dict:
    """同步股票列表、日线（增量）与基准指数。

    universe: csi300 | all；limit 仅取前 N 只（调试用）。
    """
    end = end or _fmt(date.today())
    stocks = src.csi300_symbols() if universe == "csi300" else src.list_stocks()
    if limit:
        stocks = stocks.head(limit)
    store.upsert_stocks(stocks)
    log(f"股票列表 {len(stocks)} 只（universe={universe}）")

    total_rows, failed = 0, []
    for i, row in enumerate(stocks.itertuples(index=False), 1):
        sym = row.symbol
        last = store.last_date(sym)
        s = _fmt(pd.Timestamp(last) + timedelta(days=1)) if last else start
        if s > end:
            continue
        time.sleep(0.2)  # 节流，避免触发数据源限频
        try:
            df = src.fetch_daily(sym, s, end)
        except RuntimeError as e:
            failed.append(sym)
            log(f"  [{i}/{len(stocks)}] {sym} 失败: {e}")
            continue
        n = store.upsert_daily(df)
        if not df.empty:
            store.set_last_date(sym, df["date"].max().date())
        total_rows += n
        if i % 25 == 0 or i == len(stocks):
            log(f"  [{i}/{len(stocks)}] 累计 {total_rows} 行")

    idx = src.fetch_index_daily(BENCH_INDEX, start, end)
    store.upsert_index_daily(BENCH_INDEX, idx)
    log(f"基准指数 {BENCH_INDEX} {len(idx)} 行")
    return {"stocks": len(stocks), "rows": total_rows, "failed": failed}


def quality_report(store: MarketStore) -> pd.DataFrame:
    """数据质量：覆盖区间、bar 数、距全市场最新日期的滞后天数。"""
    cov = store.coverage()
    if cov.empty:
        return cov
    latest = cov["last_date"].max()
    cov["lag_days"] = (pd.Timestamp(latest) - pd.to_datetime(cov["last_date"])).dt.days
    return cov.sort_values("lag_days", ascending=False)
