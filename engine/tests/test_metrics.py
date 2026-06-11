import pandas as pd
import pytest

from jianwei.report.metrics import annual_return, compute_metrics, max_drawdown


def test_max_drawdown():
    nav = pd.Series([100.0, 110.0, 99.0, 108.0])
    assert max_drawdown(nav) == pytest.approx(99 / 110 - 1)


def test_annual_return_one_year():
    # 252 个交易日恰好一年：年化 = 总收益
    nav = pd.Series(pd.RangeIndex(252).map(lambda i: 100 * (1.2 ** (i / 251))))
    assert annual_return(nav) == pytest.approx(0.2, rel=1e-6)


def test_compute_metrics_with_trades():
    nav = pd.Series([100.0, 101.0, 102.0, 103.0])
    trades = pd.DataFrame(
        {
            "side": ["buy", "sell", "buy", "sell"],
            "shares": [100, 100, 100, 100],
            "price": [10.0, 11.0, 11.0, 10.5],
            "fee": [5.0, 5.55, 5.0, 5.53],
            "realized_pnl": [0.0, 89.45, 0.0, -60.53],
        }
    )
    m = compute_metrics(nav, trades=trades)
    assert m["closed_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["total_fees"] == pytest.approx(21.08)
