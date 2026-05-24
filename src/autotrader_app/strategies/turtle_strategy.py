"""海龟交易策略（Turtle Trading Strategy）。

基于经典的海龟交易法则，使用唐奇安通道突破作为入场信号，
结合 ATR 进行仓位管理和止损。

交易逻辑：
  · 入场：价格突破 N 日高点 → 买入；跌破 N 日低点 → 卖出
  · 加仓：每上涨 0.5 ATR 加仓一次，最多 4 次
  · 止损：跌破 2 ATR 止损
  · 离场：跌破 M 日低点 → 平仓（反向突破离场）

参数：
  · entry_period:   唐奇安通道入场周期（默认 20）
  · exit_period:    唐奇安通道离场周期（默认 10）
  · atr_period:     ATR 计算周期（默认 20）
  · atr_multiplier:  ATR 止损倍数（默认 2.0）
  · max_additions:  最大加仓次数（默认 3）
  · add_unit_atr:   加仓间隔（ATR 倍数，默认 0.5）
"""
from __future__ import annotations

import pandas as pd

from autotrader_app.strategies.base import (
    StrategyBase,
    StrategyContext,
    StrategyDecision,
    StrategyMode,
    StrategySignal,
)


class TurtleTradingStrategy(StrategyBase):
    """海龟交易策略。"""

    name = "turtle_strategy"

    DEFAULT_ENTRY: int = 20
    DEFAULT_EXIT: int = 10
    DEFAULT_ATR_PERIOD: int = 20
    DEFAULT_ATR_MULT: float = 2.0
    DEFAULT_MAX_ADD: int = 3
    DEFAULT_ADD_UNIT: float = 0.5

    def __init__(
        self,
        entry_period: int = DEFAULT_ENTRY,
        exit_period: int = DEFAULT_EXIT,
        atr_period: int = DEFAULT_ATR_PERIOD,
        atr_multiplier: float = DEFAULT_ATR_MULT,
        max_additions: int = DEFAULT_MAX_ADD,
        add_unit_atr: float = DEFAULT_ADD_UNIT,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
    ) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_additions = max_additions
        self.add_unit_atr = add_unit_atr
        self._min_bars = max(entry_period, atr_period, exit_period) + 5

    # ─────────────────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────────────────

    def compute_atr(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """计算 ATR（平均真实波幅）。"""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(self.atr_period).mean()

    def compute_donchian(self, high: pd.Series, low: pd.Series, period: int) -> tuple[pd.Series, pd.Series]:
        """计算唐奇安通道上下轨。"""
        upper = high.rolling(period).max()
        lower = low.rolling(period).min()
        return upper, lower

    # ─────────────────────────────────────────────────────────
    # 信号生成
    # ─────────────────────────────────────────────────────────

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, context: StrategyContext
    ) -> StrategyDecision:
        if bars.empty or len(bars) < self._min_bars:
            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.HOLD,
                reason=f"数据不足（需要 ≥{self._min_bars} 条）", mode=self.mode,
            )

        df = bars.copy()
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        current_price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        context.latest_prices[symbol] = current_price

        # 计算指标
        entry_upper, entry_lower = self.compute_donchian(high, low, self.entry_period)
        exit_upper, exit_lower = self.compute_donchian(high, low, self.exit_period)
        atr = self.compute_atr(high, low, close)

        current_entry_top = float(entry_upper.iloc[-1])
        current_entry_bot = float(entry_lower.iloc[-1])
        current_exit_bot = float(exit_lower.iloc[-2])
        prev_exit_bot = float(exit_lower.iloc[-2])
        current_atr = float(atr.iloc[-1])

        current_qty = context.positions.get(symbol, 0)

        # ── 离场信号：跌破离场通道下轨 ─────────────────────
        if current_qty > 0 and current_price < current_exit_bot and prev_close >= prev_exit_bot:
            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.SELL,
                reason=f"海龟离场：价格({current_price:.2f})跌破{self.exit_period}日低点({current_exit_bot:.2f})",
                suggested_quantity=current_qty, price=current_price, mode=self.mode,
            )

        # ── 入场信号：突破入场通道上轨 ─────────────────────
        if current_price > current_entry_top:
            risk_ok, risk_msg = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol, signal=StrategySignal.HOLD,
                    reason=f"海龟入场信号({current_entry_top:.2f})，但风控拦截：{risk_msg}",
                    price=current_price, mode=self.mode,
                )

            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol, signal=StrategySignal.HOLD,
                    reason="海龟入场信号，但资金不足以买入一手",
                    price=current_price, mode=self.mode,
                )

            atr_note = f"ATR({current_atr:.3f})" if current_atr > 0 else ""
            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.BUY,
                reason=f"海龟入场：突破{self.entry_period}日高点({current_entry_top:.2f}) {atr_note}",
                target_ratio=target_ratio, suggested_quantity=quantity,
                price=current_price, mode=self.mode,
            )

        # ── HOLD ────────────────────────────────────────────
        status = f"持仓{symbol}" if current_qty > 0 else "空仓观望"
        return StrategyDecision(
            symbol=symbol, signal=StrategySignal.HOLD,
            reason=f"海龟{status} | 通道[{current_entry_bot:.2f}~{current_entry_top:.2f}]",
            price=current_price, mode=self.mode,
        )
