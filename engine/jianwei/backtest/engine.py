"""日频调仓回测引擎。

撮合规则（与未来模拟盘共用同一套约束语义）：
- 信号日收盘后选股，次一交易日开盘价成交（含滑点）
- T+1：当日买入不可当日卖出
- 涨停开盘不可买入、跌停开盘不可卖出（按板块/ST 区分幅度）
- 停牌（当日无 bar）不可交易，估值沿用最近收盘价
- 整手交易（100 股），佣金最低 5 元，卖出收印花税
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from jianwei.backtest.constraints import TradeCost, can_sell_t1, limit_ratio


@dataclass
class Position:
    shares: int
    buy_date: pd.Timestamp
    avg_cost: float  # 含费摊薄成本，用于胜率统计


@dataclass
class BacktestResult:
    nav: pd.Series
    trades: pd.DataFrame
    positions: dict[str, Position]
    benchmark: pd.Series | None = None


@dataclass
class Backtester:
    meta: pd.DataFrame  # stocks 表：symbol, name, board
    cost: TradeCost = field(default_factory=TradeCost)
    rebalance: str = "W"  # W=周末信号下周一调仓, M=月末
    initial_cash: float = 1_000_000.0
    limit_eps: float = 2e-3  # 涨跌幅判定容差（前复权价格非精确价位）

    def __post_init__(self) -> None:
        self._name = dict(zip(self.meta["symbol"], self.meta["name"].fillna("")))
        self._board = dict(zip(self.meta["symbol"], self.meta["board"]))

    def _limit(self, sym: str) -> float:
        return limit_ratio(self._board.get(sym, "main"), self._name.get(sym, ""))

    def run(
        self,
        panel: dict[str, pd.DataFrame],
        scores: pd.DataFrame,
        top_n: int,
        benchmark: pd.Series | None = None,
    ) -> BacktestResult:
        open_, close = panel["open"], panel["close"]
        idx = close.index
        prev_close = close.ffill().shift(1)
        exec_map = self._exec_days(idx)

        cash = self.initial_cash
        pos: dict[str, Position] = {}
        last_px: dict[str, float] = {}
        trades: list[dict] = []
        nav = pd.Series(0.0, index=idx)

        for t in idx:
            sig_day = exec_map.get(t)
            if sig_day is not None:
                row = scores.loc[sig_day].dropna().sort_values(ascending=False)
                target = list(row.head(top_n).index)
                cash = self._rebalance(t, target, pos, cash, open_, prev_close, last_px, trades)
            # 更新最新价并结算当日净值（停牌沿用最近价）
            day_close = close.loc[t]
            for sym, px in day_close.dropna().items():
                last_px[sym] = px
            nav[t] = cash + sum(p.shares * last_px.get(s, p.avg_cost) for s, p in pos.items())

        bench = None
        if benchmark is not None:
            b = benchmark.reindex(idx).ffill()
            bench = b / b.iloc[0] * self.initial_cash
        return BacktestResult(
            nav=nav,
            trades=pd.DataFrame(
                trades,
                columns=["date", "symbol", "side", "shares", "price", "fee", "realized_pnl"],
            ),
            positions=pos,
            benchmark=bench,
        )

    # ------------------------------------------------------------------

    def _exec_days(self, idx: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
        """{执行日: 信号日}。信号日=周期内最后交易日，执行日=其后第一个交易日。"""
        rule = {"W": "W-FRI", "M": "ME"}[self.rebalance]
        sig_days = pd.Series(idx, index=idx).resample(rule).last().dropna()
        out: dict[pd.Timestamp, pd.Timestamp] = {}
        locs = idx.get_indexer(sig_days)
        for sig, loc in zip(sig_days, locs):
            if 0 <= loc < len(idx) - 1:
                out[idx[loc + 1]] = sig
        return out

    def _rebalance(
        self,
        t: pd.Timestamp,
        target: list[str],
        pos: dict[str, Position],
        cash: float,
        open_: pd.DataFrame,
        prev_close: pd.DataFrame,
        last_px: dict[str, float],
        trades: list[dict],
    ) -> float:
        day_open, day_prev = open_.loc[t], prev_close.loc[t]

        def open_ret(sym: str) -> float | None:
            o, pc = day_open.get(sym), day_prev.get(sym)
            if o is None or pc is None or math.isnan(o) or math.isnan(pc) or pc == 0:
                return None  # 停牌或无前收
            return o / pc - 1

        # 先卖出：持有但不在目标内
        for sym in [s for s in pos if s not in target]:
            r = open_ret(sym)
            if r is None:  # 停牌
                continue
            if r <= -self._limit(sym) + self.limit_eps:  # 跌停开盘，卖不出
                continue
            if not can_sell_t1(pos[sym].buy_date, t):  # T+1
                continue
            p = pos.pop(sym)
            px = day_open[sym] * (1 - self.cost.slippage)
            value = p.shares * px
            fee = self.cost.sell_fee(value)
            cash += value - fee
            trades.append(
                dict(date=t, symbol=sym, side="sell", shares=p.shares, price=px, fee=fee,
                     realized_pnl=value - fee - p.shares * p.avg_cost)
            )

        # 再买入：目标内未持有，等权分配
        buys = [s for s in target if s not in pos]
        if not buys:
            return cash
        nav_now = cash + sum(p.shares * last_px.get(s, p.avg_cost) for s, p in pos.items())
        per_name = nav_now / max(len(target), 1)
        for sym in buys:
            r = open_ret(sym)
            if r is None:  # 停牌
                continue
            if r >= self._limit(sym) - self.limit_eps:  # 涨停开盘，买不进
                continue
            px = day_open[sym] * (1 + self.cost.slippage)
            budget = min(per_name, cash)
            shares = int(budget / (px * 100)) * 100  # 整手
            if shares <= 0:
                continue
            value = shares * px
            fee = self.cost.buy_fee(value)
            if value + fee > cash:
                shares -= 100
                if shares <= 0:
                    continue
                value = shares * px
                fee = self.cost.buy_fee(value)
            cash -= value + fee
            pos[sym] = Position(shares=shares, buy_date=t, avg_cost=(value + fee) / shares)
            trades.append(
                dict(date=t, symbol=sym, side="buy", shares=shares, price=px, fee=fee,
                     realized_pnl=0.0)
            )
        return cash
