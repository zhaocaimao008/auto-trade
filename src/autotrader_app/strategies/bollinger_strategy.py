"""布林带（Bollinger Bands）均值回归策略。

布林带由中轨（SMA）、上轨（中轨 + K×标准差）和下轨（中轨 - K×标准差）组成。

交易逻辑：
  · 收盘价跌破下轨 → 超卖 → BUY（均值回归）
  · 收盘价突破上轨 → 超买 → SELL（均值回归）
  · 价格在带内运行 → HOLD

参数：
  · window:       SMA 计算周期（默认 20）
  · num_std:      标准差倍数（默认 2.0）
  · use_trend_filter: True 时仅当 MA50 > MA200（多头排列）才允许买入

参考：
  布林带适合震荡行情，在趋势行情中可能产生逆势信号。
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


class BollingerBandsStrategy(StrategyBase):
    """布林带均值回归策略。"""

    name = "bollinger_strategy"

    # ── 默认参数 ─────────────────────────────────────────────
    DEFAULT_WINDOW: int = 20
    DEFAULT_NUM_STD: float = 2.0

    def __init__(
        self,
        window: int = DEFAULT_WINDOW,
        num_std: float = DEFAULT_NUM_STD,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
        use_trend_filter: bool = False,
    ) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)

        if window <= 0:
            raise ValueError("布林带 window 必须大于 0。")
        if num_std <= 0:
            raise ValueError("num_std 必须大于 0。")

        self.window = window
        self.num_std = num_std
        self.use_trend_filter = use_trend_filter
        self._min_bars = window + 5

    # ─────────────────────────────────────────────────────────
    # 公开工具方法
    # ─────────────────────────────────────────────────────────

    def compute_bands(
        self, close: pd.Series
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """计算布林带三轨。

        Args:
            close: 收盘价序列。

        Returns:
            (middle, upper, lower) 三条线：
            · middle = SMA(window)
            · upper = middle + num_std × std
            · lower = middle - num_std × std
        """
        middle = close.rolling(self.window).mean()
        std = close.rolling(self.window).std()
        upper = middle + self.num_std * std
        lower = middle - self.num_std * std
        return middle, upper, lower

    # ─────────────────────────────────────────────────────────
    # 核心：信号生成
    # ─────────────────────────────────────────────────────────

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, context: StrategyContext
    ) -> StrategyDecision:
        if bars.empty or len(bars) < self._min_bars:
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.HOLD,
                reason=f"数据不足（需要 ≥{self._min_bars} 条，当前 {len(bars)} 条）",
                mode=self.mode,
            )

        df = bars.copy()
        close = df["close"].astype(float)
        current_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2])
        context.latest_prices[symbol] = current_price

        # ── 计算布林带 ───────────────────────────────────────
        middle, upper, lower = self.compute_bands(close)
        current_upper = float(upper.iloc[-1])
        current_lower = float(lower.iloc[-1])
        current_middle = float(middle.iloc[-1])
        prev_lower = float(lower.iloc[-2])
        prev_upper = float(upper.iloc[-2])

        # 带宽（用于判断趋势/震荡）
        bandwidth = (current_upper - current_lower) / current_middle if current_middle > 0 else 0
        squeezing = bandwidth < 0.05  # 带宽 < 5% 视为"缩口"（即将变盘）

        # ── 趋势过滤器 ───────────────────────────────────────
        trend_ok = True
        trend_note = ""
        if self.use_trend_filter and len(close) >= 200:
            ma50 = float(close.rolling(50).mean().iloc[-1])
            ma200 = float(close.rolling(200).mean().iloc[-1])
            if ma50 < ma200:
                trend_ok = False
                trend_note = "MA50 < MA200，空头排列，逆势买入风险较高"

        # ── 跌破下轨 → BUY（均值回归）────────────────────────
        if current_price <= current_lower and prev_price > prev_lower:
            if not trend_ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"价格触及下轨({current_lower:.2f})，但 {trend_note}",
                    price=current_price,
                    mode=self.mode,
                )

            risk_ok, risk_msg = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"价格触及下轨({current_lower:.2f})，但风控拦截：{risk_msg}",
                    price=current_price,
                    mode=self.mode,
                )

            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"下轨反弹机会，但资金不足以买入一手",
                    target_ratio=target_ratio,
                    price=current_price,
                    mode=self.mode,
                )

            squeeze_note = "布林带缩口" if squeezing else "布林带张口"
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.BUY,
                reason=f"价格触及下轨({current_lower:.2f}) | {squeeze_note}",
                target_ratio=target_ratio,
                suggested_quantity=quantity,
                price=current_price,
                mode=self.mode,
            )

        # ── 突破上轨 → SELL（均值回归）───────────────────────
        if current_price >= current_upper and prev_price < prev_upper:
            current_qty = context.positions.get(symbol, 0)
            if current_qty <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"价格触及上轨({current_upper:.2f})，当前无持仓",
                    price=current_price,
                    mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.SELL,
                reason=f"价格触及上轨({current_upper:.2f})，均值回归卖出",
                target_ratio=0.0,
                suggested_quantity=current_qty,
                price=current_price,
                mode=self.mode,
            )

        # ── 带内运行 → HOLD ──────────────────────────────────
        # 计算当前价格在布林带中的位置（0=下轨，1=上轨）
        band_range = current_upper - current_lower
        position_in_band = (
            (current_price - current_lower) / band_range if band_range > 0 else 0.5
        )

        if position_in_band < 0.25:
            zone_note = "靠近下轨"
        elif position_in_band > 0.75:
            zone_note = "靠近上轨"
        else:
            zone_note = "中轨附近"

        return StrategyDecision(
            symbol=symbol,
            signal=StrategySignal.HOLD,
            reason=f"布林带{zone_note}({position_in_band:.0%}) | {'缩口' if squeezing else '正常'}",
            price=current_price,
            mode=self.mode,
        )
