from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

# 北京时间偏移（UTC+8）
_BJT_OFFSET = timezone(timedelta(hours=8))

# A 股交易时间段
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)


def is_trading_day(dt: datetime | None = None) -> bool:
    """判断是否为交易日（当前只排除周末，后续可接入节假日 API）。"""
    dt = dt or datetime.now()
    # 周末排除（0=周一，6=周日）
    if dt.weekday() >= 5:
        return False
    # TODO: 接入法定节假日接口（如 akshare.tool_trade_date_hist_sina）
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
