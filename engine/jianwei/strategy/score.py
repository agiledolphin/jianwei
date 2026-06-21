"""多因子打分策略：截面 z-score 加权合成，取 Top N。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from jianwei.factors.price import compute_all

DEFAULT_WEIGHTS = {
    "momentum_20": 0.3,
    "momentum_60": 0.3,
    "reversal_5": 0.1,
    "low_volatility_60": 0.2,
    "liquidity_20": 0.1,
}


def make_panel(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """长表 -> {field: 宽表(index=date, columns=symbol)}"""
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    out = {}
    for f in ("open", "high", "low", "close", "amount", "volume"):
        out[f] = daily.pivot_table(index="date", columns="symbol", values=f)
    return out


def zscore_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """逐日截面标准化，并截断到 ±3 抑制极端值。"""
    df = df.replace([float("inf"), float("-inf")], pd.NA)  # inf/-inf 先置 NaN，避免均值/减法 warning
    mu = df.mean(axis=1)
    sd = df.std(axis=1).replace(0, pd.NA)
    return df.sub(mu, axis=0).div(sd, axis=0).clip(-3, 3)


@dataclass
class ScoreStrategy:
    name: str = "score_v1"
    top_n: int = 10
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    min_amount_20d: float = 3e7  # 20 日均成交额下限（元），流动性硬过滤

    def params(self) -> dict:
        return {"top_n": self.top_n, "weights": self.weights, "min_amount_20d": self.min_amount_20d}

    def scores(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """全历史打分矩阵（index=date, columns=symbol）。"""
        factors = compute_all(panel, list(self.weights))
        total = None
        for name, w in self.weights.items():
            z = zscore_cross_section(factors[name]) * w
            total = z if total is None else total.add(z, fill_value=0)
        # 流动性硬过滤：不达标置 NA，不参与排名
        liquid = panel["amount"].rolling(20).mean() >= self.min_amount_20d
        return total.where(liquid)

    def select(self, panel: dict[str, pd.DataFrame], on: pd.Timestamp | None = None) -> pd.DataFrame:
        """某日（缺省最新交易日）Top N 选股结果。"""
        sc = self.scores(panel)
        on = on or sc.index.max()
        row = sc.loc[:on].iloc[-1].dropna().sort_values(ascending=False)
        top = row.head(self.top_n)
        return pd.DataFrame({"symbol": top.index, "score": top.values, "date": on})
