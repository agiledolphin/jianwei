"""Optuna 因子权重寻优 + champion/challenger 晋升机制。

Walk-forward 切分：
  train_years 年训练期 → val_years 年验证期
  目标函数：训练期内夏普最大化
  晋升条件：验证期年化超额收益 > 当前 champion（或 champion 尚无验证记录）

Champion 存 SQLite strategies 表（params 含 "evolved": true 标记）。
每次进化结果写 backtest_runs，可在「回测」页查看历史。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)
log = logging.getLogger(__name__)


@dataclass
class EvolveConfig:
    n_trials: int = 50
    train_years: float = 3.0   # 训练窗口（年）
    val_years: float = 1.0     # 验证窗口（年）
    top_n_range: tuple[int, int] = (5, 20)
    min_amount_range: tuple[float, float] = (1e7, 1e8)


# 参与寻优的因子（权重均在 [0,1]，Optuna 内部归一化）
FACTOR_NAMES = ["momentum_20", "momentum_60", "reversal_5", "low_volatility_60", "liquidity_20"]


def _run_backtest_period(panel, meta, bench, params: dict, start: str, end: str) -> dict:
    from jianwei.backtest.engine import Backtester
    from jianwei.report.metrics import compute_metrics
    from jianwei.strategy.score import ScoreStrategy

    strat = ScoreStrategy(
        top_n=params["top_n"],
        weights={f: params[f"w_{f}"] for f in FACTOR_NAMES},
        min_amount_20d=params["min_amount_20d"],
    )
    bt = Backtester(meta=meta, rebalance="W")

    sub_panel = {k: v.loc[start:end] for k, v in panel.items()}
    sub_scores = strat.scores(panel).loc[start:end]
    if sub_panel["close"].empty or sub_scores.empty:
        return {}

    sub_bench = bench.loc[start:end] if bench is not None else None
    res = bt.run(sub_panel, sub_scores, top_n=params["top_n"], benchmark=sub_bench)
    if len(res.nav) < 20:
        return {}
    return compute_metrics(res.nav, res.benchmark, res.trades)


def _build_trial_params(trial: optuna.Trial, cfg: EvolveConfig) -> dict:
    raw = {f: trial.suggest_float(f"w_{f}", 0.0, 1.0) for f in FACTOR_NAMES}
    total = sum(raw.values()) or 1.0
    params: dict = {f"w_{f}": v / total for f, v in raw.items()}
    params["top_n"] = trial.suggest_int("top_n", cfg.top_n_range[0], cfg.top_n_range[1])
    params["min_amount_20d"] = trial.suggest_float(
        "min_amount_20d", cfg.min_amount_range[0], cfg.min_amount_range[1], log=True
    )
    return params


def evolve(
    cfg: EvolveConfig | None = None,
    log_fn=log.info,
) -> dict:
    """运行一轮进化，返回 champion 参数及是否晋升。"""
    import pandas as pd

    from jianwei.data.store import MarketStore
    from jianwei.strategy.score import make_panel

    cfg = cfg or EvolveConfig()

    # 加载数据
    store = MarketStore()
    try:
        daily = store.daily_panel()
        meta = store.stocks()
        bench = store.index_series("000300")
    finally:
        store.close()

    if daily.empty:
        return {"error": "无行情数据，请先同步"}

    panel = make_panel(daily)
    all_dates = panel["close"].index
    if len(all_dates) < 2:
        return {"error": "行情数据不足"}

    t_end = all_dates.max()
    val_start = t_end - pd.DateOffset(years=cfg.val_years)
    train_start = val_start - pd.DateOffset(years=cfg.train_years)

    train_s = train_start.strftime("%Y-%m-%d")
    train_e = (val_start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    val_s = val_start.strftime("%Y-%m-%d")
    val_e = t_end.strftime("%Y-%m-%d")

    log_fn(f"训练期: {train_s} ~ {train_e}")
    log_fn(f"验证期: {val_s} ~ {val_e}")

    # Optuna 在训练期内最大化夏普
    def objective(trial: optuna.Trial) -> float:
        params = _build_trial_params(trial, cfg)
        m = _run_backtest_period(panel, meta, bench, params, train_s, train_e)
        return m.get("sharpe", -999.0)

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=cfg.n_trials, show_progress_bar=False)
    best_params = _build_trial_params(
        optuna.trial.FixedTrial(study.best_params), cfg
    )
    log_fn(f"最优试验: sharpe={study.best_value:.3f}")

    # 验证期评估
    val_m = _run_backtest_period(panel, meta, bench, best_params, val_s, val_e)
    val_excess = val_m.get("excess_annual_return", None)
    log_fn(f"验证期超额年化: {val_excess:.2%}" if val_excess is not None else "验证期数据不足")

    # 查询当前 champion
    from jianwei.strategy.registry import Registry
    reg = Registry()
    try:
        champion = _get_champion(reg)
        champion_excess = champion.get("val_excess") if champion else None

        # 晋升判断
        promoted = False
        if val_excess is not None and (
            champion_excess is None or val_excess > champion_excess
        ):
            _save_champion(reg, best_params, val_excess, train_m={"train_sharpe": study.best_value}, val_m=val_m)
            promoted = True
            log_fn("新 champion 已晋升！")
        else:
            log_fn(f"未晋升（challenger={val_excess:.2%} vs champion={champion_excess}）" if val_excess is not None else "未晋升")
    finally:
        reg.close()

    return {
        "promoted": promoted,
        "best_params": best_params,
        "train_sharpe": study.best_value,
        "val_metrics": val_m,
        "train_period": [train_s, train_e],
        "val_period": [val_s, val_e],
        "n_trials": cfg.n_trials,
    }


def _get_champion(reg) -> dict | None:
    import json
    rows = reg.con.execute(
        "SELECT params FROM strategies WHERE name='score_evolved' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not rows:
        return None
    p = json.loads(rows[0])
    return p  # params 里含 val_excess


def _save_champion(reg, params: dict, val_excess: float, train_m: dict, val_m: dict) -> int:
    import json
    from datetime import datetime, UTC

    full_params = {
        **params,
        "evolved": True,
        "val_excess": val_excess,
        "train_metrics": train_m,
        "val_metrics": val_m,
    }
    pj = json.dumps(full_params, sort_keys=True, ensure_ascii=False)
    cur = reg.con.execute(
        "INSERT OR IGNORE INTO strategies (name, params, created_at) VALUES (?, ?, ?)",
        ("score_evolved", pj, datetime.now(UTC).isoformat()),
    )
    reg.con.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = reg.con.execute(
        "SELECT id FROM strategies WHERE name='score_evolved' AND params=?", (pj,)
    ).fetchone()
    return row[0]
