from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.broker.mock_broker import MockBroker
from autotrader_app.data.providers import DataProviderFactory, DataRequest, MarketDataProvider
from autotrader_app.models import OrderRequest, OrderSide
from autotrader_app.strategies.base import StrategyContext, StrategySignal
from autotrader_app.strategies.double_ma_strategy import DoubleMA_Strategy


class TradingService:
    """交易服务，负责串联数据、策略和模拟下单。"""

    def __init__(self, provider: MarketDataProvider | None = None, broker: BrokerBase | None = None) -> None:
        self.provider = provider or DataProviderFactory.create()
        self.broker = broker or MockBroker()
        self.strategy = DoubleMA_Strategy(symbol_list=[], position_ratio=0.2)

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

    def submit_manual_order(self, symbol: str, side: str, quantity: int, price: float):
        order = OrderRequest(
            symbol=symbol,
            side=OrderSide(side.upper()),
            quantity=quantity,
            price=price,
            strategy_name="manual",
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
