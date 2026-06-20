"""命令行入口：jianwei sync / select / backtest / quality"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jianwei", description="见微 Jianwei — A 股自动选股引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="同步行情数据到本地 DuckDB")
    sp.add_argument("--universe", choices=["csi300", "all"], default="csi300")
    sp.add_argument("--start", default="20190101")
    sp.add_argument("--limit", type=int, default=None, help="仅同步前 N 只（调试）")

    sel = sub.add_parser("select", help="按最新数据选股")
    sel.add_argument("--top", type=int, default=10)

    bt = sub.add_parser("backtest", help="回测内置多因子策略")
    bt.add_argument("--start", default=None, help="如 2021-01-01")
    bt.add_argument("--end", default=None)
    bt.add_argument("--top", type=int, default=10)
    bt.add_argument("--rebalance", choices=["W", "M"], default="W")
    bt.add_argument("--cash", type=float, default=1_000_000)

    sub.add_parser("quality", help="数据质量报告")

    ev = sub.add_parser("evolve", help="Optuna 因子权重寻优 + champion/challenger 晋升")
    ev.add_argument("--trials", type=int, default=50)
    ev.add_argument("--train-years", type=float, default=3.0)
    ev.add_argument("--val-years", type=float, default=1.0)

    sv = sub.add_parser("serve", help="启动本地 HTTP 服务（供桌面端调用）")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--token", default=None, help="Bearer 鉴权 token（缺省不鉴权）")

    args = p.parse_args(argv)
    return {
        "sync": _sync, "select": _select, "backtest": _backtest,
        "quality": _quality, "evolve": _evolve_cmd, "serve": _serve,
    }[args.cmd](args)


def _sync(args) -> int:
    from jianwei.data.store import MarketStore
    from jianwei.data.sync import sync

    store = MarketStore()
    try:
        res = sync(store, universe=args.universe, start=args.start, limit=args.limit)
    finally:
        store.close()
    print(f"完成：{res['stocks']} 只 / 新增 {res['rows']} 行 / 失败 {len(res['failed'])} 只")
    if res["failed"]:
        print("失败列表:", ", ".join(res["failed"]))
    return 0


def _load_panel(start=None, end=None):
    from jianwei.data.store import MarketStore
    from jianwei.strategy.score import make_panel

    store = MarketStore()
    try:
        daily = store.daily_panel(start=start, end=end)
        if daily.empty:
            raise SystemExit("本地无行情数据，请先执行 jianwei sync")
        meta = store.stocks()
        bench = store.index_series("000300", start=start, end=end)
    finally:
        store.close()
    return make_panel(daily), meta, bench


def _select(args) -> int:
    from jianwei.strategy.score import ScoreStrategy

    panel, meta, _ = _load_panel()
    strat = ScoreStrategy(top_n=args.top)
    sel = strat.select(panel)
    names = dict(zip(meta["symbol"], meta["name"]))
    print(f"=== {sel['date'].iloc[0].date()} Top {args.top} ===")
    for r in sel.itertuples(index=False):
        print(f"{r.symbol}  {names.get(r.symbol, '?'):<10} score={r.score:+.3f}")
    return 0


def _backtest(args) -> int:
    from jianwei.backtest.engine import Backtester
    from jianwei.report.metrics import compute_metrics, render_text
    from jianwei.strategy.registry import Registry
    from jianwei.strategy.score import ScoreStrategy

    panel, meta, bench = _load_panel(start=args.start, end=args.end)
    strat = ScoreStrategy(top_n=args.top)
    bt = Backtester(meta=meta, rebalance=args.rebalance, initial_cash=args.cash)
    res = bt.run(panel, strat.scores(panel), top_n=args.top, benchmark=bench)
    m = compute_metrics(res.nav, res.benchmark, res.trades)
    print(render_text(m, title=f"回测 {res.nav.index[0].date()} ~ {res.nav.index[-1].date()}"))

    reg = Registry()
    try:
        sid = reg.register_strategy(strat.name, strat.params())
        run_id = reg.record_backtest(sid, str(res.nav.index[0].date()), str(res.nav.index[-1].date()), m)
    finally:
        reg.close()
    print(f"\n已记录：strategy_id={sid} run_id={run_id}")
    return 0


def _evolve_cmd(args) -> int:
    from jianwei.evolve.optimizer import EvolveConfig, evolve

    cfg = EvolveConfig(
        n_trials=args.trials,
        train_years=args.train_years,
        val_years=args.val_years,
    )
    print(f"开始进化：{cfg.n_trials} trials，训练 {cfg.train_years}y / 验证 {cfg.val_years}y")
    result = evolve(cfg, log_fn=print)
    if "error" in result:
        print("错误:", result["error"])
        return 1
    print(f"\n{'已晋升新 champion ✓' if result['promoted'] else '未晋升（challenger 未超过 champion）'}")
    print(f"训练期夏普: {result['train_sharpe']:.3f}")
    vm = result.get("val_metrics", {})
    if vm:
        print(f"验证期年化: {vm.get('annual_return', 0):.2%}  超额: {vm.get('excess_annual_return', 0):.2%}  回撤: {vm.get('max_drawdown', 0):.2%}")
    print("\n最优参数:")
    for k, v in result["best_params"].items():
        if k.startswith("w_"):
            print(f"  {k[2:]:<20} {v:.3f}")
    print(f"  top_n                {result['best_params']['top_n']}")
    return 0


def _serve(args) -> int:
    import os
    import threading
    import time

    import uvicorn

    if args.token:
        os.environ["JIANWEI_TOKEN"] = args.token

    def watch_parent(ppid: int = os.getppid()) -> None:
        # 壳经 uv 间接拉起本进程，壳退出时只能杀到 uv；
        # 父进程消失（被 init 收养）即自退，避免引擎成为孤儿进程。
        while os.getppid() == ppid:
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    uvicorn.run("jianwei.api.app:app", host=args.host, port=args.port, log_level="warning")
    return 0


def _quality(args) -> int:
    from jianwei.data.store import MarketStore
    from jianwei.data.sync import quality_report

    store = MarketStore()
    try:
        rep = quality_report(store)
    finally:
        store.close()
    if rep.empty:
        print("本地无数据")
        return 0
    stale = rep[rep["lag_days"] > 5]
    print(rep.to_string(index=False, max_rows=20))
    print(f"\n共 {len(rep)} 只；滞后超过 5 天的 {len(stale)} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
