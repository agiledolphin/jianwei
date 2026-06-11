import pandas as pd

from jianwei.backtest.constraints import TradeCost, can_sell_t1, limit_ratio


def test_limit_ratio_by_board():
    assert limit_ratio("main") == 0.10
    assert limit_ratio("chinext") == 0.20
    assert limit_ratio("star") == 0.20
    assert limit_ratio("bse") == 0.30


def test_limit_ratio_st_only_on_main():
    assert limit_ratio("main", "ST金刚") == 0.05
    assert limit_ratio("main", "*ST大集") == 0.05
    assert limit_ratio("chinext", "ST某创") == 0.20  # 创业板 ST 仍 20%


def test_t1_blocks_same_day_sell():
    d = pd.Timestamp("2024-01-08")
    assert not can_sell_t1(buy_date=d, today=d)
    assert can_sell_t1(buy_date=d, today=d + pd.Timedelta(days=1))


def test_commission_minimum():
    c = TradeCost()
    assert c.buy_fee(1000) == 5.0  # 万2.5 仅 0.25 元，按最低 5 元收
    assert c.buy_fee(1_000_000) == 250.0


def test_stamp_tax_sell_only():
    c = TradeCost()
    v = 100_000
    assert c.buy_fee(v) == 25.0
    assert c.sell_fee(v) == 25.0 + v * 5e-4  # 佣金 + 印花税
