"""EasyTrader 实盘交易柜台。

通过 easytrader 库连接券商客户端（华泰/银河/广发等），实现自动化交易。

安全设计：
  · 所有凭证从 .env 读取，代码零硬编码
  · 全局 is_live=False 开关，默认真单保护
  · place_order 在非 live 模式仅打印日志不实际下单
  · GUI 中实盘模式有醒目红色警告

依赖（可选）：
  pip install easytrader
  pip install pywinauto       # Windows 客户端自动化依赖
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from autotrader_app.broker.broker_base import BrokerBase
from autotrader_app.models import FillResult, OrderRequest, OrderResult, OrderSide, OrderStatus, OrderType

# ── 实盘日志 ──────────────────────────────────────────────
try:
    from autotrader_app.logging_config import live_logger
    _live_log = live_logger()
except Exception:
    _live_log = logger

# ── Optional import: easytrader ────────────────────────────────────────
try:
    import easytrader
except ImportError:
    easytrader = None  # type: ignore[assignment]


class EasyTraderBroker(BrokerBase):
    """EasyTrader 实盘交易柜台。

    通过 easytrader 连接已登录的券商客户端进行自动交易。
    支持华泰（ht）、银河（yjb）、广发（gf）等主流券商。

    使用方式：
        1. 手动登录券商客户端
        2. 创建 broker 实例并调用 login()
        3. 调用 place_order / get_positions 等进行交易

    Attributes:
        is_live:        全局实盘开关。True=真单模式，False=日志模拟（默认）。
        _user:          实际 easytrader 连接实例（login 后赋值）。
        _broker_type:   券商类型（ht/yjb/gf 等）。
    """

    name: str = "easytrader"

    def __init__(self, is_live: bool = False) -> None:
        if easytrader is None:
            logger.warning("easytrader 未安装，实盘功能不可用。请执行: pip install easytrader")
        self.is_live: bool = is_live
        self._user: Any = None
        self._broker_type: str = ""
        # 记录本地委托（供 get_orders 返回）
        self._local_orders: dict[str, OrderResult] = {}

    # ────────────────────────────────────────────────────────────────
    # 登录 / 连接
    # ────────────────────────────────────────────────────────────────

    def login(
        self,
        account: str = "",
        password: str = "",
        exe_path: str = "",
        broker_type: str = "ht",
    ) -> bool:
        """连接已打开的券商客户端。

        支持从参数或环境变量读取凭证（优先使用参数）。

        Args:
            account:     账号（留空从 .env 读取 EASYTRADER_ACCOUNT）。
            password:    密码（留空从 .env 读取 EASYTRADER_PASSWORD）。
            exe_path:    客户端可执行文件路径（留空从 .env 读取 EASYTRADER_EXE_PATH）。
            broker_type: 券商类型，ht=华泰, yjb=银河, gf=广发, 等。

        Returns:
            True 连接成功，False 失败。

        Raises:
            RuntimeError: easytrader 未安装。
        """
        if easytrader is None:
            raise RuntimeError("easytrader 未安装，请先 pip install easytrader")

        # 从环境变量读取缺省值
        from autotrader_app.config import get_settings

        s = get_settings()
        account = account or s.easytrader_account
        password = password or s.easytrader_password
        exe_path = exe_path or s.easytrader_exe_path
        broker_type = broker_type or s.easytrader_broker_type or "ht"

        if not account or not exe_path:
            logger.error("EasyTrader 登录失败：账号({})和客户端路径({})不能为空", bool(account), bool(exe_path))
            return False

        self._broker_type = broker_type
        try:
            self._user = easytrader.use(broker_type)
            self._user.connect(exe_path)  # type: ignore[attr-defined]
            logger.info("EasyTrader 客户端连接成功: broker={} account={}", broker_type, account)
            _live_log.info("[登录] 客户端连接成功 broker={}", broker_type)
            if self.is_live:
                _live_log.warning("[风险] 实盘模式已启用，所有操作将发送真实委托")
            return True
        except Exception as exc:
            logger.error("EasyTrader 登录失败: {}", exc)
            self._user = None
            return False

    @property
    def is_connected(self) -> bool:
        """客户端是否已连接。"""
        return self._user is not None

    # ────────────────────────────────────────────────────────────────
    # 下单（实盘保护）
    # ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        order: OrderRequest,
    ) -> tuple[OrderResult, list[FillResult]]:
        """执行交易委托。

        内部根据 self.is_live 决定是否向券商客户端发送真单：
          · True  → 调用 easytrader 实际下单
          · False → 仅记录日志，返回模拟成功结果

        Args:
            order: OrderRequest 对象（含 symbol/side/quantity/price/order_type）。

        Returns:
            (OrderResult, [FillResult]) 元组。
        """
        if not self._check_live_ready():
            return self._mock_result(order, OrderStatus.REJECTED, "未连接客户端")

        order_id = str(uuid.uuid4())

        if not self.is_live:
            logger.info("[模拟] EasyTrader 下单: {} {} x{} @ {}", order.side.value, order.symbol, order.quantity, order.price)
            result = OrderResult(
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                order_type=order.order_type,
                status=OrderStatus.FILLED,
                reason="模拟确认（is_live=False）",
                strategy_name=order.strategy_name,
            )
            fill = FillResult(
                fill_id=str(uuid.uuid4()),
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                filled_at=datetime.now(),
            )
            self._local_orders[order_id] = result
            return result, [fill]

        return self._place_live(order, order_id)

    def _check_live_ready(self) -> bool:
        """检查是否可进行交易操作。"""
        if not self.is_connected:
            logger.warning("EasyTrader 未连接，请先调用 login()")
            return False
        return True

    def _place_live(self, order: OrderRequest, order_id: str) -> tuple[OrderResult, list[FillResult]]:
        """实际通过 easytrader 下单（实盘模式）。"""
        if self._user is None:
            return self._mock_result(order, OrderStatus.REJECTED, "客户端未连接")

        try:
            # easytrader 下单参数：股票代码、价格、数量、方向
            side_map = {OrderSide.BUY: "buy", OrderSide.SELL: "sell"}
            cmd_side = side_map.get(order.side, "buy")

            # 市价单 vs 限价单
            if order.order_type == OrderType.MARKET:
                # 市价单：price 传 0，easytrader 自动处理
                result = self._user.buy(symbol=order.symbol, price=0, amount=order.quantity)
            else:
                result = getattr(self._user, cmd_side)(
                    symbol=order.symbol,
                    price=order.price,
                    amount=order.quantity,
                )

            logger.info("[实盘] EasyTrader 下单成功: {} {} x{} @ {} → {}", cmd_side, order.symbol, order.quantity, order.price, result)
            _live_log.info("[下单] {} {} x{} @ {}", cmd_side, order.symbol, order.quantity, order.price)

            # 解析 easytrader 返回结果
            order_result = OrderResult(
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                order_type=order.order_type,
                status=OrderStatus.FILLED,
                reason="实盘成交",
                strategy_name=order.strategy_name,
            )

            fill = FillResult(
                fill_id=str(uuid.uuid4()),
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=order.price,
                filled_at=datetime.now(),
            )
            self._local_orders[order_id] = order_result
            return order_result, [fill]

        except Exception as exc:
            logger.error("[实盘] EasyTrader 下单失败: {}", exc)
            return self._mock_result(order, OrderStatus.REJECTED, f"下单异常：{exc}")

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """兼容接口。"""
        result, _ = self.place_order(order)
        return result

    # ────────────────────────────────────────────────────────────────
    # 撤单
    # ────────────────────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        """撤销委托。

        本地记录的订单直接标记取消；
        实盘中调用 easytrader 撤单接口。
        """
        if order_id in self._local_orders:
            self._local_orders[order_id].status = OrderStatus.CANCELLED
            return True

        if not self._check_live_ready():
            return False

        try:
            assert self._user is not None
            self._user.cancel_order(order_id)  # type: ignore[attr-defined]
            logger.info("[实盘] 撤单成功: {}", order_id)
            return True
        except Exception as exc:
            logger.error("[实盘] 撤单失败 {}: {}", order_id, exc)
            return False

    # ────────────────────────────────────────────────────────────────
    # 查询持仓
    # ────────────────────────────────────────────────────────────────

    def get_positions(self) -> pd.DataFrame:
        """获取当前持仓。

        Returns:
            DataFrame 列：symbol, quantity, avg_price, current_price, market_value, pnl_pct
            未连接或查询失败时返回空 DataFrame。
        """
        if not self._check_live_ready():
            return pd.DataFrame(columns=["symbol", "quantity", "avg_price"])

        try:
            assert self._user is not None
            raw = self._user.position  # type: ignore[attr-defined]
            if raw is None or (isinstance(raw, (list, pd.DataFrame)) and len(raw) == 0):
                return pd.DataFrame(columns=["symbol", "quantity", "avg_price"])

            df = pd.DataFrame(raw)
            # 统一列名：easytrader 返回的字段名因券商而异，做兼容映射
            col_map = {
                "stock_code": "symbol",
                "stock_name": "name",
                "current_amount": "quantity",
                "cost_price": "avg_price",
                "current_price": "current_price",
                "market_value": "market_value",
                "profit_ratio": "pnl_pct",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            return df[["symbol", "quantity", "avg_price"]].copy()
        except Exception as exc:
            logger.error("查询持仓失败: {}", exc)
            return pd.DataFrame(columns=["symbol", "quantity", "avg_price"])

    # ────────────────────────────────────────────────────────────────
    # 查询账户
    # ────────────────────────────────────────────────────────────────

    def get_account(self) -> dict[str, float]:
        """获取账户资金信息。

        Returns:
            dict 含字段：cash（可用资金）, market_value（持仓市值）, total_assets（总资产）。
            未连接或查询失败时返回全 0。
        """
        if not self._check_live_ready():
            return {"cash": 0.0, "market_value": 0.0, "total_assets": 0.0}

        try:
            assert self._user is not None
            raw = self._user.balance  # type: ignore[attr-defined]
            if raw is None:
                return {"cash": 0.0, "market_value": 0.0, "total_assets": 0.0}

            df = pd.DataFrame([raw]) if isinstance(raw, dict) else pd.DataFrame(raw)
            col_map = {
                "available": "cash",
                "available_balance": "cash",
                "market_value": "market_value",
                "asset_balance": "total_assets",
                "total_assets": "total_assets",
            }
            result = {"cash": 0.0, "market_value": 0.0, "total_assets": 0.0}
            for src_key, dst_key in col_map.items():
                if src_key in df.columns:
                    val = float(df[src_key].iloc[0])
                    if val:
                        result[dst_key] = val
            return result
        except Exception as exc:
            logger.error("查询账户失败: {}", exc)
            return {"cash": 0.0, "market_value": 0.0, "total_assets": 0.0}

    # ────────────────────────────────────────────────────────────────
    # 查询成交 / 委托
    # ────────────────────────────────────────────────────────────────

    def get_fills(self) -> pd.DataFrame:
        """获取当日成交记录。

        Returns:
            DataFrame 列：fill_id, order_id, symbol, side, quantity, price, filled_at
        """
        if not self._check_live_ready():
            return pd.DataFrame(columns=["fill_id", "order_id", "symbol", "side", "quantity", "price", "filled_at"])

        try:
            assert self._user is not None
            raw = self._user.deal  # type: ignore[attr-defined]
            if raw is None or (isinstance(raw, (list, pd.DataFrame)) and len(raw) == 0):
                # 返回本地记录
                return self._local_fills_df()

            df = pd.DataFrame(raw)
            col_map = {
                "stock_code": "symbol",
                "deal_amount": "quantity",
                "deal_price": "price",
                "business_time": "filled_at",
                "business_balance": "amount",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            return df[["symbol", "quantity", "price"]].copy()
        except Exception as exc:
            logger.error("查询成交失败: {}", exc)
            return self._local_fills_df()

    def get_orders(self) -> pd.DataFrame:
        """获取当日委托记录。"""
        if not self._check_live_ready():
            return pd.DataFrame(columns=["order_id", "symbol", "side", "quantity", "price", "status"])

        try:
            assert self._user is not None
            raw = self._user.entrust  # type: ignore[attr-defined]
            if raw is None:
                return self._local_orders_df()
            df = pd.DataFrame(raw)
            return df
        except Exception:
            return self._local_orders_df()

    # ────────────────────────────────────────────────────────────────
    # 行情价格
    # ────────────────────────────────────────────────────────────────

    def get_latest_price(self, symbol: str) -> float | None:
        """获取股票最新价。

        优先使用行情接口；若不可用则从持仓数据提取（仅限已持仓股票）。
        """
        if not self._check_live_ready():
            return None
        try:
            assert self._user is not None
            try:
                quote = self._user.get_security_quotes(symbol)
                if quote is not None:
                    if isinstance(quote, list) and quote:
                        row = quote[0]
                        for col in ("price", "current_price"):
                            if col in row:
                                return float(row[col])
                    if isinstance(quote, dict):
                        for col in ("price", "current_price"):
                            if col in quote:
                                return float(quote[col])
            except (AttributeError, Exception):
                pass
            raw = self._user.position
            if raw is not None:
                df = pd.DataFrame(raw)
                row = df[df.get("stock_code", df.get("symbol")) == symbol]
                if not row.empty:
                    for col in ("current_price", "price", "cost_price"):
                        if col in row.columns:
                            return float(row[col].iloc[0])
        except Exception:
            pass
        return None

    # ────────────────────────────────────────────────────────────────
    # 内部工具
    # ────────────────────────────────────────────────────────────────

    def _mock_result(
        self,
        order: OrderRequest,
        status: OrderStatus,
        reason: str,
    ) -> tuple[OrderResult, list[FillResult]]:
        order_id = str(uuid.uuid4())
        result = OrderResult(
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type,
            status=status,
            reason=reason,
            strategy_name=order.strategy_name,
        )
        self._local_orders[order_id] = result
        return result, []

    def _local_orders_df(self) -> pd.DataFrame:
        rows = [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side.value,
                "quantity": o.quantity,
                "price": o.price,
                "status": o.status.value,
            }
            for o in self._local_orders.values()
        ]
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["order_id", "symbol", "side", "quantity", "price", "status"])

    def _local_fills_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["fill_id", "order_id", "symbol", "side", "quantity", "price", "filled_at"])

    # ────────────────────────────────────────────────────────────────
    # 兼容接口：get_positions 的 BrokerBase 抽象已覆盖
    # ────────────────────────────────────────────────────────────────

    # _place_impl — BrokerBase 要求子类实现，但 EasyTrader 直接重写 place_order
    def _place_impl(self, order: OrderRequest) -> tuple[OrderResult, list[FillResult]]:
        return self.place_order(order)
