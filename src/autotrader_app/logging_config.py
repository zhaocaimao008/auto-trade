from __future__ import annotations

from pathlib import Path

from loguru import logger

from autotrader_app.config import BASE_DIR


def setup_logger() -> None:
    """初始化日志输出。"""

    logger.remove()
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(lambda msg: print(msg, end=""), level="INFO")
    logger.add(
        log_dir / "app.log",
        rotation="10 MB",
        retention=10,
        enqueue=False,
        encoding="utf-8",
        level="INFO",
    )
