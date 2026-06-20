"""模拟盘撮合引擎（SimBroker）。

复用回测的约束语义：
- 信号日（选股日）次日开盘价撮合（T+1 约束）
- 涨跌停 / 停牌 / 整手 / 佣金 / 印花税 / 滑点与回测保持一致
- 持仓与流水落 SQLite（sim_positions / sim_trades），供 API 实时查询

与回测的区别：
- 每天只撮合一次（当天 15:35 同步后触发），没有历史回放
- NAV 按最新收盘价实时估算
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, UTC

import pandas as pd

from jianwei.backtest.constraints import TradeCost, can_sell_t1, limit_ratio
from jianwei.config import app_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_positions (
    symbol      TEXT PRIMARY KEY,
    shares      INTEGER NOT NULL,
    buy_date    TEXT NOT NULL,
    avg_cost    REAL NOT NULL        -- 含手续费摊薄成本（元/股）
);
CREATE TABLE IF NOT EXISTS sim_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,       -- buy | sell
    shares      INTEGER NOT NULL,
    price       REAL NOT NULL,
    fee         REAL NOT NULL,
    realized_pnl REAL,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sim_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""

_INITIAL_CASH = 1_000_000.0


class SimBroker:
    def __init__(self, path: str | None = None, cost: TradeCost | None = None):
        self.con = sqlite3.connect(str(path or app_db_path()))
        self.con.executescript(_SCHEMA)
        self.cost = cost or TradeCost()
        # 初始化现金（仅首次）
        self.con.execute(
            "INSERT OR IGNORE INTO sim_state VALUES ('cash', ?)",
            (str(_INITIAL_CASH),),
        )
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # -- 读取 ----------------------------------------------------------------

    @property
    def cash(self) -> float:
        row = self.con.execute("SELECT value FROM sim_state WHERE key='cash'").fetchone()
        return float(row[0]) if row else _INITIAL_CASH

    @cash.setter
    def cash(self, v: float) -> None:
        self.con.execute("INSERT OR REPLACE INTO sim_state VALUES ('cash', ?)", (str(v),))

    def positions(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT symbol, shares, buy_date, avg_cost FROM sim_positions"
        ).fetchall()
        return [dict(zip(["symbol", "shares", "buy_date", "avg_cost"], r)) for r in rows]

    def trades(self, limit: int = 50) -> list[dict]:
        rows = self.con.execute(
            "SELECT id, date, symbol, side, shares, price, fee, realized_pnl, note "
            "FROM sim_trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        keys = ["id", "date", "symbol", "side", "shares", "price", "fee", "realized_pnl", "note"]
        return [dict(zip(keys, r)) for r in rows]

    def nav(self) -> dict:
        """当前净值：现金 + Σ 持仓市值（按 DuckDB 最新收盘价）。"""
        from jianwei.data.store import MarketStore

        pos = self.positions()
        if not pos:
            return {"cash": self.cash, "market_value": 0.0, "nav": self.cash, "positions": []}

        symbols = [p["symbol"] for p in pos]
        store = MarketStore(read_only=True)
        try:
            panel = store.daily_panel(symbols=symbols)
        finally:
            store.close()

        last_close: dict[str, float] = {}
        if not panel.empty:
            latest = panel.groupby("symbol")["close"].last()
            last_close = latest.to_dict()

        total_mv = 0.0
        for p in pos:
            px = last_close.get(p["symbol"], p["avg_cost"])
            mv = p["shares"] * px
            p["last_price"] = px
            p["market_value"] = mv
            p["pnl"] = mv - p["shares"] * p["avg_cost"]
            total_mv += mv

        return {
            "cash": self.cash,
            "market_value": total_mv,
            "nav": self.cash + total_mv,
            "positions": pos,
        }

    # -- 撮合 ---------------------------------------------------------------

    def execute_picks(self, picks: pd.DataFrame) -> dict:
        """根据最新选股结果调仓（等权）。

        picks: DataFrame with columns [symbol, score, ...]
        今日为信号日，撮合价用今日收盘价近似（无法预知明日开盘价，
        用收盘价作为估计，真实模拟盘应在次日开盘后撮合）。
        """
        from jianwei.data.store import MarketStore

        today = date.today().isoformat()
        target_syms = list(picks["symbol"])

        # 取今日收盘价
        store = MarketStore()
        try:
            all_syms = list(set(target_syms) | {p["symbol"] for p in self.positions()})
            panel = store.daily_panel(symbols=all_syms)
            meta = store.stocks()
        finally:
            store.close()

        if panel.empty:
            return {"sold": [], "bought": [], "skipped": [], "note": "无行情数据"}

        name_map = dict(zip(meta["symbol"], meta["name"]))
        board_map = dict(zip(meta["symbol"], meta["board"]))

        # 最新两日收盘价（用于涨跌停判断）
        last2 = (
            panel.sort_values("date")
            .groupby("symbol")
            .tail(2)
            [["symbol", "date", "close"]]
        )

        def get_close(sym: str) -> float | None:
            rows = last2[last2["symbol"] == sym].sort_values("date")
            return float(rows["close"].iloc[-1]) if not rows.empty else None

        def get_prev_close(sym: str) -> float | None:
            rows = last2[last2["symbol"] == sym].sort_values("date")
            return float(rows["close"].iloc[-2]) if len(rows) >= 2 else None

        def open_ret_approx(sym: str) -> float | None:
            px, pc = get_close(sym), get_prev_close(sym)
            if px is None or pc is None or pc == 0:
                return None
            return px / pc - 1

        cur_pos = {p["symbol"]: p for p in self.positions()}
        cash = self.cash
        sold, bought, skipped = [], [], []

        # 1. 先卖出不在目标内的持仓
        for sym, p in list(cur_pos.items()):
            if sym in target_syms:
                continue
            r = open_ret_approx(sym)
            if r is None:
                skipped.append({"symbol": sym, "reason": "停牌"})
                continue
            board = board_map.get(sym, "main")
            name = name_map.get(sym, "")
            lim = limit_ratio(board, name)
            if r <= -lim + 2e-3:
                skipped.append({"symbol": sym, "reason": "跌停"})
                continue
            if not can_sell_t1(pd.Timestamp(p["buy_date"]), pd.Timestamp(today)):
                skipped.append({"symbol": sym, "reason": "T+1"})
                continue

            px = (get_close(sym) or p["avg_cost"]) * (1 - self.cost.slippage)
            value = p["shares"] * px
            fee = self.cost.sell_fee(value)
            pnl = value - fee - p["shares"] * p["avg_cost"]
            cash += value - fee
            self.con.execute("DELETE FROM sim_positions WHERE symbol=?", (sym,))
            self._record_trade(today, sym, "sell", p["shares"], px, fee, pnl)
            sold.append(sym)

        # 2. 计算可用资金（含未变动持仓市值）
        remaining_pos = {s: p for s, p in cur_pos.items() if s not in sold and s not in [x["symbol"] for x in skipped if x["reason"] == "T+1"]}
        mv = sum((get_close(s) or p["avg_cost"]) * p["shares"] for s, p in remaining_pos.items())
        nav_now = cash + mv
        buys = [s for s in target_syms if s not in remaining_pos]
        if not buys:
            self.cash = cash
            self.con.commit()
            return {"sold": sold, "bought": bought, "skipped": skipped}

        per_name = nav_now / max(len(target_syms), 1)

        # 3. 买入目标内未持有的
        for sym in buys:
            r = open_ret_approx(sym)
            if r is None:
                skipped.append({"symbol": sym, "reason": "停牌"})
                continue
            board = board_map.get(sym, "main")
            name = name_map.get(sym, "")
            lim = limit_ratio(board, name)
            if r >= lim - 2e-3:
                skipped.append({"symbol": sym, "reason": "涨停"})
                continue
            px = (get_close(sym) or 0) * (1 + self.cost.slippage)
            if px <= 0:
                skipped.append({"symbol": sym, "reason": "无价格"})
                continue
            budget = min(per_name, cash)
            shares = int(budget / (px * 100)) * 100
            if shares <= 0:
                skipped.append({"symbol": sym, "reason": "资金不足"})
                continue
            value = shares * px
            fee = self.cost.buy_fee(value)
            if value + fee > cash:
                shares -= 100
                if shares <= 0:
                    skipped.append({"symbol": sym, "reason": "资金不足"})
                    continue
                value = shares * px
                fee = self.cost.buy_fee(value)
            cash -= value + fee
            avg_cost = (value + fee) / shares
            self.con.execute(
                "INSERT OR REPLACE INTO sim_positions VALUES (?, ?, ?, ?)",
                (sym, shares, today, avg_cost),
            )
            self._record_trade(today, sym, "buy", shares, px, fee, 0.0)
            bought.append(sym)

        self.cash = cash
        self.con.commit()
        return {"sold": sold, "bought": bought, "skipped": skipped}

    def _record_trade(
        self, dt: str, sym: str, side: str, shares: int,
        price: float, fee: float, pnl: float, note: str | None = None,
    ) -> None:
        self.con.execute(
            "INSERT INTO sim_trades (date, symbol, side, shares, price, fee, realized_pnl, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dt, sym, side, shares, price, fee, pnl, note, datetime.now(UTC).isoformat()),
        )

    def reset(self, cash: float = _INITIAL_CASH) -> None:
        """重置模拟盘（清空持仓和流水）。"""
        self.con.executescript(
            "DELETE FROM sim_positions; DELETE FROM sim_trades;"
        )
        self.cash = cash
        self.con.commit()
