from __future__ import annotations

import logging

import pandas as pd

from datetime import date, datetime, time, timedelta, timezone

_logger = logging.getLogger(__name__)

# 北京时间偏移（UTC+8）
_BJT_OFFSET = timezone(timedelta(hours=8))

# A 股交易时间段
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)

# 交易日缓存（避免每次判断都调接口）
_trade_date_cache: set[date] | None = None
_cache_date: date | None = None


def _load_trade_calendar() -> set[date]:
    """从 AKShare 加载交易日历并缓存。

    仅在需要时调用，全年数据缓存到内存中。
    若 AKShare 不可用，回退到"排除周末"的简单规则。
    """
    global _trade_date_cache, _cache_date
    today = datetime.now().astimezone(_BJT_OFFSET).date()

    # 缓存有效（当天已加载）
    if _trade_date_cache is not None and _cache_date == today:
        return _trade_date_cache

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            raise ValueError("empty trade calendar")

        # 统一列名（大小写兼容）
        df.columns = [c.lower() for c in df.columns]
        # 只保留交易日
        df = df[df["is_open"] == 1]
        # trade_date 转为 date 对象
        dates: set[date] = set()
        for val in df["trade_date"]:
            d = pd.to_datetime(val).date()
            dates.add(d)

        _trade_date_cache = dates
        _cache_date = today
        _logger.info("Loaded %d trade dates from AKShare", len(dates))
    except Exception as exc:
        _logger.warning("Failed to load trade calendar from AKShare: %s", exc)
        _trade_date_cache = None

    return _trade_date_cache or set()


def is_trading_day(dt: datetime | None = None) -> bool:
    """判断是否为 A 股交易日。

    优先使用 AKShare 交易日历（缓存），
    无法获取时排除周末作为简单回退。
    """
    dt = dt or datetime.now()
    bjt = dt.astimezone(_BJT_OFFSET)
    d = bjt.date()

    # 周末排除（0=周一，6=周日）
    if d.weekday() >= 5:
        return False

    try:
        trade_dates = _load_trade_calendar()
        if trade_dates:
            return d in trade_dates
    except Exception:
        pass

    return True


def is_trading_time(dt: datetime | None = None) -> bool:
    """判断当前时间是否在 A 股交易时段内（北京时间）。"""
    dt = dt or datetime.now()
    if not is_trading_day(dt):
        return False
    bjt = dt.astimezone(_BJT_OFFSET)
    t = bjt.time()
    return (MORNING_START <= t <= MORNING_END) or (AFTERNOON_START <= t <= AFTERNOON_END)


def next_trading_segment_start(dt: datetime | None = None) -> str | None:
    """返回下一个交易时段的开始时间描述，用于状态栏显示。"""
    dt = dt or datetime.now()
    bjt = dt.astimezone(_BJT_OFFSET)
    t = bjt.time()

    if t < MORNING_START:
        return "09:30 开盘"
    if MORNING_END < t < AFTERNOON_START:
        return "13:00 午盘"
    if t > AFTERNOON_END:
        # 下一个交易日
        next_day = bjt + timedelta(days=1)
        while not is_trading_day(next_day):
            next_day += timedelta(days=1)
        return next_day.strftime("%m-%d 09:30")
    return None  # 正在交易中
