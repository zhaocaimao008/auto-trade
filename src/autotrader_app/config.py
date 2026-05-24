from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    """应用配置。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── 基本 ────────────────────────────────────────────────────
    app_name: str = "A股自动交易系统"
    db_url: str = Field(default="sqlite:///auto_trader.db", alias="DB_URL")

    # ── 数据源 ──────────────────────────────────────────────────
    tushare_token: str = Field(default="", alias="TUSHARE_TOKEN")
    default_data_source: str = Field(default="akshare", alias="DEFAULT_DATA_SOURCE")

    # ── 交易时间 ────────────────────────────────────────────────
    market_close_hour: int = Field(default=15, alias="MARKET_CLOSE_HOUR")
    market_close_minute: int = Field(default=0, alias="MARKET_CLOSE_MINUTE")

    # ── EasyTrader 实盘配置 ─────────────────────────────────────
    broker_type: str = Field(default="mock", alias="BROKER_TYPE")
    """Broker 类型: mock | easytrader"""

    easytrader_broker_type: str = Field(default="ht", alias="EASYTRADER_BROKER_TYPE")
    """券商类型（ht=华泰, yjb=银河, gf=广发, 等）。"""

    easytrader_account: str = Field(default="", alias="EASYTRADER_ACCOUNT")
    """券商账号。"""

    easytrader_password: str = Field(default="", alias="EASYTRADER_PASSWORD")
    """券商密码。"""

    easytrader_exe_path: str = Field(default="", alias="EASYTRADER_EXE_PATH")
    """券商客户端可执行文件路径。"""

    @property
    def is_live_trading(self) -> bool:
        """是否启用实盘交易（BROKER_TYPE=easytrader 且非 mock）。"""
        return self.broker_type.lower() == "easytrader"

    @property
    def sqlite_path(self) -> Path:
        if self.db_url.startswith("sqlite:///"):
            return BASE_DIR / self.db_url.removeprefix("sqlite:///")
        return BASE_DIR / "auto_trader.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def check_config() -> list[str]:
    """检查配置，返回所有警告消息列表（空列表 = 一切正常）。"""
    warnings: list[str] = []
    settings = get_settings()

    if not settings.tushare_token:
        warnings.append("TUSHARE_TOKEN 未配置，Tushare 数据源不可用，使用 AKShare 作为默认数据源。")

    if settings.default_data_source not in ("akshare", "tushare"):
        warnings.append(f"未知数据源 '{settings.default_data_source}'，将使用 AKShare。")

    # EasyTrader 实盘检查
    if settings.is_live_trading:
        if not settings.easytrader_account:
            warnings.append("⚠️ 实盘模式：EASYTRADER_ACCOUNT 未配置！")
        if not settings.easytrader_exe_path:
            warnings.append("⚠️ 实盘模式：EASYTRADER_EXE_PATH 未配置！")
        warnings.append("🔴 实盘模式已启用，请确认已理解风险！")
    else:
        if settings.broker_type not in ("mock", "easytrader"):
            warnings.append(f"未知 BROKER_TYPE '{settings.broker_type}'，将使用 MockBroker。")

    return warnings
