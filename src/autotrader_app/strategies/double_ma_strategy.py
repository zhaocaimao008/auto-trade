from __future__ import annotations

import pandas as pd

from autotrader_app.strategies.base import (
    StrategyBase,
    StrategyContext,
    StrategyDecision,
    StrategyMode,
    StrategySignal,
)


class DoubleMA_Strategy(StrategyBase):
    """双均线趋势策略。

    逻辑：
    - 金叉买入
    - 死叉卖出
    - 支持回测和实时模式
    - 内置简单仓位风控
    """

    name = "double_ma_strategy"

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
    ) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)
        if short_window <= 0 or long_window <= 0:
            raise ValueError("均线周期必须大于 0。")
        if short_window >= long_window:
            raise ValueError("短期均线周期必须小于长期均线周期。")

        self.short_window = short_window
        self.long_window = long_window

    def generate_signal(self, symbol: str, bars: pd.DataFrame, context: StrategyContext) -> StrategyDecision:
        if bars.empty or len(bars) < self.long_window + 2:
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.HOLD,
                reason="行情数据不足，无法计算双均线",
                mode=self.mode,
            )

        df = bars.copy()
        df["short_ma"] = df["close"].rolling(self.short_window).mean()
        df["long_ma"] = df["close"].rolling(self.long_window).mean()

        prev_row = df.iloc[-2]
        last_row = df.iloc[-1]
        current_price = float(last_row["close"])

        context.latest_prices[symbol] = current_price

        golden_cross = prev_row["short_ma"] <= prev_row["long_ma"] and last_row["short_ma"] > last_row["long_ma"]
        death_cross = prev_row["short_ma"] >= prev_row["long_ma"] and last_row["short_ma"] < last_row["long_ma"]

        if golden_cross:
            risk_ok, reason = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"出现金叉，但风控拦截：{reason}",
                    price=current_price,
                    mode=self.mode,
                )

            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason="出现金叉，但资金不足以买入一手",
                    target_ratio=target_ratio,
                    price=current_price,
                    mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.BUY,
                reason=f"短期均线({self.short_window})上穿长期均线({self.long_window})，出现金叉",
                target_ratio=target_ratio,
                suggested_quantity=quantity,
                price=current_price,
                mode=self.mode,
            )

        if death_cross:
            current_qty = context.positions.get(symbol, 0)
            if current_qty <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason="出现死叉，但当前无持仓",
                    price=current_price,
                    mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.SELL,
                reason=f"短期均线({self.short_window})下穿长期均线({self.long_window})，出现死叉",
                target_ratio=0.0,
                suggested_quantity=current_qty,
                price=current_price,
                mode=self.mode,
            )

        return StrategyDecision(
            symbol=symbol,
            signal=StrategySignal.HOLD,
            reason="均线未发生交叉，继续观望",
            price=current_price,
            mode=self.mode,
        )
