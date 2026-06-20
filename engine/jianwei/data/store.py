"""DuckDB 行情存储：股票列表、日线、指数日线、同步状态。"""

from __future__ import annotations

import duckdb
import pandas as pd

from jianwei.config import market_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    symbol      VARCHAR PRIMARY KEY,
    name        VARCHAR,
    board       VARCHAR  -- main / chinext / star / bse
);
CREATE TABLE IF NOT EXISTS daily (
    symbol      VARCHAR,
    date        DATE,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    amount      DOUBLE,
    pct_chg     DOUBLE,
    turnover    DOUBLE,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS index_daily (
    symbol      VARCHAR,
    date        DATE,
    close       DOUBLE,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS sync_state (
    symbol      VARCHAR PRIMARY KEY,
    last_date   DATE,
    updated_at  TIMESTAMP DEFAULT now()
);
"""

DAILY_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]


def board_of(symbol: str) -> str:
    """按代码前缀判定板块（决定涨跌停幅度）。"""
    if symbol.startswith(("300", "301", "302")):
        return "chinext"  # 创业板 20%
    if symbol.startswith("688"):
        return "star"  # 科创板 20%
    if symbol.startswith(("4", "8", "92")):
        return "bse"  # 北交所 30%
    return "main"  # 主板 10%


class MarketStore:
    def __init__(self, path: str | None = None, read_only: bool = False):
        self.con = duckdb.connect(str(path or market_db_path()), read_only=read_only)
        if not read_only:
            self.con.execute(_SCHEMA)

    def close(self) -> None:
        self.con.close()

    # -- 写入 --------------------------------------------------------------

    def upsert_stocks(self, df: pd.DataFrame) -> None:
        df = df[["symbol", "name"]].copy()
        df["board"] = df["symbol"].map(board_of)
        self.con.register("_stocks", df)
        self.con.execute("INSERT OR REPLACE INTO stocks SELECT * FROM _stocks")
        self.con.unregister("_stocks")

    def upsert_daily(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = df[DAILY_COLS]
        self.con.register("_daily", df)
        self.con.execute("INSERT OR REPLACE INTO daily SELECT * FROM _daily")
        self.con.unregister("_daily")
        return len(df)

    def upsert_index_daily(self, symbol: str, df: pd.DataFrame) -> None:
        df = df[["date", "close"]].copy()
        df.insert(0, "symbol", symbol)
        self.con.register("_idx", df)
        self.con.execute("INSERT OR REPLACE INTO index_daily SELECT * FROM _idx")
        self.con.unregister("_idx")

    def set_last_date(self, symbol: str, last_date) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?, now())", [symbol, last_date]
        )

    # -- 读取 --------------------------------------------------------------

    def last_date(self, symbol: str):
        row = self.con.execute(
            "SELECT last_date FROM sync_state WHERE symbol = ?", [symbol]
        ).fetchone()
        return row[0] if row else None

    def stocks(self) -> pd.DataFrame:
        return self.con.execute("SELECT * FROM stocks ORDER BY symbol").df()

    def daily_panel(self, symbols: list[str] | None = None, start=None, end=None) -> pd.DataFrame:
        """长表：symbol, date, OHLCV...，供因子与回测使用。"""
        q = "SELECT * FROM daily WHERE 1=1"
        params: list = []
        if symbols:
            q += f" AND symbol IN ({','.join('?' * len(symbols))})"
            params += symbols
        if start is not None:
            q += " AND date >= ?"
            params.append(start)
        if end is not None:
            q += " AND date <= ?"
            params.append(end)
        q += " ORDER BY date, symbol"
        return self.con.execute(q, params).df()

    def index_series(self, symbol: str, start=None, end=None) -> pd.Series:
        q = "SELECT date, close FROM index_daily WHERE symbol = ?"
        params: list = [symbol]
        if start is not None:
            q += " AND date >= ?"
            params.append(start)
        if end is not None:
            q += " AND date <= ?"
            params.append(end)
        df = self.con.execute(q + " ORDER BY date", params).df()
        return df.set_index("date")["close"]

    def coverage(self) -> pd.DataFrame:
        """每只股票的数据覆盖情况，供质量检查。"""
        return self.con.execute(
            """
            SELECT symbol, min(date) AS first_date, max(date) AS last_date, count(*) AS bars
            FROM daily GROUP BY symbol ORDER BY symbol
            """
        ).df()
