"""A 股交易约束：涨跌停幅度、交易费用、T+1。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_BOARD_LIMIT = {"main": 0.10, "chinext": 0.20, "star": 0.20, "bse": 0.30}


def limit_ratio(board: str, name: str = "") -> float:
    """涨跌停幅度。ST/*ST 主板为 5%；创业板/科创板 ST 仍为 20%。"""
    if "ST" in name.upper() and board == "main":
        return 0.05
    return _BOARD_LIMIT.get(board, 0.10)


def can_sell_t1(buy_date, today) -> bool:
    """T+1：当日买入的仓位当日不可卖出。"""
    return pd.Timestamp(today) > pd.Timestamp(buy_date)


@dataclass(frozen=True)
class TradeCost:
    commission_rate: float = 2.5e-4  # 佣金 万2.5
    commission_min: float = 5.0      # 单笔最低 5 元
    stamp_sell: float = 5e-4         # 卖出印花税 0.05%
    slippage: float = 1e-4           # 滑点（按价格比例，双向）

    def commission(self, value: float) -> float:
        return max(value * self.commission_rate, self.commission_min)

    def buy_fee(self, value: float) -> float:
        return self.commission(value)

    def sell_fee(self, value: float) -> float:
        return self.commission(value) + value * self.stamp_sell
