"""回测引擎撮合规则测试：用手工构造的行情与打分矩阵驱动。"""

import pandas as pd
import pytest

from jianwei.backtest.engine import Backtester

DATES = pd.bdate_range("2024-01-01", periods=15)  # 周一 1/1 ~ 周五 1/19
EXEC1 = pd.Timestamp("2024-01-08")  # 信号日 1/5(五) -> 执行日 1/8(一)
EXEC2 = pd.Timestamp("2024-01-15")  # 信号日 1/12(五) -> 执行日 1/15(一)

META = pd.DataFrame(
    {
        "symbol": ["600001", "600002", "300001"],
        "name": ["甲股", "乙股", "创丙"],
        "board": ["main", "main", "chinext"],
    }
)


def flat_panel(price: float = 10.0) -> dict[str, pd.DataFrame]:
    syms = META["symbol"]
    close = pd.DataFrame(price, index=DATES, columns=syms)
    return {"open": close.copy(), "close": close}


def scores_for(targets: dict[pd.Timestamp, list[str]]) -> pd.DataFrame:
    """在信号日给目标股票递减打分，其余日期全 NaN。"""
    sc = pd.DataFrame(float("nan"), index=DATES, columns=META["symbol"])
    for day, syms in targets.items():
        for rank, s in enumerate(syms):
            sc.loc[day, s] = 10.0 - rank
    return sc


SIG1, SIG2 = pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-12")


def test_buy_lot_rounding_and_fees():
    panel = flat_panel()
    bt = Backtester(meta=META, initial_cash=1_000_000)
    res = bt.run(panel, scores_for({SIG1: ["600001"]}), top_n=1)
    buys = res.trades[res.trades["side"] == "buy"]
    assert len(buys) == 1
    row = buys.iloc[0]
    assert row["date"] == EXEC1
    assert row["shares"] % 100 == 0  # 整手
    assert row["fee"] == pytest.approx(max(row["shares"] * row["price"] * 2.5e-4, 5))


def test_limit_up_open_blocks_buy():
    panel = flat_panel()
    panel["open"].loc[EXEC1, "600001"] = 11.05  # 较前收 10 高开 10.5%，主板涨停
    bt = Backtester(meta=META)
    res = bt.run(panel, scores_for({SIG1: ["600001"]}), top_n=1)
    assert res.trades.empty
    assert res.positions == {}
    assert res.nav.iloc[-1] == pytest.approx(bt.initial_cash)


def test_chinext_20pct_limit():
    panel = flat_panel()
    panel["open"].loc[EXEC1, "300001"] = 11.5  # +15%：主板算涨停，创业板不算
    bt = Backtester(meta=META)
    res = bt.run(panel, scores_for({SIG1: ["300001"]}), top_n=1)
    buys = res.trades[res.trades["side"] == "buy"]
    assert len(buys) == 1 and buys.iloc[0]["date"] == EXEC1  # 创业板 20% 幅度内，可以买入


def test_limit_down_open_blocks_sell():
    panel = flat_panel()
    panel["open"].loc[EXEC2, "600001"] = 9.0  # -10% 跌停开盘
    panel["close"].loc[EXEC2:, "600001"] = 9.0
    bt = Backtester(meta=META)
    res = bt.run(panel, scores_for({SIG1: ["600001"], SIG2: ["600002"]}), top_n=1)
    assert "600001" in res.positions  # 跌停卖不出，仓位保留
    sells = res.trades[res.trades["side"] == "sell"]
    assert sells.empty


def test_normal_rotation_sell_has_stamp_tax():
    panel = flat_panel()
    bt = Backtester(meta=META)
    res = bt.run(panel, scores_for({SIG1: ["600001"], SIG2: ["600002"]}), top_n=1)
    sells = res.trades[res.trades["side"] == "sell"]
    assert len(sells) == 1
    row = sells.iloc[0]
    v = row["shares"] * row["price"]
    assert row["fee"] == pytest.approx(max(v * 2.5e-4, 5) + v * 5e-4)
    assert "600002" in res.positions


def test_suspension_blocks_trade_and_nav_uses_last_close():
    panel = flat_panel()
    panel["open"].loc[EXEC1, "600001"] = float("nan")  # 执行日停牌
    panel["close"].loc[EXEC1, "600001"] = float("nan")
    bt = Backtester(meta=META)
    res = bt.run(panel, scores_for({SIG1: ["600001"]}), top_n=1)
    assert res.trades.empty  # 停牌买不进
    assert res.nav.notna().all()  # 净值曲线无缺口
