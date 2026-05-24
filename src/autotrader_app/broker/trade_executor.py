from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from loguru import logger

from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.database import get_session
from autotrader_app.models import (
    ExecutionMode,
    FillResult,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from autotrader_app.repositories import AccountRepository, FillRepository, OrderRepository, PositionRepository


@dataclass(slots=True)
class AccountState:
    cash: float = 100_000.0
    market_value: float = 0.0

    @property
    def total_assets(self) -> float:
        return self.cash + self.market_value


class TradeExecutor(BrokerBase):
    """统一交易执行器。

    - 当前支持模拟交易
    - 预留真实下单接口
    - 记录委托、成交、持仓
    - 支持市价单、限价单
    """

    def __init__(self, mode: ExecutionMode = ExecutionMode.PAPER, initial_cash: float = 100_000.0) -> None:
        self.mode = mode
        self.account = AccountState(cash=initial_cash)
        self.open_orders: dict[str, OrderResult] = {}

    def place_order(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        if self.mode == ExecutionMode.LIVE:
            return self._place_live_order(order)
        return self._place_paper_order(order)

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """兼容旧调用方式。"""

        result, _ = self.place_order(order)
        return result

    def cancel_order(self, order_id: str) -> bool:
        existing = self.open_orders.get(order_id)
        if existing is None or existing.status != OrderStatus.PENDING:
            return False

        existing.status = OrderStatus.CANCELLED
        with get_session() as session:
            OrderRepository(session).add_or_update(existing)
        self.open_orders.pop(order_id, None)
        return True

    def get_positions(self) -> pd.DataFrame:
        with get_session() as session:
            rows = PositionRepository(session).list_all()
            return pd.DataFrame(
                [{"symbol": row.symbol, "quantity": row.quantity, "avg_price": row.avg_price} for row in rows]
            )

    def get_orders(self) -> pd.DataFrame:
        with get_session() as session:
            rows = OrderRepository(session).latest(limit=200)
            return pd.DataFrame(
                [
                    {
                        "order_id": row.order_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "order_type": row.order_type,
                        "quantity": row.quantity,
                        "price": row.price,
                        "status": row.status,
                        "strategy_name": row.strategy_name,
                        "reason": row.reason,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ]
            )

    def get_fills(self) -> pd.DataFrame:
        with get_session() as session:
            rows = FillRepository(session).latest(limit=200)
            return pd.DataFrame(
                [
                    {
                        "fill_id": row.fill_id,
                        "order_id": row.order_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "quantity": row.quantity,
                        "price": row.price,
                        "filled_at": row.filled_at,
                    }
                    for row in rows
                ]
            )

    def _place_paper_order(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        order_result = OrderResult(
            order_id=str(uuid.uuid4()),
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type,
            status=OrderStatus.PENDING,
            reason="等待撮合",
        )
        self.open_orders[order_result.order_id] = order_result

        with get_session() as session:
            positions = PositionRepository(session)
            orders = OrderRepository(session)
            fills = FillRepository(session)
            accounts = AccountRepository(session)

            fill_price = self._resolve_fill_price(order)
            valid, reason = self._validate_order(order, fill_price, positions)
            if not valid:
                order_result.price = fill_price
                order_result.status = OrderStatus.REJECTED
                order_result.reason = reason
                orders.add(order_result)
                self.open_orders.pop(order_result.order_id, None)
                return order_result, []

            order_result.price = fill_price
            generated_fills = self._fill_order(order_result, positions)
            order_result.status = OrderStatus.FILLED
            order_result.reason = "撮合成功"
            orders.add(order_result)

            for fill in generated_fills:
                fills.add(fill)

            self._refresh_market_value(positions)
            accounts.add_snapshot(self.account.cash, self.account.market_value)
            self.open_orders.pop(order_result.order_id, None)

        logger.info(
            "Paper order filled: {} {} {} x{} @ {}",
            order_result.order_type.value,
            order_result.side.value,
            order_result.symbol,
            order_result.quantity,
            order_result.price,
        )
        return order_result, generated_fills

    def _place_live_order(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        raise NotImplementedError("真实下单接口尚未实现，请在这里接入券商 API。")

    def _validate_order(self, order: OrderRequest, fill_price: float, repo: PositionRepository) -> tuple[bool, str]:
        if fill_price <= 0:
            return False, "无效价格"
        if order.quantity <= 0 or order.quantity % 100 != 0:
            return False, "A股委托数量必须按 100 股一手"

        position = repo.get_by_symbol(order.symbol)
        if order.side == OrderSide.BUY:
            if order.quantity * fill_price > self.account.cash:
                return False, "资金不足"
            return True, "OK"

        if position is None or position.quantity < order.quantity:
            return False, "持仓不足"
        return True, "OK"

    def _resolve_fill_price(self, order: OrderRequest) -> float:
        if order.order_type == OrderType.MARKET:
            # 当前模拟环境没有盘口，这里默认使用传入价格作为最新成交价。
            return order.price
        return order.price

    def _fill_order(self, order: OrderResult, repo: PositionRepository) -> list[FillResult]:
        position = repo.get_by_symbol(order.symbol)

        if order.side == OrderSide.BUY:
            cost = order.quantity * order.price
            self.account.cash -= cost
            if position is None:
                repo.upsert(order.symbol, order.quantity, order.price)
            else:
                total_qty = position.quantity + order.quantity
                avg_price = ((position.quantity * position.avg_price) + cost) / total_qty
                repo.upsert(order.symbol, total_qty, avg_price)
        else:
            self.account.cash += order.quantity * order.price
            remaining = position.quantity - order.quantity if position else 0
            if position and remaining <= 0:
                repo.delete(position)
            elif position:
                repo.upsert(order.symbol, remaining, position.avg_price)

        return [
            FillResult(
                fill_id=str(uuid.uuid4()),
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                filled_at=datetime.now(),
            )
        ]

    def _refresh_market_value(self, repo: PositionRepository) -> None:
        total = 0.0
        for position in repo.list_all():
            total += position.quantity * position.avg_price
        self.account.market_value = total

    # ── BrokerBase 抽象方法实现 ────────────────────────────

    def _place_impl(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        return self.place_order(order)
