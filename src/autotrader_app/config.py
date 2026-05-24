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
    """检查配置，返回配置状态报告列表（含正常信息与警告）。"""
    reports: list[str] = []
    settings = get_settings()

    # ── 数据源 ──────────────────────────────────────────
    if settings.default_data_source == "akshare":
        reports.append("数据源: AKShare（默认）")
    elif settings.default_data_source == "tushare":
        if not settings.tushare_token:
            reports.append("⚠️ TUSHARE_TOKEN 未配置，Tushare 不可用，使用 AKShare")
        else:
            reports.append("数据源: Tushare Pro（已配置）")
    else:
        reports.append(f"⚠️ 未知数据源 '{settings.default_data_source}'，使用 AKShare")

    # ── Broker ──────────────────────────────────────────
    reports.append(f"Broker: {settings.broker_type}")

    if settings.broker_type == "easytrader":
        reports.append("🔴 实盘模式已启用")
        if not settings.easytrader_account:
            reports.append("  ❌ EASYTRADER_ACCOUNT 未配置")
        else:
            reports.append(f"  ✅ 账号已配置")
        if not settings.easytrader_exe_path:
            reports.append("  ❌ EASYTRADER_EXE_PATH 未配置")
        else:
            reports.append(f"  ✅ 客户端路径已配置")
        reports.append(f"  券商类型: {settings.easytrader_broker_type or 'ht'}")
    else:
        reports.append("✅ 模拟模式，无资金风险")

    return reports
