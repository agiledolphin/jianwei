"""FastAPI 本地服务：桌面端唯一数据入口。

仅绑定 127.0.0.1；设置 JIANWEI_TOKEN 后所有业务路由需携带
`Authorization: Bearer <token>`（/health 除外，供壳探活）。
"""

from __future__ import annotations

import math
import os
import threading
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from jianwei import __version__
from jianwei.data.store import MarketStore


def _check_token(request: Request) -> None:
    token = os.environ.get("JIANWEI_TOKEN")
    if not token or request.url.path == "/health":  # 探活免鉴权
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid token")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from jianwei.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Jianwei Engine",
    version=__version__,
    dependencies=[Depends(_check_token)],
    lifespan=_lifespan,
)
app.add_middleware(  # 仅本机回环可达，来源校验交给 token
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _open_store() -> MarketStore:
    import duckdb

    try:
        return MarketStore()
    except duckdb.IOException as e:  # 另一进程（如 CLI sync）持有写锁
        raise HTTPException(status_code=503, detail=f"行情库被其他进程占用，请稍后重试：{e}") from e


def _clean(obj):
    """递归把 NaN/Inf 置 None，保证 JSON 合法。"""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _records(df: pd.DataFrame) -> list[dict]:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif df[c].dtype == object:
            df[c] = df[c].map(lambda v: str(v) if isinstance(v, pd.Timestamp) else v)
    return _clean(df.to_dict(orient="records"))


def _load_panel(start=None, end=None):
    store = _open_store()
    try:
        daily = store.daily_panel(start=start, end=end)
        if daily.empty:
            raise HTTPException(status_code=409, detail="本地无行情数据，请先同步")
        meta = store.stocks()
        bench = store.index_series("000300", start=start, end=end)
    finally:
        store.close()
    from jianwei.strategy.score import make_panel

    return make_panel(daily), meta, bench


# -- 基础 -------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/stocks")
def stocks() -> list[dict]:
    store = _open_store()
    try:
        return _records(store.stocks())
    finally:
        store.close()


@app.get("/kline/{symbol}")
def kline(symbol: str, start: str | None = None, end: str | None = None) -> dict:
    store = _open_store()
    try:
        df = store.daily_panel(symbols=[symbol], start=start, end=end)
        meta = store.stocks()
    finally:
        store.close()
    if df.empty:
        raise HTTPException(status_code=404, detail=f"无 {symbol} 行情数据")
    name = dict(zip(meta["symbol"], meta["name"])).get(symbol, "")
    cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    return {"symbol": symbol, "name": name, "bars": _records(df[cols])}


# -- 选股 / 回测 -------------------------------------------------------------


@app.get("/picks")
def picks(top: int = 10) -> dict:
    from jianwei.strategy.score import ScoreStrategy

    panel, meta, _ = _load_panel()
    sel = ScoreStrategy(top_n=top).select(panel)
    names = dict(zip(meta["symbol"], meta["name"]))
    sel["name"] = sel["symbol"].map(names)
    return {"date": str(sel["date"].iloc[0].date()), "picks": _records(sel[["symbol", "name", "score"]])}


class BacktestReq(BaseModel):
    start: str | None = None
    end: str | None = None
    top: int = Field(default=10, ge=1, le=50)
    rebalance: str = Field(default="W", pattern="^[WM]$")
    cash: float = Field(default=1_000_000, gt=0)


@app.post("/backtest")
def backtest(req: BacktestReq) -> dict:
    from jianwei.backtest.engine import Backtester
    from jianwei.report.metrics import compute_metrics
    from jianwei.strategy.registry import Registry
    from jianwei.strategy.score import ScoreStrategy

    panel, meta, bench = _load_panel(start=req.start, end=req.end)
    strat = ScoreStrategy(top_n=req.top)
    bt = Backtester(meta=meta, rebalance=req.rebalance, initial_cash=req.cash)
    res = bt.run(panel, strat.scores(panel), top_n=req.top, benchmark=bench)
    m = compute_metrics(res.nav, res.benchmark, res.trades)

    reg = Registry()
    try:
        sid = reg.register_strategy(strat.name, strat.params())
        run_id = reg.record_backtest(sid, str(res.nav.index[0].date()), str(res.nav.index[-1].date()), m)
    finally:
        reg.close()

    nav_df = pd.DataFrame({"date": res.nav.index, "nav": res.nav.values})
    if res.benchmark is not None:
        nav_df["benchmark"] = res.benchmark.values
    return {
        "run_id": run_id,
        "metrics": _clean(m),
        "nav": _records(nav_df),
        "trades": _records(res.trades),
    }


# -- 数据同步（后台线程） -----------------------------------------------------


class _SyncState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.log: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


_sync = _SyncState()


class SyncReq(BaseModel):
    universe: str = Field(default="csi300", pattern="^(csi300|all)$")
    start: str = "20190101"


def _run_sync(universe: str, start: str) -> None:
    from jianwei.data.sync import sync as do_sync

    store = _open_store()
    try:
        _sync.result = do_sync(store, universe=universe, start=start, log=_sync.log.append)
    except Exception as e:  # 后台线程异常只能落状态，由 /sync/status 暴露
        _sync.error = str(e)
    finally:
        store.close()


@app.post("/sync")
def sync_start(req: SyncReq) -> dict:
    with _sync.lock:
        if _sync.running:
            raise HTTPException(status_code=409, detail="同步已在进行中")
        _sync.log, _sync.result, _sync.error = [], None, None
        _sync.thread = threading.Thread(
            target=_run_sync, args=(req.universe, req.start), daemon=True
        )
        _sync.thread.start()
    return {"started": True}


@app.get("/sync/status")
def sync_status() -> dict:
    return {
        "running": _sync.running,
        "log": _sync.log[-20:],
        "result": _clean(_sync.result),
        "error": _sync.error,
    }


@app.get("/quality")
def quality() -> dict:
    from jianwei.data.sync import quality_report

    store = _open_store()
    try:
        rep = quality_report(store)
    finally:
        store.close()
    if rep.empty:
        return {"stocks": 0, "stale": 0, "rows": []}
    stale = int((rep["lag_days"] > 5).sum())
    return {"stocks": int(len(rep)), "stale": stale, "rows": _records(rep)}


# -- 调度 -------------------------------------------------------------------


@app.get("/schedule/status")
def schedule_status() -> dict:
    from jianwei.scheduler import status
    return status()


@app.post("/schedule/trigger")
def schedule_trigger() -> dict:
    from jianwei.scheduler import trigger_now
    trigger_now()
    return {"triggered": True}


@app.get("/schedule/log")
def schedule_log(limit: int = 20) -> list[dict]:
    from jianwei.scheduler_log import SchedulerLog
    sl = SchedulerLog()
    try:
        return sl.recent(limit=limit)
    finally:
        sl.close()


# -- 模拟盘 -----------------------------------------------------------------


@app.get("/sim/nav")
def sim_nav() -> dict:
    from jianwei.sim.broker import SimBroker
    b = SimBroker()
    try:
        return _clean(b.nav())
    finally:
        b.close()


@app.get("/sim/trades")
def sim_trades(limit: int = 50) -> list[dict]:
    from jianwei.sim.broker import SimBroker
    b = SimBroker()
    try:
        return b.trades(limit=limit)
    finally:
        b.close()


@app.post("/sim/execute")
def sim_execute() -> dict:
    """立即用最新选股结果撮合一次（用于手动测试）。"""
    from jianwei.sim.broker import SimBroker
    from jianwei.strategy.score import ScoreStrategy

    panel, meta, _ = _load_panel()
    picks = ScoreStrategy().select(panel)
    picks["name"] = picks["symbol"].map(dict(zip(meta["symbol"], meta["name"])))

    b = SimBroker()
    try:
        return b.execute_picks(picks)
    finally:
        b.close()


@app.post("/sim/reset")
def sim_reset() -> dict:
    from jianwei.sim.broker import SimBroker
    b = SimBroker()
    try:
        b.reset()
        return {"reset": True}
    finally:
        b.close()
