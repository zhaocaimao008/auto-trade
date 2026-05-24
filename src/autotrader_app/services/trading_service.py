from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.broker.mock_broker import MockBroker
from autotrader_app.data.providers import DataProviderFactory, DataRequest, MarketDataProvider
from autotrader_app.models import OrderRequest, OrderSide
from autotrader_app.strategies.base import StrategyBase, StrategyContext, StrategySignal
from autotrader_app.strategies.double_ma_strategy import DoubleMA_Strategy


class TradingService:
    """交易服务，负责串联数据、策略和模拟下单。

    支持单策略评估（evaluate_strategy）和多策略并行评估（run_all_strategies）。
    策略实例可通过 register_strategy / unregister_strategy 动态注册。
    """

    def __init__(self, provider: MarketDataProvider | None = None, broker: BrokerBase | None = None) -> None:
        self.provider = provider or DataProviderFactory.create()
        self.broker = broker or MockBroker()
        # 默认双均线策略（向后兼容旧有调用）
        self.strategy = DoubleMA_Strategy(symbol_list=[], position_ratio=0.2)
        # 多策略注册表：name -> 策略实例
        self._strategy_registry: dict[str, StrategyBase] = {}

    # ── 多策略注册管理 ─────────────────────────────────────

    def register_strategy(self, strategy: StrategyBase) -> None:
        """向服务注册一个策略实例（以 strategy.name 为 key）。"""
        self._strategy_registry[strategy.name] = strategy
        logger.info("Strategy registered: {}", strategy.name)

    def unregister_strategy(self, name: str) -> bool:
        """按名称移除已注册策略，返回是否成功。"""
        if name in self._strategy_registry:
            del self._strategy_registry[name]
            logger.info("Strategy unregistered: {}", name)
            return True
        return False

    def clear_strategies(self) -> None:
        """清空所有已注册策略。"""
        self._strategy_registry.clear()

    def get_registered_strategies(self) -> list[StrategyBase]:
        """返回当前注册的所有策略列表（副本）。"""
        return list(self._strategy_registry.values())

    # ── 多策略并行评估 ─────────────────────────────────────

    def run_strategy_instance(
        self,
        strategy: StrategyBase,
        symbol: str,
        bars: pd.DataFrame | None = None,
        days: int = 120,
    ) -> dict:
        """用指定策略实例对单只股票进行评估，返回决策字典。

        Args:
            strategy: 任意继承 StrategyBase 的策略实例。
            symbol:   股票代码。
            bars:     预先获取的行情 DataFrame；为 None 时自动拉取。
            days:     自动拉取时回溯的交易日数。

        Returns:
            包含 signal / reason / decision / latest_bar / bar_count 的字典。
        """
        if bars is None or bars.empty:
            bars = self.fetch_bars(symbol, days=days)

        context = self._build_context(symbol, bars)
        decision = strategy.run(symbol, bars, context)
        latest = bars.iloc[-1].to_dict() if not bars.empty else {}
        return {
            "strategy": strategy.name,
            "signal": decision.signal.value,
            "reason": decision.reason,
            "decision": decision,
            "latest_bar": latest,
            "bar_count": len(bars),
        }

    def run_all_strategies(
        self,
        symbol: str,
        bars: pd.DataFrame | None = None,
        days: int = 120,
    ) -> list[dict]:
        """对所有已注册策略并行评估同一只股票。

        返回列表，每个元素对应一个策略的决策结果字典。
        """
        if bars is None or bars.empty:
            bars = self.fetch_bars(symbol, days=days)

        results: list[dict] = []
        for strategy in self._strategy_registry.values():
            try:
                result = self.run_strategy_instance(strategy, symbol, bars=bars)
                results.append(result)
            except Exception as exc:
                logger.warning("Strategy {} failed on {}: {}", strategy.name, symbol, exc)
                results.append({
                    "strategy": strategy.name,
                    "signal": "HOLD",
                    "reason": f"评估异常：{exc}",
                    "decision": None,
                    "latest_bar": {},
                    "bar_count": 0,
                })
        return results

    # ── 原有方法（向后兼容） ──────────────────────────────

    def fetch_bars(self, symbol: str, days: int = 120) -> pd.DataFrame:
        request = DataRequest(symbol=symbol, start=datetime.now() - timedelta(days=days), end=datetime.now())
        bars = self.provider.get_daily_bars(request)
        logger.info("Fetched {} bars for {}", len(bars), symbol)
        return bars

    def evaluate_strategy(self, symbol: str) -> dict:
        bars = self.fetch_bars(symbol)
        context = self._build_context(symbol, bars)
        signal = self.strategy.run(symbol, bars, context)
        latest = bars.iloc[-1].to_dict() if not bars.empty else {}
        return {"signal": signal.signal.value, "reason": signal.reason, "decision": signal, "latest_bar": latest, "bar_count": len(bars)}

    def submit_manual_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        strategy_name: str = "manual",
    ):
        order = OrderRequest(
            symbol=symbol,
            side=OrderSide(side.upper()),
            quantity=quantity,
            price=price,
            strategy_name=strategy_name,
        )
        return self.broker.submit_order(order)

    def run_strategy_once(self, symbol: str, lot_size: int = 100):
        bars = self.fetch_bars(symbol)
        if bars.empty:
            return {"action": "NONE", "reason": "无行情数据"}

        context = self._build_context(symbol, bars)
        decision = self.strategy.run(symbol, bars, context)

        if decision.signal == StrategySignal.BUY:
            quantity = decision.suggested_quantity or lot_size
            result = self.submit_manual_order(symbol, "BUY", quantity, decision.price)
            return {"action": "BUY", "decision": decision, "order": asdict(result)}
        if decision.signal == StrategySignal.SELL:
            quantity = decision.suggested_quantity or lot_size
            result = self.submit_manual_order(symbol, "SELL", quantity, decision.price)
            return {"action": "SELL", "decision": decision, "order": asdict(result)}
        return {"action": "HOLD", "decision": decision, "price": decision.price}

    def _build_context(self, symbol: str, bars: pd.DataFrame) -> StrategyContext:
        positions_df = self.broker.get_positions()
        positions = {}
        latest_prices = {}
        market_value = 0.0

        if not positions_df.empty:
            for _, row in positions_df.iterrows():
                positions[str(row["symbol"])] = int(row["quantity"])
                latest_prices[str(row["symbol"])] = float(row["avg_price"])
                market_value += float(row["quantity"]) * float(row["avg_price"])

        if not bars.empty:
            latest_prices[symbol] = float(bars.iloc[-1]["close"])

        total_assets = self.broker.account.total_assets if self.broker.account.total_assets > 0 else self.broker.account.cash
        total_position_ratio = market_value / total_assets if total_assets > 0 else 0.0

        return StrategyContext(
            total_capital=total_assets,
            available_cash=self.broker.account.cash,
            total_position_ratio=total_position_ratio,
            positions=positions,
            latest_prices=latest_prices,
        )
