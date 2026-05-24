from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from loguru import logger

from autotrader_app.config import get_settings


try:
    import akshare as ak
except Exception:
    ak = None

try:
    import tushare as ts
except Exception:
    ts = None

try:
    import requests
except Exception:
    requests = None


@dataclass(slots=True)
class DataRequest:
    symbol: str
    start: datetime
    end: datetime
    frequency: str = "D"


class MarketDataProvider(ABC):
    """行情数据源抽象。"""

    name: str = "base"

    @abstractmethod
    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def search_symbols(self, keyword: str) -> pd.DataFrame:
        raise NotImplementedError


# ────────────────────────────────────────────────────────────
# 模拟数据生成器（最终 fallback）
# ────────────────────────────────────────────────────────────

def _generate_mock_bars(symbol: str, days: int = 120) -> pd.DataFrame:
    """生成模拟 K 线数据（所有数据源不可用时使用）。"""
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.5))
    dates = pd.date_range(start=start, end=end, freq="B")
    if len(dates) < 30:
        dates = pd.date_range(end=end, periods=60, freq="D")

    np.random.seed(abs(hash(symbol)) % (2**31))
    base = 10.0 + (abs(hash(symbol)) % 200) / 10
    closes = base + np.cumsum(np.random.randn(len(dates)) * 0.15)

    df = pd.DataFrame({
        "datetime": dates[:len(closes)], "symbol": symbol,
        "open": closes * (1 + np.random.randn(len(closes)) * 0.003),
        "high": closes * (1 + np.abs(np.random.randn(len(closes)) * 0.008)),
        "low": closes * (1 - np.abs(np.random.randn(len(closes)) * 0.008)),
        "close": closes,
        "volume": np.random.randint(100000, 5000000, len(closes)),
    })
    logger.info("生成模拟行情: {} {} bars", symbol, len(df))
    return df


# ────────────────────────────────────────────────────────────
# 数据源 1：AKShare（免费，无需 Token）
# ────────────────────────────────────────────────────────────

class AkshareProvider(MarketDataProvider):
    name = "akshare"

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        if ak is None:
            raise RuntimeError("AKShare 未安装")
        try:
            df = ak.stock_zh_a_hist(
                symbol=request.symbol, period="daily",
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"), adjust="qfq",
            )
            if df is not None and not df.empty:
                return self._normalize(df, request.symbol)
        except Exception as exc:
            logger.warning("AKShare 失败: {}", exc)
        raise ConnectionError("AKShare 不可用")

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        if ak is None:
            return pd.DataFrame()
        try:
            all_ = ak.stock_info_a_code_name()
            if keyword:
                mask = all_["code"].astype(str).str.contains(keyword) | all_["name"].astype(str).str.contains(keyword)
                return all_.loc[mask].reset_index(drop=True)
            return all_.head(50)
        except Exception:
            return pd.DataFrame()

    @staticmethod
    def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        renamed = df.rename(columns={"日期": "datetime", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"})
        renamed["datetime"] = pd.to_datetime(renamed["datetime"])
        renamed["symbol"] = symbol
        return renamed[["datetime", "symbol", "open", "high", "low", "close", "volume"]].sort_values("datetime")


# ────────────────────────────────────────────────────────────
# 数据源 2：新浪财经 API（免费，无需 Token）
# ────────────────────────────────────────────────────────────

class SinaProvider(MarketDataProvider):
    """新浪财经免费行情接口。"""

    name = "sina"

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        if requests is None:
            raise RuntimeError("requests 未安装")
        try:
            url = "https://quotes.money.163.com/service/chddata.html"
            code = f"0{request.symbol}" if request.symbol.startswith(("6", "9")) else f"1{request.symbol}"
            params = {"code": code, "start": request.start.strftime("%Y%m%d"), "end": request.end.strftime("%Y%m%d")}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200 and len(resp.text) > 100:
                import io
                df = pd.read_csv(io.StringIO(resp.text), encoding="gbk", skipfooter=0, engine="python")
                df = df.rename(columns={"日期": "datetime", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume"})
                df["datetime"] = pd.to_datetime(df["datetime"])
                df["symbol"] = request.symbol
                df = df.sort_values("datetime")
                return df[["datetime", "symbol", "open", "high", "low", "close", "volume"]]
        except Exception as exc:
            logger.warning("新浪财经失败: {}", exc)
        raise ConnectionError("新浪财经不可用")

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        return pd.DataFrame()


# ────────────────────────────────────────────────────────────
# 数据源 3：东方财富（通过 AKShare 的 EM 接口）
# ────────────────────────────────────────────────────────────

class EastMoneyProvider(MarketDataProvider):
    """东方财富免费行情接口（基于 AKShare 的东财接口）。"""

    name = "eastmoney"

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        if ak is None:
            raise RuntimeError("AKShare 未安装")
        try:
            df = ak.stock_zh_a_hist(
                symbol=request.symbol, period="daily",
                start_date=request.start.strftime("%Y%m%d"),
                end_date=request.end.strftime("%Y%m%d"), adjust="qfq",
            )
            if df is not None and not df.empty:
                return self._normalize(df, request.symbol)
        except Exception as exc:
            logger.warning("东方财富失败: {}", exc)
        raise ConnectionError("东方财富不可用")

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        return pd.DataFrame()

    @staticmethod
    def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        renamed = df.rename(columns={"日期": "datetime", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"})
        renamed["datetime"] = pd.to_datetime(renamed["datetime"])
        renamed["symbol"] = symbol
        return renamed[["datetime", "symbol", "open", "high", "low", "close", "volume"]].sort_values("datetime")


# ────────────────────────────────────────────────────────────
# 数据源 4：Tushare Pro（需要 Token，免费注册可用）
# ────────────────────────────────────────────────────────────

class TushareProvider(MarketDataProvider):
    name = "tushare"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN")
        if ts is None:
            raise RuntimeError("Tushare 未安装")
        ts.set_token(settings.tushare_token)
        self.pro = ts.pro_api()

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        df = self.pro.daily(
            ts_code=request.symbol,
            start_date=request.start.strftime("%Y%m%d"),
            end_date=request.end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            raise ConnectionError("Tushare 返回空数据")
        df = df.rename(columns={"trade_date": "datetime", "vol": "volume"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["symbol"] = request.symbol
        return df[["datetime", "symbol", "open", "high", "low", "close", "volume"]].sort_values("datetime")

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        df = self.pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name")
        if keyword:
            mask = df["ts_code"].str.contains(keyword) | df["name"].str.contains(keyword)
            df = df.loc[mask]
        return df.reset_index(drop=True)


# ────────────────────────────────────────────────────────────
# 工厂：多源自动切换
# ────────────────────────────────────────────────────────────

_PROVIDER_CHAIN: list[type[MarketDataProvider]] = [
    AkshareProvider,
    EastMoneyProvider,
    SinaProvider,
    TushareProvider,
]


class DataProviderFactory:
    """数据源工厂 —— 自动多源切换。

    尝试顺序：
      1. AkshareProvider（免费，无需 Token）
      2. EastMoneyProvider（基于 AKShare 东财接口）
      3. SinaProvider（新浪财经免费 API）
      4. TushareProvider（需 Token，可选）
      5. 模拟数据（最终 fallback）
    """

    @staticmethod
    def create(provider_name: str | None = None) -> MarketDataProvider:
        settings = get_settings()
        selected = (provider_name or settings.default_data_source).lower()
        logger.info("数据源选择: {}", selected)

        # 如果指定了具体数据源，只尝试该数据源 + 模拟 fallback
        provider_map = {
            "akshare": AkshareProvider,
            "eastmoney": EastMoneyProvider,
            "sina": SinaProvider,
            "tushare": TushareProvider,
        }
        if selected in provider_map:
            return _MultiSourceProvider(primary=selected, selected=True)

        return _MultiSourceProvider()

    @staticmethod
    def get_provider_names() -> list[str]:
        return ["akshare", "eastmoney", "sina", "tushare"]


class _MultiSourceProvider(MarketDataProvider):
    """多源自动切换包装器 —— 依序尝试每个数据源，全部失败则返回模拟数据。"""

    name = "multi"

    def __init__(self, primary: str = "", selected: bool = False):
        self._primary = primary
        self._selected = selected

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        # 生成唯一 seed 确保同一股票每次模拟数据一致
        seed = abs(hash(f"{request.symbol}_{request.start.strftime('%Y%m')}")) % (2**31)

        sources: list[tuple[str, type[MarketDataProvider]]] = []

        if self._selected and self._primary:
            sources = [(n, cls) for n, cls in _get_chain() if n == self._primary]
        else:
            sources = _get_chain()

        for name, provider_cls in sources:
            try:
                logger.info("尝试数据源: {}", name)
                instance = _create_provider(provider_cls)
                if instance is None:
                    continue
                df = instance.get_daily_bars(request)
                if df is not None and not df.empty:
                    logger.info("数据源 {} 成功: {} bars", name, len(df))
                    return df
            except Exception as exc:
                logger.warning("数据源 {} 失败: {}", name, exc)
                continue

        logger.warning("所有数据源均不可用，使用模拟数据")
        np.random.seed(seed)
        return _generate_mock_bars(request.symbol)

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        for _, provider_cls in _get_chain():
            try:
                instance = _create_provider(provider_cls)
                if instance is None:
                    continue
                df = instance.search_symbols(keyword)
                if df is not None and not df.empty:
                    return df
            except Exception:
                continue
        return pd.DataFrame()


def _get_chain() -> list[tuple[str, type[MarketDataProvider]]]:
    return [(cls.name, cls) for cls in _PROVIDER_CHAIN]


def _create_provider(cls: type[MarketDataProvider]) -> MarketDataProvider | None:
    """安全创建 provider 实例。"""
    try:
        return cls()
    except Exception as exc:
        logger.debug("创建 {} 失败: {}", cls.__name__, exc)
        return None
