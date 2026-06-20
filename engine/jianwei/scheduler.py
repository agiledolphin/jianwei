"""APScheduler 每日调度：收盘后同步行情、重新选股、触发模拟盘撮合。

调度时间（Asia/Shanghai）：
  15:35  daily_job —— 增量同步 + 打分选股 + 模拟盘撮合
  09:15  （预留）开盘前检查停牌/涨跌停状态

通过 start_scheduler(app) 挂载到 FastAPI lifespan，随服务启停。
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# -- 核心任务 ----------------------------------------------------------------


def _daily_sync_and_pick() -> None:
    """增量同步 + 重新打分选股，结果写入 scheduler_runs 日志。"""
    from jianwei.data.store import MarketStore
    from jianwei.data.sync import sync
    from jianwei.scheduler_log import SchedulerLog
    from jianwei.strategy.score import ScoreStrategy, make_panel

    log.info("daily_job 开始")
    sl = SchedulerLog()
    try:
        # 1. 增量同步
        store = MarketStore()
        try:
            result = sync(store, universe="csi300", log=log.info)
        finally:
            store.close()
        log.info("同步完成: %s", result)

        # 2. 重新打分
        store = MarketStore()
        try:
            daily = store.daily_panel()
            meta = store.stocks()
        finally:
            store.close()

        panel = make_panel(daily)
        strat = ScoreStrategy()
        picks = strat.select(panel)
        picks["name"] = picks["symbol"].map(dict(zip(meta["symbol"], meta["name"])))
        picks_list = picks[["symbol", "name", "score"]].to_dict(orient="records")
        log.info("选股完成: %d 只", len(picks_list))

        # 3. 触发模拟盘撮合（若已启用）
        try:
            from jianwei.sim.broker import SimBroker
            broker = SimBroker()
            try:
                broker.execute_picks(picks)
            finally:
                broker.close()
            log.info("模拟盘撮合完成")
        except Exception as e:
            log.warning("模拟盘撮合跳过: %s", e)

        sl.record("daily_job", "ok", {"sync": result, "picks": picks_list})
        log.info("daily_job 完成")

    except Exception as e:
        log.exception("daily_job 失败")
        sl.record("daily_job", "error", {"error": str(e)})
    finally:
        sl.close()


# -- 启停接口 ----------------------------------------------------------------


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _daily_sync_and_pick,
        CronTrigger(hour=15, minute=35, timezone="Asia/Shanghai"),
        id="daily_job",
        replace_existing=True,
        misfire_grace_time=600,  # 服务重启后 10 分钟内仍触发
    )
    _scheduler.start()
    log.info("调度器已启动，下次触发: %s", next_run_time())
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("调度器已停止")


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_job")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def trigger_now() -> None:
    """立即触发一次 daily_job（用于手动测试或 API 调用）。"""
    if _scheduler is None or not _scheduler.running:
        start_scheduler()
    _scheduler.get_job("daily_job").modify(next_run_time=datetime.now(_scheduler.timezone))


def status() -> dict:
    running = _scheduler is not None and _scheduler.running
    return {
        "running": running,
        "next_run": next_run_time(),
        "timezone": "Asia/Shanghai",
        "jobs": [j.id for j in (_scheduler.get_jobs() if running else [])],
    }
