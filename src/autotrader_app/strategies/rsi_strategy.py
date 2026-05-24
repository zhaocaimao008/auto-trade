"""RSI 相对强弱指标策略。

RSI（Relative Strength Index）通过比较一定周期内的平均涨幅和平均跌幅，
衡量股票的"超买"和"超卖"状态。

交易逻辑：
  · RSI < oversold_threshold（默认 30）→ 超卖 → BUY 信号
  · RSI > overbought_threshold（默认 70）→ 超买 → SELL 信号
  · 中间区域 → HOLD

参数：
  · period:          RSI 计算周期（默认 14）
  · oversold:        超卖阈值（默认 30），低于此值产生买入信号
  · overbought:      超买阈值（默认 70），高于此值产生卖出信号
  · use_ma_filter:   True 时仅当价格高于 MA60 才允许买入（趋势过滤）

参考计算方法（Wilder 平滑法）：
  RSI = 100 - (100 / (1 + RS))
  RS = avg_gain / avg_loss
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


class RSIStrategy(StrategyBase):
    """RSI 相对强弱指标策略。"""

    name = "rsi_strategy"

    # ── 默认参数 ─────────────────────────────────────────────
    DEFAULT_PERIOD: int = 14
    DEFAULT_OVERSOLD: float = 30.0
    DEFAULT_OVERBOUGHT: float = 70.0
    DEFAULT_MA60_WINDOW: int = 60

    def __init__(
        self,
        period: int = DEFAULT_PERIOD,
        oversold: float = DEFAULT_OVERSOLD,
        overbought: float = DEFAULT_OVERBOUGHT,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
        use_ma_filter: bool = False,
        ma_window: int = DEFAULT_MA60_WINDOW,
    ) -> None:
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)

        if period <= 0:
            raise ValueError("RSI period 必须大于 0。")
        if oversold <= 0 or overbought <= 0:
            raise ValueError("超买/超卖阈值必须大于 0。")
        if oversold >= overbought:
            raise ValueError(f"oversold({oversold}) 必须小于 overbought({overbought})。")

        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.use_ma_filter = use_ma_filter
        self.ma_window = ma_window
        self._min_bars = period + 5

    # ─────────────────────────────────────────────────────────
    # 公开工具方法
    # ─────────────────────────────────────────────────────────

    def compute_rsi(self, close: pd.Series) -> pd.Series:
        """计算 RSI 值序列（Wilder 平滑法）。

        Args:
            close: 收盘价序列。

        Returns:
            RSI 值序列（0-100 之间）。
        """
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1.0 / self.period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / self.period, adjust=False).mean()

        # 避免除零
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

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
        context.latest_prices[symbol] = current_price

        # ── 计算 RSI ─────────────────────────────────────────
        rsi_series = self.compute_rsi(close)
        current_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2])

        # ── MA 过滤器 ────────────────────────────────────────
        ma_pass = True
        ma_note = ""
        if self.use_ma_filter:
            if len(close) >= self.ma_window:
                ma_val = float(close.rolling(self.ma_window).mean().iloc[-1])
                if current_price < ma_val:
                    ma_pass = False
                    ma_note = (
                        f"价格({current_price:.2f}) < MA{self.ma_window}"
                        f"({ma_val:.2f})，不符合上行趋势"
                    )
            else:
                ma_note = f"数据不足 {self.ma_window} 条，跳过 MA 过滤"

        # ── 超卖 → BUY ───────────────────────────────────────
        if current_rsi <= self.oversold:
            if not ma_pass:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"RSI({current_rsi:.1f}) 超卖，但 {ma_note}",
                    price=current_price,
                    mode=self.mode,
                )

            risk_ok, risk_msg = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"RSI({current_rsi:.1f}) 超卖，但风控拦截：{risk_msg}",
                    price=current_price,
                    mode=self.mode,
                )

            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"RSI({current_rsi:.1f}) 超卖，但资金不足以买入一手",
                    target_ratio=target_ratio,
                    price=current_price,
                    mode=self.mode,
                )

            filter_note = " | MA 过滤通过" if self.use_ma_filter and ma_pass else ""
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.BUY,
                reason=f"RSI({current_rsi:.1f}) 超卖 (≤{self.oversold}){filter_note}",
                target_ratio=target_ratio,
                suggested_quantity=quantity,
                price=current_price,
                mode=self.mode,
            )

        # ── 超买 → SELL ──────────────────────────────────────
        if current_rsi >= self.overbought:
            current_qty = context.positions.get(symbol, 0)
            if current_qty <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"RSI({current_rsi:.1f}) 超买 (≥{self.overbought})，当前无持仓",
                    price=current_price,
                    mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.SELL,
                reason=f"RSI({current_rsi:.1f}) 超买 (≥{self.overbought})",
                target_ratio=0.0,
                suggested_quantity=current_qty,
                price=current_price,
                mode=self.mode,
            )

        # ── 中间区域 → HOLD ──────────────────────────────────
        zone = "偏弱" if current_rsi < 50 else "偏强"
        direction = "回落中" if current_rsi < prev_rsi else "上升中"
        return StrategyDecision(
            symbol=symbol,
            signal=StrategySignal.HOLD,
            reason=f"RSI({current_rsi:.1f}) {zone} {direction}",
            price=current_price,
            mode=self.mode,
        )
