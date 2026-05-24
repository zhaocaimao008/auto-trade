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
except Exception:  # pragma: no cover - optional import at runtime
    ak = None

try:
    import tushare as ts
except Exception:  # pragma: no cover - optional import at runtime
    ts = None


@dataclass(slots=True)
class DataRequest:
    symbol: str
    start: datetime
    end: datetime
    frequency: str = "D"


class MarketDataProvider(ABC):
    """行情数据源抽象。"""

    @abstractmethod
    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def search_symbols(self, keyword: str) -> pd.DataFrame:
        raise NotImplementedError


class AkshareProvider(MarketDataProvider):
    """AKShare 数据源（网络不可用时自动降级为模拟数据）。"""

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        if ak is None:
            raise RuntimeError("AKShare 未安装或导入失败。")

        start = request.start.strftime("%Y%m%d")
        end = request.end.strftime("%Y%m%d")
        try:
            df = ak.stock_zh_a_hist(
                symbol=request.symbol, period="daily",
                start_date=start, end_date=end, adjust="qfq",
            )
            if df is not None and not df.empty:
                return self._normalize_akshare(df, request.symbol)
        except Exception as exc:
            logger.warning("AKShare 获取行情失败: {}，使用模拟数据", exc)

        return self._generate_mock_bars(request.symbol, days=120)

    @staticmethod
    def _generate_mock_bars(symbol: str, days: int = 120) -> pd.DataFrame:
        """生成模拟 K 线数据（网络不可用时自动降级）。"""
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

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        if ak is None:
            raise RuntimeError("AKShare 未安装或导入失败。")

        all_symbols = ak.stock_info_a_code_name()
        if keyword:
            mask = all_symbols["code"].astype(str).str.contains(keyword) | all_symbols["name"].astype(str).str.contains(keyword)
            return all_symbols.loc[mask].reset_index(drop=True)
        return all_symbols.head(50)

    @staticmethod
    def _normalize_akshare(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        renamed = df.rename(
            columns={
                "日期": "datetime",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
            }
        )
        renamed["datetime"] = pd.to_datetime(renamed["datetime"])
        renamed["symbol"] = symbol
        return renamed[["datetime", "symbol", "open", "high", "low", "close", "volume"]].sort_values("datetime")


class TushareProvider(MarketDataProvider):
    """Tushare Pro 数据源。"""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN。")
        if ts is None:
            raise RuntimeError("Tushare 未安装或导入失败。")
        ts.set_token(settings.tushare_token)
        self.pro = ts.pro_api()

    def get_daily_bars(self, request: DataRequest) -> pd.DataFrame:
        ts_code = request.symbol
        df = self.pro.daily(
            ts_code=ts_code,
            start_date=request.start.strftime("%Y%m%d"),
            end_date=request.end.strftime("%Y%m%d"),
        )
        if df.empty:
            return df

        df = df.rename(columns={"trade_date": "datetime", "vol": "volume"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["symbol"] = ts_code
        return df[["datetime", "symbol", "open", "high", "low", "close", "volume"]].sort_values("datetime")

    def search_symbols(self, keyword: str) -> pd.DataFrame:
        df = self.pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,area,industry,list_date")
        if keyword:
            mask = df["ts_code"].astype(str).str.contains(keyword) | df["name"].astype(str).str.contains(keyword)
            df = df.loc[mask]
        return df.reset_index(drop=True)


class DataProviderFactory:
    """数据源选择器。"""

    @staticmethod
    def create(provider_name: str | None = None) -> MarketDataProvider:
        settings = get_settings()
        selected = (provider_name or settings.default_data_source).lower()
        logger.info("Using market data provider: {}", selected)
        if selected == "tushare":
            return TushareProvider()
        return AkshareProvider()
