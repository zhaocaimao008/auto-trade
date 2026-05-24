"""Strategy modules."""
from __future__ import annotations

from autotrader_app.strategies.base import StrategyBase, StrategyContext, StrategyDecision, StrategyMode, StrategySignal
from autotrader_app.strategies.double_ma_strategy import DoubleMA_Strategy
from autotrader_app.strategies.macd_strategy import MACDStrategy

try:
    from autotrader_app.strategies.rsi_strategy import RSIStrategy
except ImportError:
    RSIStrategy = None  # type: ignore[assignment]

try:
    from autotrader_app.strategies.bollinger_strategy import BollingerBandsStrategy
except ImportError:
    BollingerBandsStrategy = None  # type: ignore[assignment]

__all__ = [
    "StrategyBase", "StrategyContext", "StrategyDecision", "StrategyMode", "StrategySignal",
    "DoubleMA_Strategy", "MACDStrategy", "RSIStrategy", "BollingerBandsStrategy",
]
