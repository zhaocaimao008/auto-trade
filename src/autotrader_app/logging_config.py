from __future__ import annotations

from pathlib import Path

from loguru import logger

from autotrader_app.config import BASE_DIR


def setup_logger() -> None:
    """初始化日志输出。"""

    logger.remove()
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 控制台输出
    logger.add(lambda msg: print(msg, end=""), level="INFO")

    # 应用日志（轮转）
    logger.add(
        log_dir / "app.log",
        rotation="10 MB",
        retention=10,
        enqueue=False,
        encoding="utf-8",
        level="INFO",
    )

    # 实盘交易日志（独立文件，通过 extra channel=live 过滤）
    logger.add(
        log_dir / "live_trading.log",
        rotation="100 MB",
        retention=30,
        enqueue=False,
        encoding="utf-8",
        level="INFO",
        filter=lambda record: record.get("extra", {}).get("channel") == "live",
    )


def live_logger() -> logger:
    """返回一个绑定到实盘日志频道的 logger 实例。

    使用方式：
        live_logger().info("买入 000001 x100 @ 12.50")
    """
    return logger.bind(channel="live")
