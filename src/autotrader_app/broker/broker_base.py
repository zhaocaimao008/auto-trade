from __future__ import annotations

from abc import ABC, abstractmethod

from autotrader_app.models import FillResult, OrderRequest, OrderResult


class BrokerBase(ABC):
    """交易柜台抽象接口。"""

    @abstractmethod
    def place_order(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError
