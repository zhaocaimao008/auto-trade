from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


@dataclass(slots=True)
class MarketBar:
    symbol: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    price: float = 0.0
    order_type: OrderType = OrderType.MARKET
    strategy_name: str = "manual"


@dataclass(slots=True)
class OrderResult:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    order_type: OrderType
    status: OrderStatus
    created_at: datetime = field(default_factory=datetime.now)
    reason: str = ""


@dataclass(slots=True)
class FillResult:
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    filled_at: datetime = field(default_factory=datetime.now)
