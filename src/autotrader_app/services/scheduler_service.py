from __future__ import annotations

import threading
import time
from collections.abc import Callable

import schedule
from loguru import logger


class SchedulerService:
    """定时任务服务，支持 A 股交易时段自动启停引擎。

    自动调度（启用后每天执行）：
    · 09:20 → 启动引擎（集合竞价前）
    · 11:30 → 暂停引擎（午间休市）
    · 12:55 → 恢复引擎（下午开盘前）
    · 15:05 → 停止引擎（收盘后）

    非交易日通过交易日判断自动跳过（引擎 tick 中已实现）。
    """

    PRE_OPEN = "09:20"
    MORNING_CLOSE = "11:30"
    AFTERNOON_OPEN = "12:55"
    MARKET_CLOSE = "15:05"

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._engine_start_cb: Callable[[], None] | None = None
        self._engine_pause_cb: Callable[[], None] | None = None
        self._engine_resume_cb: Callable[[], None] | None = None
        self._engine_stop_cb: Callable[[], None] | None = None

    # ── 引擎控制挂钩 ──────────────────────────────────────

    def set_engine_callbacks(
        self,
        start: Callable[[], None],
        pause: Callable[[], None],
        resume: Callable[[], None],
        stop: Callable[[], None],
    ) -> None:
        """注册交易引擎控制回调。

        Args:
            start:  启动引擎（09:20 自动调用）。
            pause:  暂停引擎（11:30 自动调用）。
            resume: 恢复引擎（12:55 自动调用）。
            stop:   停止引擎（15:05 自动调用）。
        """
        self._engine_start_cb = start
        self._engine_pause_cb = pause
        self._engine_resume_cb = resume
        self._engine_stop_cb = stop

    def _register_trading_hours(self) -> None:
        """注册 A 股交易时段自动调度任务。"""
        if self._engine_start_cb:
            schedule.every().day.at(self.PRE_OPEN).do(self._engine_start_cb)
            logger.info("Scheduled engine start at {}", self.PRE_OPEN)
        if self._engine_pause_cb:
            schedule.every().day.at(self.MORNING_CLOSE).do(self._engine_pause_cb)
            logger.info("Scheduled engine pause at {}", self.MORNING_CLOSE)
        if self._engine_resume_cb:
            schedule.every().day.at(self.AFTERNOON_OPEN).do(self._engine_resume_cb)
            logger.info("Scheduled engine resume at {}", self.AFTERNOON_OPEN)
        if self._engine_stop_cb:
            schedule.every().day.at(self.MARKET_CLOSE).do(self._engine_stop_cb)
            logger.info("Scheduled engine stop at {}", self.MARKET_CLOSE)

    # ── 自定义任务 ────────────────────────────────────────

    def register_daily_job(self, time_str: str, func: Callable[[], None]) -> None:
        schedule.every().day.at(time_str).do(func)
        logger.info("Registered job at {}", time_str)

    def register_interval_job(self, minutes: int, func: Callable[[], None]) -> None:
        schedule.every(minutes).minutes.do(func)
        logger.info("Registered interval job every {} min", minutes)

    # ── 启动 / 停止 ───────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        schedule.clear()
        self._register_trading_hours()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        schedule.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("Scheduler stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)
