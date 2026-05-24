from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from loguru import logger

from autotrader_app.database import get_session
from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.models import FillResult, OrderRequest, OrderResult, OrderSide, OrderStatus
from autotrader_app.repositories import AccountRepository, FillRepository, OrderRepository, PositionRepository


@dataclass(slots=True)
class AccountState:
    cash: float = 100_000.0
    market_value: float = 0.0

    @property
    def total_assets(self) -> float:
        return self.cash + self.market_value


class MockBroker(BrokerBase):
    """模拟交易柜台，继承 BrokerBase 统一接口。"""

    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self.account = AccountState(cash=initial_cash)

    def _place_impl(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        with get_session() as session:
            positions = PositionRepository(session)
            orders = OrderRepository(session)
            fills = FillRepository(session)
            accounts = AccountRepository(session)

            result, fill_records = self._fill_order(order, positions)
            orders.add(result)
            for fr in fill_records:
                fills.add(fr)
            self._refresh_market_value(positions)
            accounts.add_snapshot(self.account.cash, self.account.market_value)

            logger.info(
                "Mock order filled: {} {} x{} @ {} ({} fill records)",
                result.side.value,
                result.symbol,
                result.quantity,
                result.price,
                len(fill_records),
            )
            return result, fill_records

    def get_positions(self) -> pd.DataFrame:
        with get_session() as session:
            repo = PositionRepository(session)
            rows = repo.list_all()
            return pd.DataFrame(
                [{"symbol": row.symbol, "quantity": row.quantity, "avg_price": row.avg_price} for row in rows]
            )

    def cancel_order(self, order_id: str) -> bool:
        """取消委托（Mock 下直接返回 False 表示已成交不可撤）。"""
        with get_session() as session:
            repo = OrderRepository(session)
            order = repo.get_by_order_id(order_id)
            if order is None or order.status not in (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED):
                return False
            order.status = OrderStatus.CANCELLED
            repo.add_or_update(order)
            return True

    def get_positions(self) -> pd.DataFrame:
        with get_session() as session:
            repo = PositionRepository(session)
            rows = repo.list_all()
            return pd.DataFrame(
                [{"symbol": row.symbol, "quantity": row.quantity, "avg_price": row.avg_price} for row in rows]
            )

    def get_fills(self) -> pd.DataFrame:
        """获取所有成交记录，按时间倒序。"""
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

    def _fill_order(self, order: OrderRequest, repo: PositionRepository) -> tuple[OrderResult, list[FillResult]]:
        order_id = str(uuid.uuid4())
        position = repo.get_by_symbol(order.symbol)

        if order.side == OrderSide.BUY:
            cost = order.quantity * order.price
            if cost > self.account.cash:
                return OrderResult(
                    order_id=order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.price,
                    order_type=order.order_type,
                    status=OrderStatus.REJECTED,
                    reason="资金不足",
                ), []

            self.account.cash -= cost
            if position is None:
                repo.upsert(order.symbol, order.quantity, order.price)
            else:
                total_qty = position.quantity + order.quantity
                new_avg = ((position.quantity * position.avg_price) + cost) / total_qty
                repo.upsert(order.symbol, total_qty, new_avg)

        else:
            if position is None or position.quantity < order.quantity:
                return OrderResult(
                    order_id=order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.price,
                    order_type=order.order_type,
                    status=OrderStatus.REJECTED,
                    reason="持仓不足",
                ), []

            income = order.quantity * order.price
            self.account.cash += income
            remaining = position.quantity - order.quantity
            if remaining <= 0:
                repo.delete(position)
            else:
                repo.upsert(order.symbol, remaining, position.avg_price)

        fills: list[FillResult] = [
            FillResult(
                fill_id=str(uuid.uuid4()),
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                filled_at=datetime.now(),
            )
        ]

        return OrderResult(
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type,
            status=OrderStatus.FILLED,
        ), fills

    def get_equity_history(self, limit: int = 2000) -> pd.DataFrame:
        """返回账户净值历史，用于绘制权益曲线。

        列：created_at, cash, market_value, total_assets
        始终在末尾追加一条"当前实时"记录，确保曲线延伸至当下。
        """
        # 在 session 内直接转为字典，避免 session 关闭后 DetachedInstanceError
        with get_session() as session:
            rows = AccountRepository(session).list_all(limit=limit)
            records = [
                {
                    "created_at": row.created_at,
                    "cash": row.cash,
                    "market_value": row.market_value,
                    "total_assets": row.total_assets,
                }
                for row in rows
            ]

        if not records:
            # 无历史快照时返回空 DataFrame（GUI 侧显示占位提示）
            return pd.DataFrame(columns=["created_at", "cash", "market_value", "total_assets"])

        df = pd.DataFrame(records)

        # 追加实时当前状态（不入库，仅供展示）
        realtime = {
            "created_at": datetime.now(),
            "cash": self.account.cash,
            "market_value": self.account.market_value,
            "total_assets": self.account.total_assets,
        }
        df = pd.concat([df, pd.DataFrame([realtime])], ignore_index=True)
        return df

    def _refresh_market_value(self, repo: PositionRepository) -> None:
        total = 0.0
        for position in repo.list_all():
            total += position.quantity * position.avg_price
        self.account.market_value = total
