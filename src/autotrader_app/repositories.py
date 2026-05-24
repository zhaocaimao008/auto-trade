from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from autotrader_app.database import AccountSnapshot, FillRecord, OrderRecord, PositionRecord
from autotrader_app.models import FillResult, OrderResult


class PositionRepository:
    """仓位持久化。"""

    def __init__(self, session):
        self.session = session

    def list_all(self) -> list[PositionRecord]:
        stmt = select(PositionRecord).order_by(PositionRecord.symbol)
        return list(self.session.scalars(stmt))

    def get_by_symbol(self, symbol: str) -> PositionRecord | None:
        stmt = select(PositionRecord).where(PositionRecord.symbol == symbol)
        return self.session.scalar(stmt)

    def upsert(self, symbol: str, quantity: int, avg_price: float) -> PositionRecord:
        position = self.get_by_symbol(symbol)
        if position is None:
            position = PositionRecord(symbol=symbol, quantity=quantity, avg_price=avg_price)
            self.session.add(position)
        else:
            position.quantity = quantity
            position.avg_price = avg_price
            position.updated_at = datetime.now()
        return position

    def delete(self, position: PositionRecord) -> None:
        self.session.delete(position)


class OrderRepository:
    """订单持久化。"""

    def __init__(self, session):
        self.session = session

    def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        stmt = select(OrderRecord).where(OrderRecord.order_id == order_id)
        return self.session.scalar(stmt)

    def add(self, order: OrderResult) -> OrderRecord:
        record = OrderRecord(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side.value,
            order_type=order.order_type.value,
            quantity=order.quantity,
            price=order.price,
            status=order.status.value,
            strategy_name=getattr(order, "strategy_name", "manual"),
            reason=order.reason,
            created_at=order.created_at,
        )
        self.session.add(record)
        return record

    def add_or_update(self, order: OrderResult) -> OrderRecord:
        stmt = select(OrderRecord).where(OrderRecord.order_id == order.order_id)
        record = self.session.scalar(stmt)
        if record is None:
            return self.add(order)

        record.symbol = order.symbol
        record.side = order.side.value
        record.order_type = order.order_type.value
        record.quantity = order.quantity
        record.price = order.price
        record.status = order.status.value
        record.reason = order.reason
        return record

    def latest(self, limit: int = 50) -> list[OrderRecord]:
        stmt = select(OrderRecord).order_by(OrderRecord.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))


class FillRepository:
    """成交持久化。"""

    def __init__(self, session):
        self.session = session

    def add(self, fill: FillResult) -> FillRecord:
        record = FillRecord(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side.value,
            quantity=fill.quantity,
            price=fill.price,
            filled_at=fill.filled_at,
        )
        self.session.add(record)
        return record

    def latest(self, limit: int = 50) -> list[FillRecord]:
        stmt = select(FillRecord).order_by(FillRecord.filled_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))


class AccountRepository:
    """账户快照持久化。"""

    def __init__(self, session):
        self.session = session

    def add_snapshot(self, cash: float, market_value: float) -> AccountSnapshot:
        snapshot = AccountSnapshot(
            cash=cash,
            market_value=market_value,
            total_assets=cash + market_value,
        )
        self.session.add(snapshot)
        return snapshot

    def latest(self) -> AccountSnapshot | None:
        stmt = select(AccountSnapshot).order_by(AccountSnapshot.created_at.desc()).limit(1)
        return self.session.scalar(stmt)

    def list_all(self, limit: int = 2000) -> list[AccountSnapshot]:
        """按时间升序返回全部快照（用于绘制权益曲线）。"""
        stmt = (
            select(AccountSnapshot)
            .order_by(AccountSnapshot.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def list_since(self, since: datetime, limit: int = 2000) -> list[AccountSnapshot]:
        """返回 since 之后的快照（按时间升序）。"""
        stmt = (
            select(AccountSnapshot)
            .where(AccountSnapshot.created_at >= since)
            .order_by(AccountSnapshot.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))
