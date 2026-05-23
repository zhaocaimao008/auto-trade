"""MACD 趋势策略。

A 股标准 MACD 指标计算（EMA 模式，adjust=False），含金叉/死叉信号
以及可选的 MA60 趋势过滤，直接继承 StrategyBase，兼容回测与实时模式。
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


class MACDStrategy(StrategyBase):
    """MACD 趋势策略。

    指标计算（A 股常用参数 12-26-9）：
    ─────────────────────────────────
    EMA_fast  = EMA(close, fast_period)
    EMA_slow  = EMA(close, slow_period)
    DIF       = EMA_fast - EMA_slow          ← 差值线（快线）
    DEA       = EMA(DIF,  signal_period)     ← 信号线（慢线）
    MACD_hist = (DIF - DEA) × 2             ← 柱状图（A 股惯例乘 2）

    交易信号：
    ─────────────────────────────────
    · 金叉买入：DIF 从下穿 DEA → 前根 DIF ≤ DEA，当前 DIF > DEA
    · 死叉卖出：DIF 从上穿 DEA → 前根 DIF ≥ DEA，当前 DIF < DEA
    · 无交叉：HOLD，附带当前 DIF/DEA 状态说明

    可选过滤器：
    ─────────────────────────────────
    use_ma60_filter=True 时，仅当收盘价高于 MA60 才允许买入，
    避免在下行趋势中被 MACD 金叉误导（"死叉陷阱"）。
    """

    name = "macd_strategy"

    # ── A 股 MACD 标准默认参数 ─────────────────────────────
    DEFAULT_FAST_PERIOD: int = 12
    DEFAULT_SLOW_PERIOD: int = 26
    DEFAULT_SIGNAL_PERIOD: int = 9
    DEFAULT_MA60_WINDOW: int = 60

    def __init__(
        self,
        fast_period: int = DEFAULT_FAST_PERIOD,
        slow_period: int = DEFAULT_SLOW_PERIOD,
        signal_period: int = DEFAULT_SIGNAL_PERIOD,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
        use_ma60_filter: bool = False,
        ma60_window: int = DEFAULT_MA60_WINDOW,
    ) -> None:
        """初始化 MACD 策略。

        Args:
            fast_period:     快线 EMA 周期，默认 12。
            slow_period:     慢线 EMA 周期，默认 26；必须 > fast_period。
            signal_period:   信号线 EMA 周期，默认 9。
            symbol_list:     股票池，为空时允许所有股票。
            position_ratio:  单笔买入占总资金的目标比例，默认 20%。
            mode:            REALTIME（实时）或 BACKTEST（回测）。
            use_ma60_filter: True 时买入前先检查价格是否高于 MA60。
            ma60_window:     MA 过滤的均线周期，默认 60。
        """
        super().__init__(symbol_list=symbol_list, position_ratio=position_ratio, mode=mode)

        # 参数合法性校验
        if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
            raise ValueError("MACD 各周期参数必须大于 0。")
        if fast_period >= slow_period:
            raise ValueError(f"fast_period({fast_period}) 必须小于 slow_period({slow_period})。")

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.use_ma60_filter = use_ma60_filter
        self.ma60_window = ma60_window

        # EMA 稳定至少需要 slow_period + signal_period + 若干额外行
        self._min_bars: int = slow_period + signal_period + 5

    # ─────────────────────────────────────────────────────────
    # 公开工具方法：可被 GUI / 回测模块独立调用
    # ─────────────────────────────────────────────────────────

    def compute_macd(
        self, close: pd.Series
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """计算 DIF、DEA 和 MACD 柱状图三条线。

        使用 pandas ewm(adjust=False) 模拟同花顺/东方财富的 EMA 计算方式。

        Args:
            close: 收盘价序列（index 不要求连续）。

        Returns:
            (dif, dea, hist) 三条同长度 pd.Series：
            · dif  — 差值线（快 EMA - 慢 EMA）
            · dea  — 信号线（dif 的 EMA）
            · hist — 柱状图 = (dif - dea) × 2
        """
        # adjust=False：递推计算，与 A 股行情软件一致
        ema_fast = close.ewm(span=self.fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow_period, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=self.signal_period, adjust=False).mean()
        hist = (dif - dea) * 2  # A 股惯例乘 2 后才是"柱状图高度"
        return dif, dea, hist

    # ─────────────────────────────────────────────────────────
    # 核心：信号生成（StrategyBase 抽象方法实现）
    # ─────────────────────────────────────────────────────────

    def generate_signal(
        self, symbol: str, bars: pd.DataFrame, context: StrategyContext
    ) -> StrategyDecision:
        """根据最新两根 K 线的 DIF/DEA 关系判断金叉或死叉。"""

        # ── 数据量不足保护 ──────────────────────────────────
        if bars.empty or len(bars) < self._min_bars:
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.HOLD,
                reason=(
                    f"行情数据不足（需要 ≥{self._min_bars} 条，"
                    f"当前 {len(bars)} 条），无法计算 MACD"
                ),
                mode=self.mode,
            )

        df = bars.copy()
        close = df["close"].astype(float)
        current_price = float(close.iloc[-1])

        # 同步最新价到上下文（供仓位计算使用）
        context.latest_prices[symbol] = current_price

        # ── 计算 MACD 三线 ──────────────────────────────────
        dif, dea, _ = self.compute_macd(close)

        # 取倒数第二根（前一根）和最后一根的 DIF / DEA
        prev_dif = float(dif.iloc[-2])
        prev_dea = float(dea.iloc[-2])
        curr_dif = float(dif.iloc[-1])
        curr_dea = float(dea.iloc[-1])

        # ── 判断交叉方向 ────────────────────────────────────
        # 金叉：DIF 从 DEA 下方向上穿越
        golden_cross: bool = (prev_dif <= prev_dea) and (curr_dif > curr_dea)
        # 死叉：DIF 从 DEA 上方向下穿越
        death_cross: bool = (prev_dif >= prev_dea) and (curr_dif < curr_dea)

        # ── 可选：MA60 趋势过滤 ─────────────────────────────
        ma60_pass = True
        ma60_note = ""
        if self.use_ma60_filter:
            if len(close) >= self.ma60_window:
                ma60_val = float(close.rolling(self.ma60_window).mean().iloc[-1])
                if current_price < ma60_val:
                    ma60_pass = False
                    ma60_note = (
                        f"价格({current_price:.2f}) < MA{self.ma60_window}"
                        f"({ma60_val:.2f})，不符合上行趋势"
                    )
            else:
                # 数据不足 ma60_window 时跳过过滤（不拦截）
                ma60_note = f"数据不足 {self.ma60_window} 条，跳过 MA60 过滤"

        # ── 金叉 → 尝试买入 ─────────────────────────────────
        if golden_cross:
            # MA60 过滤
            if not ma60_pass:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"MACD 金叉，但 {ma60_note}",
                    price=current_price,
                    mode=self.mode,
                )

            # 整体 + 单股仓位风控
            risk_ok, risk_msg = self.check_risk_limits(symbol, context)
            if not risk_ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"MACD 金叉，但风控拦截：{risk_msg}",
                    price=current_price,
                    mode=self.mode,
                )

            # 计算本次买入数量（按仓位比例、取整到 100 股）
            target_ratio = min(self.position_ratio, self.max_single_position_ratio)
            quantity = self.calculate_order_quantity(symbol, current_price, context, target_ratio)
            if quantity <= 0:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason="MACD 金叉，但可用资金不足以买入一手（100 股）",
                    target_ratio=target_ratio,
                    price=current_price,
                    mode=self.mode,
                )

            # 构造买入原因描述
            filter_note = " | MA60 过滤通过" if self.use_ma60_filter and ma60_pass else ""
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.BUY,
                reason=(
                    f"MACD 金叉 | DIF({curr_dif:.4f}) 上穿 DEA({curr_dea:.4f})"
                    f"{filter_note}"
                ),
                target_ratio=target_ratio,
                suggested_quantity=quantity,
                price=current_price,
                mode=self.mode,
            )

        # ── 死叉 → 尝试卖出 ─────────────────────────────────
        if death_cross:
            current_qty = context.positions.get(symbol, 0)
            if current_qty <= 0:
                # 无持仓时死叉不产生实际交易，记录观察信号
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=(
                        f"MACD 死叉 | DIF({curr_dif:.4f}) 下穿 DEA({curr_dea:.4f})"
                        "，当前无持仓"
                    ),
                    price=current_price,
                    mode=self.mode,
                )

            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.SELL,
                reason=(
                    f"MACD 死叉 | DIF({curr_dif:.4f}) 下穿 DEA({curr_dea:.4f})"
                ),
                target_ratio=0.0,
                suggested_quantity=current_qty,  # 全部卖出
                price=current_price,
                mode=self.mode,
            )

        # ── 无交叉：HOLD，附带状态说明 ─────────────────────
        # 判断当前所处的 MACD 区间，方便 GUI 日志展示
        zone = "多头区间" if curr_dif > 0 else "空头区间"
        trend = "金叉形态" if curr_dif > curr_dea else "死叉形态"
        return StrategyDecision(
            symbol=symbol,
            signal=StrategySignal.HOLD,
            reason=(
                f"MACD 无交叉 | {zone} / {trend} "
                f"DIF={curr_dif:.4f} DEA={curr_dea:.4f}"
            ),
            price=current_price,
            mode=self.mode,
        )
