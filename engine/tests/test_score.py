import numpy as np
import pandas as pd

from jianwei.strategy.score import ScoreStrategy

DATES = pd.bdate_range("2024-01-01", periods=30)


def build_panel() -> dict[str, pd.DataFrame]:
    # A 日涨 1%，B 横盘，C 日涨 2% 但成交额极小（应被流动性过滤）
    n = len(DATES)
    close = pd.DataFrame(
        {
            "600001": 10 * 1.01 ** np.arange(n),
            "600002": np.full(n, 10.0),
            "300001": 10 * 1.02 ** np.arange(n),
        },
        index=DATES,
    )
    amount = pd.DataFrame(
        {"600001": 1e8, "600002": 1e8, "300001": 1e4}, index=DATES
    )
    return {"close": close, "amount": amount}


def test_momentum_ranking_with_liquidity_filter():
    strat = ScoreStrategy(top_n=1, weights={"momentum_20": 1.0}, min_amount_20d=3e7)
    sel = strat.select(build_panel())
    # C 动量最高但流动性不达标，应选出 A
    assert sel["symbol"].tolist() == ["600001"]


def test_no_filter_picks_highest_momentum():
    strat = ScoreStrategy(top_n=1, weights={"momentum_20": 1.0}, min_amount_20d=0)
    sel = strat.select(build_panel())
    assert sel["symbol"].tolist() == ["300001"]
