from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from autotrader_app.risk.risk_manager import RiskManager


class StrategyMode(str, Enum):
    """策略运行模式。"""

    BACKTEST = "backtest"
    REALTIME = "realtime"


class StrategySignal(str, Enum):
    """标准交易信号。"""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(slots=True)
class StrategyContext:
    """策略上下文。

    用于在回测和实时模式下向策略传入统一的账户状态。
    """

    total_capital: float
    available_cash: float
    total_position_ratio: float = 0.0
    positions: dict[str, int] = field(default_factory=dict)
    latest_prices: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class StrategyDecision:
    """策略决策结果。"""

    symbol: str
    signal: StrategySignal
    reason: str
    target_ratio: float = 0.0
    suggested_quantity: int = 0
    price: float = 0.0
    mode: StrategyMode = StrategyMode.REALTIME


class StrategyBase(ABC):
    """策略基类。

    所有具体策略需要继承这个类，并实现信号生成逻辑。
    """

    name: str = "base_strategy"

    def __init__(
        self,
        symbol_list: list[str] | None = None,
        position_ratio: float = 0.2,
        mode: StrategyMode = StrategyMode.REALTIME,
        risk_manager: "RiskManager | None" = None,
    ) -> None:
        self.symbol_list = symbol_list or []
        self.position_ratio = position_ratio
        self.mode = mode

        # 简单风控约束：单股不超过总资金 30%，整体仓位不超过 80%。
        self.max_single_position_ratio = 0.30
        self.max_total_position_ratio = 0.80

        # 外部注入的 RiskManager（可选）；注入后买入前会自动调用仓位检查
        self.risk_manager: "RiskManager | None" = risk_manager

    def set_mode(self, mode: StrategyMode) -> None:
        """切换运行模式。"""

        self.mode = mode

    def update_symbol_list(self, symbols: list[str]) -> None:
        """更新股票池。"""

        self.symbol_list = symbols

    def validate_symbol(self, symbol: str) -> bool:
        """检查股票是否在当前策略股票池内。"""

        return symbol in self.symbol_list if self.symbol_list else True

    def check_risk_limits(self, symbol: str, context: StrategyContext) -> tuple[bool, str]:
        """统一风控检查。"""

        if context.total_position_ratio >= self.max_total_position_ratio:
            return False, "整体仓位已达到 80% 上限"

        current_price = context.latest_prices.get(symbol, 0.0)
        current_qty = context.positions.get(symbol, 0)
        single_position_value = current_qty * current_price
        single_ratio = single_position_value / context.total_capital if context.total_capital > 0 else 0.0

        if single_ratio >= self.max_single_position_ratio:
            return False, "单股仓位已达到 30% 上限"

        return True, "OK"

    def calculate_order_quantity(self, symbol: str, price: float, context: StrategyContext, target_ratio: float) -> int:
        """根据目标仓位比例计算建议下单股数。

        A 股按 100 股一手进行取整。
        """

        if price <= 0 or context.total_capital <= 0:
            return 0

        budget = min(context.total_capital * target_ratio, context.available_cash)
        quantity = int(budget // price)
        lot_quantity = (quantity // 100) * 100
        return max(lot_quantity, 0)

    def run(self, symbol: str, bars: pd.DataFrame, context: StrategyContext) -> StrategyDecision:
        """统一策略执行入口。

        执行顺序：
        1. 校验股票池；
        2. 预处理 K 线数据；
        3. 调用 generate_signal 生成信号；
        4. 若挂载了 RiskManager，用其对买入信号做仓位二次拦截；
           对 HOLD 信号检查是否需要强制止损/止盈平仓（覆盖为 SELL）。
        """
        if not self.validate_symbol(symbol):
            return StrategyDecision(
                symbol=symbol,
                signal=StrategySignal.HOLD,
                reason="股票不在当前策略股票池内",
                mode=self.mode,
            )

        bars = self.prepare_data(bars)
        decision = self.generate_signal(symbol, bars, context)

        # ── RiskManager 二次检查 ──────────────────────────────
        if self.risk_manager is not None and self.risk_manager.config.enabled:
            decision = self._apply_risk_check(symbol, decision, context)

        return decision

    def _apply_risk_check(
        self,
        symbol: str,
        decision: StrategyDecision,
        context: StrategyContext,
    ) -> StrategyDecision:
        """将 RiskManager 的检查结果叠加到策略决策上。

        规则：
        · BUY  → 调用 check_entry 做仓位拦截；超限时降级为 HOLD。
        · HOLD → 检查该股票当前持仓是否触发止损/止盈；触发时升级为 SELL。
        · SELL → 不干预（策略已决定卖出）。
        """
        rm = self.risk_manager
        assert rm is not None

        if decision.signal == StrategySignal.BUY:
            ok, reason = rm.check_entry(
                symbol,
                decision.price,
                decision.suggested_quantity,
                context,
            )
            if not ok:
                return StrategyDecision(
                    symbol=symbol,
                    signal=StrategySignal.HOLD,
                    reason=f"风控拦截买入：{reason}",
                    price=decision.price,
                    mode=self.mode,
                )

        elif decision.signal == StrategySignal.HOLD:
            # 用最新价检查该股票是否需要止损/止盈
            current_price = context.latest_prices.get(symbol, 0.0)
            avg_price = 0.0
            qty = context.positions.get(symbol, 0)
            if qty > 0 and current_price > 0:
                # avg_price 从 latest_prices 取不到时尝试反推（模拟模式下 context 没有 avg_price 字段）
                # 单独构造一条最小 positions 列表
                pos_list = [{"symbol": symbol, "quantity": qty, "avg_price": context.latest_prices.get(symbol, 0.0)}]
                # 尝试从 context 里获取真实成本（如果调用方填充了 avg_prices 字段）
                avg_price_map: dict[str, float] = getattr(context, "avg_prices", {})
                if symbol in avg_price_map:
                    pos_list[0]["avg_price"] = avg_price_map[symbol]

                stop_signals = rm.scan_positions(
                    pos_list,
                    {symbol: current_price},
                    context.total_capital,
                )
                if stop_signals:
                    sig = stop_signals[0]
                    return StrategyDecision(
                        symbol=symbol,
                        signal=StrategySignal.SELL,
                        reason=sig.reason,
                        target_ratio=0.0,
                        suggested_quantity=qty,
                        price=current_price,
                        mode=self.mode,
                    )

        return decision

    def prepare_data(self, bars: pd.DataFrame) -> pd.DataFrame:
        """预处理行情数据。"""

        if bars is None or bars.empty:
            return pd.DataFrame()
        prepared = bars.copy()
        prepared = prepared.sort_values("datetime").reset_index(drop=True)
        return prepared

    @abstractmethod
    def generate_signal(self, symbol: str, bars: pd.DataFrame, context: StrategyContext) -> StrategyDecision:
        """生成具体交易信号。"""

        raise NotImplementedError
