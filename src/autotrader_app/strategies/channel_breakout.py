"""通道突破策略（Channel Breakout Strategy）。

价格突破近期高点时买入，跌破近期低点时卖出。
基于唐奇安通道简化版，无加仓逻辑，适合与海龟策略互补。

交易逻辑：
  · BUY：  收盘价突破 N 日最高价
  · SELL： 收盘价跌破 N 日最低价（有持仓时）
  · 离场：反向突破（持有时跌破低点卖出）
  · 过滤器：价格高于 MA200 才允许买入（多头趋势过滤）

参数：
  · entry_period: 入场突破周期（默认 20）
  · exit_period:  离场突破周期（默认 10，比入场短以快速止盈止损）
  · use_ma_filter: True 时价格 > MA200 才允许买入
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


class ChannelBreakoutStrategy(StrategyBase):
    """通道突破策略。"""

    name = "channel_breakout"

    DEFAULT_ENTRY: int = 20
    DEFAULT_EXIT: int = 10

    def __init__(
        self,
        entry_period: int = DEFAULT_ENTRY,
        exit_period: int = DEFAULT_EXIT,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
        use_ma_filter: bool = False,
    ) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.use_ma_filter = use_ma_filter
        self._min_bars = max(entry_period, exit_period) + 5

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

        entry_high = float(high.rolling(self.entry_period).max().iloc[-1])
        entry_low = float(low.rolling(self.entry_period).min().iloc[-1])
        prev_entry_low = float(low.rolling(self.entry_period).min().iloc[-2])
        exit_low = float(low.rolling(self.exit_period).min().iloc[-1])
        prev_exit_low = float(low.rolling(self.exit_period).min().iloc[-2])

        current_qty = context.positions.get(symbol, 0)

        # ── MA 趋势过滤器 ────────────────────────────────────
        ma_ok = True
        ma_note = ""
        if self.use_ma_filter and len(close) >= 200:
            ma200 = float(close.rolling(200).mean().iloc[-1])
            if current_price < ma200:
                ma_ok = False
                ma_note = f"价格({current_price:.2f}) < MA200({ma200:.2f})"

        # ── 离场：持有且跌破离场周期低点 ──────────────────
        if current_qty > 0 and current_price < exit_low and prev_close >= prev_exit_low:
            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.SELL,
                reason=f"通道离场：{symbol} 跌破{self.exit_period}日低点({exit_low:.2f})",
                suggested_quantity=current_qty, price=current_price, mode=self.mode,
            )

        # ── 入场：突破入场周期高点 ──────────────────────────
        if current_price > entry_high:
            if not ma_ok:
                return StrategyDecision(
                    symbol=symbol, signal=StrategySignal.HOLD,
                    reason=f"通道突破({entry_high:.2f})，但 {ma_note}",
                    price=current_price, mode=self.mode,
                )

            risk_ok, risk_msg = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol, signal=StrategySignal.HOLD,
                    reason=f"通道突破({entry_high:.2f})，风控拦截：{risk_msg}",
                    price=current_price, mode=self.mode,
                )

            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol, signal=StrategySignal.HOLD,
                    reason="通道突破，但资金不足以买入一手",
                    price=current_price, mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.BUY,
                reason=f"通道突破：突破{self.entry_period}日高点({entry_high:.2f})",
                target_ratio=target_ratio, suggested_quantity=quantity,
                price=current_price, mode=self.mode,
            )

        # ── HOLD ────────────────────────────────────────────
        band = f"[{entry_low:.2f}~{entry_high:.2f}]"
        if current_qty > 0:
            return StrategyDecision(
                symbol=symbol, signal=StrategySignal.HOLD,
                reason=f"通道持仓 {symbol} {band}", price=current_price, mode=self.mode,
            )
        return StrategyDecision(
            symbol=symbol, signal=StrategySignal.HOLD,
            reason=f"通道观望 {band}", price=current_price, mode=self.mode,
        )
