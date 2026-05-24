"""风控管理器（RiskManager）。

职责：
  1. 买入前仓位检查（check_entry）——拦截超限仓位
  2. 持仓止损/止盈扫描（scan_positions）——逐笔检查每只持仓
  3. 全局最大回撤熔断（_check_drawdown）——触发时清仓停机
  4. 风险指标计算（get_risk_metrics）——供 GUI 实时展示

设计原则：
  · 纯逻辑，不直接调用 Broker/DB，所有输入由外部传入
  · 可全局共享一个实例，也可按策略独立配置
  · 所有参数均可在运行时动态修改（GUI 配置面板写 config 字段即可）

典型调用链：
    # 买入前拦截
    ok, reason = risk_mgr.check_entry(symbol, price, quantity, context)

    # 每 tick 止损/止盈扫描（心跳定时器中）
    signals = risk_mgr.scan_positions(positions, latest_prices, current_assets)
    for sig in signals:
        trading_service.submit_manual_order(sig.symbol, "SELL", sig.quantity, sig.current_price,
                                            strategy_name=sig.trigger_type)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from autotrader_app.strategies.base import StrategyContext


# ──────────────────────────────────────────────────────────────────────────────
# 数据类
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    """风控参数配置（全量可持久化存储）。

    所有比例均为百分比形式（5.0 表示 5%）。
    金额单位为人民币元。
    """

    # ── 单股止损/止盈 ──────────────────────────────────────
    stop_loss_pct: float = 5.0
    """买入均价下跌超过该比例触发止损（正数表示跌幅，5.0 = 跌 5% 止损）。"""

    take_profit_pct: float = 15.0
    """买入均价上涨超过该比例触发止盈（正数表示涨幅，15.0 = 涨 15% 止盈）。"""

    # ── 全局最大回撤熔断 ───────────────────────────────────
    max_drawdown_pct: float = 8.0
    """从历史峰值总资产回撤超过该比例时熔断（全部平仓并停机）。"""

    # ── 仓位上限 ────────────────────────────────────────────
    max_single_position_pct: float = 30.0
    """单只股票持仓市值不得超过总资产的该比例。"""

    max_total_position_pct: float = 80.0
    """所有持仓市值之和不得超过总资产的该比例。"""

    # ── 实盘安全保护 ────────────────────────────────────────
    allowed_symbols: list[str] = field(default_factory=lambda: [])
    """实盘模式允许交易的股票代码白名单（空=不限制）。"""

    max_daily_buy_amount: float = 50_000.0
    """单日最大买入总金额（实盘模式），0=不限制。"""

    min_trade_interval_minutes: int = 1
    """同一只股票两次交易最小间隔（分钟），0=不限制。"""

    max_single_order_amount: float = 50_000.0
    """单笔委托最大金额（实盘模式），0=不限制。"""

    # ── 全局开关 ────────────────────────────────────────────
    enabled: bool = True
    """False 时所有风控检查均通过（仅调试用）。"""


@dataclass
class StopSignal:
    """单笔止损/止盈/熔断指令。

    由 scan_positions 产生，外部负责执行实际卖单。
    """

    symbol: str
    """需要平仓的股票代码。"""

    quantity: int
    """建议平仓数量（通常为持仓全量）。"""

    trigger_type: str
    """触发类型：'stop_loss' | 'take_profit' | 'max_drawdown'。"""

    reason: str
    """人类可读的触发原因（用于日志/GUI 展示）。"""

    current_price: float
    """触发时的最新价（用于下单）。"""

    avg_price: float
    """持仓成本均价。"""

    pnl_pct: float
    """当前浮动盈亏百分比（正=盈利，负=亏损）。"""

    triggered_at: datetime = field(default_factory=datetime.now)


@dataclass
class RiskMetrics:
    """实时风险快照（GUI 展示用）。"""

    current_drawdown_pct: float = 0.0
    """当前从峰值回撤百分比（负数=亏损中，正数=已回撤）。"""

    peak_assets: float = 0.0
    """历史最高总资产（回撤基准）。"""

    total_position_pct: float = 0.0
    """当前总仓位占总资产百分比。"""

    risk_level: str = "正常"
    """风险级别：'正常' | '警告' | '危险' | '熔断'。"""

    at_risk_symbols: list[str] = field(default_factory=list)
    """当前已触及止损/止盈阈值一半的股票代码（预警）。"""


# ──────────────────────────────────────────────────────────────────────────────
# 核心：RiskManager
# ──────────────────────────────────────────────────────────────────────────────

class RiskManager:
    """风险管理器。

    单例模式友好（一个 MainWindow 持有一个实例），也可按策略独立持有。

    Attributes:
        config:       当前风控参数，可在运行时动态修改。
        _peak_assets: 历史最高总资产，用于最大回撤计算；初始为 initial_cash。
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        initial_cash: float = 100_000.0,
    ) -> None:
        self.config: RiskConfig = config or RiskConfig()
        self._peak_assets: float = initial_cash
        self._initial_cash: float = initial_cash
        self._drawdown_triggered: bool = False
        # 每日交易统计
        self._today_buy_amount: float = 0.0
        self._today_date: str = ""
        # 交易间隔跟踪
        self._last_trade_time: dict[str, datetime] = {}

    # ──────────────────────────────────────────────────────
    # 公开 API
    # ──────────────────────────────────────────────────────

    def reset(self, initial_cash: float | None = None) -> None:
        """重置峰值和熔断状态（回测开始 / 清仓后调用）。"""
        if initial_cash is not None:
            self._initial_cash = initial_cash
        self._peak_assets = self._initial_cash
        self._drawdown_triggered = False
        self._today_buy_amount = 0.0
        self._today_date = ""
        self._last_trade_time.clear()
        logger.info("RiskManager reset: peak_assets={:.2f}", self._peak_assets)

    def update_peak(self, current_assets: float) -> None:
        """每 tick 更新峰值（在 scan_positions 之前调用）。"""
        if current_assets > self._peak_assets:
            self._peak_assets = current_assets

    def check_entry(
        self,
        symbol: str,
        price: float,
        quantity: int,
        context: "StrategyContext",
    ) -> tuple[bool, str]:
        """买入前风控检查：仓位上限约束。

        Args:
            symbol:   目标股票代码。
            price:    预期成交价格。
            quantity: 拟买入数量。
            context:  策略上下文（含总资产、当前持仓等信息）。

        Returns:
            (True, "OK")         — 通过，允许买入。
            (False, reason_msg)  — 被拦截，附带拦截原因。
        """
        if not self.config.enabled:
            return True, "风控已禁用"

        total_assets = context.total_capital
        if total_assets <= 0:
            return True, "OK"

        # ── 总仓位检查 ──────────────────────────────────
        current_mv = sum(
            context.positions.get(s, 0) * context.latest_prices.get(s, 0.0)
            for s in context.positions
        )
        new_mv = current_mv + quantity * price
        total_position_pct = new_mv / total_assets * 100
        if total_position_pct > self.config.max_total_position_pct:
            return False, (
                f"总仓位将达 {total_position_pct:.1f}%，"
                f"超过上限 {self.config.max_total_position_pct:.0f}%"
            )

        # ── 股票池白名单检查 ────────────────────────────
        if self.config.allowed_symbols and symbol not in self.config.allowed_symbols:
            return False, f"{symbol} 不在允许交易的白名单中"

        # ── 单笔金额上限检查 ────────────────────────────
        order_amount = quantity * price
        if self.config.max_single_order_amount > 0 and order_amount > self.config.max_single_order_amount:
            return False, (
                f"单笔委托金额 {order_amount:.0f} 元超过上限 {self.config.max_single_order_amount:.0f} 元"
            )

        # ── 每日买入总额检查 ────────────────────────────
        self._check_daily_reset()
        if self.config.max_daily_buy_amount > 0:
            new_daily = self._today_buy_amount + order_amount
            if new_daily > self.config.max_daily_buy_amount:
                return False, (
                    f"当日累计买入将达 {new_daily:.0f} 元，超过上限 {self.config.max_daily_buy_amount:.0f} 元"
                )

        # ── 交易间隔检查 ────────────────────────────────
        interval_ok, interval_msg = self._check_interval(symbol)
        if not interval_ok:
            return False, interval_msg

        # ── 单股仓位检查 ────────────────────────────────
        existing_qty = context.positions.get(symbol, 0)
        existing_price = context.latest_prices.get(symbol, price)
        single_mv = (existing_qty * existing_price) + (quantity * price)
        single_pct = single_mv / total_assets * 100
        if single_pct > self.config.max_single_position_pct:
            return False, (
                f"{symbol} 仓位将达 {single_pct:.1f}%，"
                f"超过单股上限 {self.config.max_single_position_pct:.0f}%"
            )

        return True, "OK"

    # ──────────────────────────────────────────────────────
    # 实盘安全限制辅助方法
    # ──────────────────────────────────────────────────────

    def _check_daily_reset(self) -> None:
        """检查是否需要重置每日统计数据。"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today_date:
            self._today_buy_amount = 0.0
            self._today_date = today

    def _check_interval(self, symbol: str) -> tuple[bool, str]:
        """检查同一股票的交易间隔限制。"""
        if self.config.min_trade_interval_minutes <= 0:
            return True, ""
        last = self._last_trade_time.get(symbol)
        if last is None:
            return True, ""
        elapsed = (datetime.now() - last).total_seconds() / 60
        if elapsed < self.config.min_trade_interval_minutes:
            return False, (
                f"{symbol} 距上次交易仅 {elapsed:.1f} 分钟，"
                f"不足最小间隔 {self.config.min_trade_interval_minutes} 分钟"
            )
        return True, ""

    def record_trade(self, symbol: str, side: str, amount: float) -> None:
        """记录一笔已完成交易（成交后调用，更新统计状态）。

        Args:
            symbol: 股票代码。
            side:   "BUY" 或 "SELL"。
            amount: 成交金额（quantity * price）。
        """
        self._last_trade_time[symbol] = datetime.now()
        self._check_daily_reset()
        if side.upper() == "BUY":
            self._today_buy_amount += amount

    def scan_positions(
        self,
        positions: list[dict],
        latest_prices: dict[str, float],
        current_assets: float,
    ) -> list[StopSignal]:
        """扫描所有持仓，返回需要触发的止损/止盈/熔断信号列表。

        Args:
            positions:      持仓列表，每项为 {"symbol", "quantity", "avg_price"} dict。
            latest_prices:  最新行情价格映射（symbol -> price）。
            current_assets: 当前总资产（现金 + 持仓市值）。

        Returns:
            StopSignal 列表（按优先级排序：熔断 > 止损 > 止盈）。
            若无需触发，返回空列表。
        """
        if not self.config.enabled or not positions:
            return []

        signals: list[StopSignal] = []

        # ── 1. 全局最大回撤熔断（优先级最高）──────────────
        drawdown_signal = self._check_drawdown(positions, latest_prices, current_assets)
        if drawdown_signal:
            logger.warning(
                "⚠️  全局回撤熔断触发！当前总资产={:.2f} 峰值={:.2f} 回撤={:.2f}%",
                current_assets, self._peak_assets,
                (self._peak_assets - current_assets) / self._peak_assets * 100,
            )
            return drawdown_signal  # 熔断时直接返回，不再检查单股

        # ── 2. 逐股止损/止盈扫描 ──────────────────────────
        for pos in positions:
            symbol    = str(pos["symbol"])
            quantity  = int(pos["quantity"])
            avg_price = float(pos["avg_price"])

            if quantity <= 0 or avg_price <= 0:
                continue

            current_price = latest_prices.get(symbol)
            if current_price is None or current_price <= 0:
                continue  # 无最新价，跳过

            pnl_pct = (current_price - avg_price) / avg_price * 100

            # 止损（亏损超阈值）
            if pnl_pct <= -abs(self.config.stop_loss_pct):
                signals.append(StopSignal(
                    symbol=symbol,
                    quantity=quantity,
                    trigger_type="stop_loss",
                    reason=(
                        f"止损触发：{symbol} 亏损 {pnl_pct:.2f}%"
                        f"（均价 {avg_price:.2f} → 现价 {current_price:.2f}，"
                        f"阈值 -{self.config.stop_loss_pct:.1f}%）"
                    ),
                    current_price=current_price,
                    avg_price=avg_price,
                    pnl_pct=pnl_pct,
                ))
                logger.warning("止损: {} pnl={:.2f}%", symbol, pnl_pct)
                continue  # 已止损，不再检查止盈

            # 止盈（盈利超阈值）
            if pnl_pct >= abs(self.config.take_profit_pct):
                signals.append(StopSignal(
                    symbol=symbol,
                    quantity=quantity,
                    trigger_type="take_profit",
                    reason=(
                        f"止盈触发：{symbol} 盈利 {pnl_pct:.2f}%"
                        f"（均价 {avg_price:.2f} → 现价 {current_price:.2f}，"
                        f"阈值 +{self.config.take_profit_pct:.1f}%）"
                    ),
                    current_price=current_price,
                    avg_price=avg_price,
                    pnl_pct=pnl_pct,
                ))
                logger.info("止盈: {} pnl={:.2f}%", symbol, pnl_pct)

        return signals

    def get_risk_metrics(
        self,
        positions: list[dict],
        latest_prices: dict[str, float],
        current_assets: float,
    ) -> RiskMetrics:
        """计算并返回当前实时风险快照（GUI 状态栏刷新用）。

        Args:
            positions:      持仓列表（同 scan_positions）。
            latest_prices:  最新行情价格。
            current_assets: 当前总资产。

        Returns:
            RiskMetrics 对象。
        """
        # 总仓位
        total_mv = sum(
            int(p["quantity"]) * latest_prices.get(str(p["symbol"]), float(p["avg_price"]))
            for p in positions
            if int(p["quantity"]) > 0
        )
        position_pct = total_mv / current_assets * 100 if current_assets > 0 else 0.0

        # 回撤
        if self._peak_assets > 0:
            drawdown_pct = (self._peak_assets - current_assets) / self._peak_assets * 100
        else:
            drawdown_pct = 0.0

        # 风险级别
        risk_level: str
        half_dd = self.config.max_drawdown_pct / 2
        if drawdown_pct >= self.config.max_drawdown_pct:
            risk_level = "熔断"
        elif drawdown_pct >= half_dd:
            risk_level = "危险"
        elif drawdown_pct >= self.config.max_drawdown_pct * 0.3:
            risk_level = "警告"
        else:
            risk_level = "正常"

        # 临近止损的股票（亏损超过止损阈值的 50%）
        at_risk: list[str] = []
        half_sl = self.config.stop_loss_pct / 2
        for p in positions:
            symbol    = str(p["symbol"])
            avg_price = float(p["avg_price"])
            curr      = latest_prices.get(symbol)
            if curr and avg_price > 0:
                pnl_pct = (curr - avg_price) / avg_price * 100
                if pnl_pct <= -half_sl:
                    at_risk.append(symbol)

        return RiskMetrics(
            current_drawdown_pct=drawdown_pct,
            peak_assets=self._peak_assets,
            total_position_pct=position_pct,
            risk_level=risk_level,
            at_risk_symbols=at_risk,
        )

    # ──────────────────────────────────────────────────────
    # 内部方法
    # ──────────────────────────────────────────────────────

    def _check_drawdown(
        self,
        positions: list[dict],
        latest_prices: dict[str, float],
        current_assets: float,
    ) -> list[StopSignal]:
        """检查全局最大回撤，熔断只触发一次（reset 可清除）。"""
        if self._peak_assets <= 0 or self._drawdown_triggered:
            return []

        drawdown_pct = (self._peak_assets - current_assets) / self._peak_assets * 100
        if drawdown_pct < self.config.max_drawdown_pct:
            return []

        self._drawdown_triggered = True
        signals: list[StopSignal] = []
        for pos in positions:
            symbol    = str(pos["symbol"])
            quantity  = int(pos["quantity"])
            avg_price = float(pos["avg_price"])
            if quantity <= 0:
                continue
            curr = latest_prices.get(symbol, avg_price)
            pnl_pct = (curr - avg_price) / avg_price * 100 if avg_price > 0 else 0.0
            signals.append(StopSignal(
                symbol=symbol,
                quantity=quantity,
                trigger_type="max_drawdown",
                reason=(
                    f"熔断平仓：{symbol}（全局回撤 {drawdown_pct:.2f}%"
                    f" > 阈值 {self.config.max_drawdown_pct:.1f}%）"
                ),
                current_price=curr,
                avg_price=avg_price,
                pnl_pct=pnl_pct,
            ))
        return signals
