from __future__ import annotations

import threading
import time
from collections.abc import Callable

import schedule
from loguru import logger


class SchedulerService:
    """定时任务服务。"""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def register_daily_job(self, time_str: str, func: Callable[[], None]) -> None:
        schedule.every().day.at(time_str).do(func)
        logger.info("Registered scheduled job at {}", time_str)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)
