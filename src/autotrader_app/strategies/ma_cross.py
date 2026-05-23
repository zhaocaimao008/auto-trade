from __future__ import annotations

import pandas as pd

from autotrader_app.strategies.base import StrategyBase, StrategyContext, StrategyDecision, StrategySignal


class MovingAverageCrossStrategy(StrategyBase):
    """兼容旧接口的双均线策略。"""

    name = "ma_cross"

    def __init__(self, fast: int = 5, slow: int = 20, symbol_list: list[str] | None = None, position_ratio: float = 0.2) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio)
        self.fast = fast
        self.slow = slow

    def generate_signal(self, symbol: str, bars: pd.DataFrame, context: StrategyContext) -> StrategyDecision:
        if bars.empty or len(bars) < self.slow + 2:
            return StrategyDecision(symbol=symbol, signal=StrategySignal.HOLD, reason="行情数据不足")

        df = bars.copy()
        df["fast_ma"] = df["close"].rolling(self.fast).mean()
        df["slow_ma"] = df["close"].rolling(self.slow).mean()
        prev = df.iloc[-2]
        last = df.iloc[-1]
        price = float(last["close"])

        if prev["fast_ma"] <= prev["slow_ma"] and last["fast_ma"] > last["slow_ma"]:
            qty = self.calculate_order_quantity(symbol, price, context, min(self.position_ratio, self.max_single_position_ratio))
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.BUY,
                reason="均线金叉",
                target_ratio=min(self.position_ratio, self.max_single_position_ratio),
                suggested_quantity=qty,
                price=price,
            )
        if prev["fast_ma"] >= prev["slow_ma"] and last["fast_ma"] < last["slow_ma"]:
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.SELL,
                reason="均线死叉",
                suggested_quantity=context.positions.get(symbol, 0),
                price=price,
            )
        return StrategyDecision(symbol=symbol, signal=StrategySignal.HOLD, reason="无交易信号", price=price)
