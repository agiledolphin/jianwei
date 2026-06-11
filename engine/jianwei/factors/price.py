"""价量因子库。

输入统一为宽表（index=date, columns=symbol），输出同形状的因子值矩阵；
每个因子在策略层做截面 z-score 后加权合成。register 的方向约定：值越大越好。
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

Factor = Callable[[dict[str, pd.DataFrame]], pd.DataFrame]
REGISTRY: dict[str, Factor] = {}


def register(name: str):
    def deco(fn: Factor) -> Factor:
        REGISTRY[name] = fn
        return fn

    return deco


@register("momentum_20")
def momentum_20(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """20 日动量：近月趋势延续。"""
    return panel["close"].pct_change(20)


@register("momentum_60")
def momentum_60(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """60 日动量：季度趋势。"""
    return panel["close"].pct_change(60)


@register("reversal_5")
def reversal_5(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """5 日反转：短期超跌反弹，取负号使「跌得多」得高分。"""
    return -panel["close"].pct_change(5)


@register("low_volatility_60")
def low_volatility_60(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """60 日低波动：波动越小得分越高。"""
    return -panel["close"].pct_change().rolling(60).std()


@register("liquidity_20")
def liquidity_20(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """20 日平均成交额（对数）：流动性偏好，亦避免选出僵尸股。"""
    import numpy as np

    return np.log(panel["amount"].rolling(20).mean())


def compute_all(panel: dict[str, pd.DataFrame], names: list[str]) -> dict[str, pd.DataFrame]:
    unknown = set(names) - REGISTRY.keys()
    if unknown:
        raise KeyError(f"未注册的因子: {sorted(unknown)}，可用: {sorted(REGISTRY)}")
    return {n: REGISTRY[n](panel) for n in names}
