from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from autotrader_app.models import FillResult, OrderRequest, OrderResult


class BrokerBase(ABC):
    """交易柜台抽象接口。

    - 当前支持模拟/Paper 模式
    - 预留真实下单接口（place_order 不由子类重写，子类实现 _place_impl）
    - 子类只需实现 _place_impl、cancel_order、get_positions、get_fills
    - 可选实现 get_latest_price
    """

    @abstractmethod
    def _place_impl(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        """子类实现的具体下单逻辑。"""
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_fills(self) -> pd.DataFrame:
        raise NotImplementedError

    # ── 公共接口（子类不重写） ──────────────────────────────

    def place_order(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        """统一下单入口，子类实现 _place_impl 即可。"""
        return self._place_impl(order)

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """兼容旧有的 submit_order 样式调用。"""
        result, _ = self.place_order(order)
        return result

    # ── 可选实现 ───────────────────────────────────────────

    def get_latest_price(self, symbol: str) -> float | None:
        """获取最新行情价格（默认返回 None，子类可覆盖）。"""
        return None
