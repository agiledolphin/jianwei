"""收益评估指标与文本报告。"""

from __future__ import annotations

import math

import pandas as pd

TRADING_DAYS = 252


def max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1).min())


def annual_return(nav: pd.Series) -> float:
    total = nav.iloc[-1] / nav.iloc[0]
    years = max(len(nav) / TRADING_DAYS, 1e-9)
    return float(total ** (1 / years) - 1)


def sharpe(nav: pd.Series, rf: float = 0.0) -> float:
    ret = nav.pct_change().dropna() - rf / TRADING_DAYS
    sd = ret.std()
    return float(ret.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def compute_metrics(
    nav: pd.Series, benchmark: pd.Series | None = None, trades: pd.DataFrame | None = None
) -> dict:
    mdd = max_drawdown(nav)
    ann = annual_return(nav)
    m = {
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "annual_return": ann,
        "max_drawdown": mdd,
        "sharpe": sharpe(nav),
        "calmar": ann / abs(mdd) if mdd < 0 else 0.0,
        "days": int(len(nav)),
    }
    if benchmark is not None and len(benchmark.dropna()) > 1:
        b = benchmark.dropna()
        m["benchmark_annual_return"] = annual_return(b)
        m["excess_annual_return"] = ann - m["benchmark_annual_return"]
    if trades is not None and not trades.empty:
        sells = trades[trades["side"] == "sell"]
        if not sells.empty:
            m["closed_trades"] = int(len(sells))
            m["win_rate"] = float((sells["realized_pnl"] > 0).mean())
        traded = float((trades["shares"] * trades["price"]).sum())
        years = max(len(nav) / TRADING_DAYS, 1e-9)
        m["annual_turnover"] = traded / float(nav.mean()) / years / 2  # 买卖各计一半
        m["total_fees"] = float(trades["fee"].sum())
    return m


_LABELS = [
    ("total_return", "累计收益", "{:+.2%}"),
    ("annual_return", "年化收益", "{:+.2%}"),
    ("benchmark_annual_return", "基准年化(沪深300)", "{:+.2%}"),
    ("excess_annual_return", "超额年化", "{:+.2%}"),
    ("max_drawdown", "最大回撤", "{:.2%}"),
    ("sharpe", "夏普比率", "{:.2f}"),
    ("calmar", "卡玛比率", "{:.2f}"),
    ("win_rate", "胜率(平仓)", "{:.1%}"),
    ("closed_trades", "平仓笔数", "{:d}"),
    ("annual_turnover", "年换手(单边)", "{:.1f}x"),
    ("total_fees", "总费用(元)", "{:,.0f}"),
    ("days", "交易日数", "{:d}"),
]


def render_text(m: dict, title: str = "回测报告") -> str:
    lines = [f"━━━ {title} ━━━"]
    for key, label, fmt in _LABELS:
        if key in m:
            lines.append(f"{label:<12}{fmt.format(m[key])}")
    return "\n".join(lines)
