"""Broker modules.

Broker 类型与工厂函数：
  · mock       → MockBroker（模拟交易，默认）
  · easytrader → EasyTraderBroker（通过 easytrader 连接券商客户端）
"""
from __future__ import annotations

from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.broker.mock_broker import MockBroker
from autotrader_app.broker.trade_executor import TradeExecutor

# EasyTrader 为可选导入
try:
    from autotrader_app.broker.easytrader_broker import EasyTraderBroker
except ImportError:
    EasyTraderBroker = None  # type: ignore[assignment,misc]


def create_broker(broker_type: str | None = None, is_live: bool | None = None, initial_cash: float = 100_000.0) -> BrokerBase:
    """Broker 工厂：根据类型返回对应 Broker 实例。

    Args:
        broker_type: "mock" 或 "easytrader"。None 时从 .env 读取 BROKER_TYPE。
        is_live:     是否启用实盘（仅对 easytrader 生效）。
                     None 时根据 broker_type 自动判断（easytrader → True，mock → False）。
        initial_cash: MockBroker 初始资金（仅对 mock 生效）。

    Returns:
        BrokerBase 子类实例。
    """
    from autotrader_app.config import get_settings

    settings = get_settings()
    broker_type = (broker_type or settings.broker_type or "mock").lower()

    if broker_type == "easytrader":
        if EasyTraderBroker is None:
            raise RuntimeError("easytrader 未安装，无法创建实盘 Broker。请执行: pip install easytrader")
        live = is_live if is_live is not None else True
        return EasyTraderBroker(is_live=live)

    return MockBroker(initial_cash=initial_cash)


__all__ = ["BrokerBase", "MockBroker", "TradeExecutor", "EasyTraderBroker", "create_broker"]
