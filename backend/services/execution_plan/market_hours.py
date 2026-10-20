"""
交易时段判断 — 美股(夏令时/冬令时) + 港股。

用于触发评估循环：非交易时段直接 skip。
"""
from datetime import datetime, timezone, time as dtime


def _is_us_dst(dt_utc: datetime) -> bool:
    """美国夏令时：3月第2个周日 02:00 → 11月第1个周日 02:00 (本地时)。"""
    import calendar
    year = dt_utc.year
    # 3月第2个周日
    mar = calendar.monthrange(year, 3)
    first_sun_mar = (6 - calendar.weekday(year, 3, 1)) % 7 + 1
    dst_start_day = first_sun_mar + 7  # 第2个周日
    dst_start = datetime(year, 3, dst_start_day, 7, 0, tzinfo=timezone.utc)  # 02:00 EST = 07:00 UTC

    # 11月第1个周日
    first_sun_nov = (6 - calendar.weekday(year, 11, 1)) % 7 + 1
    dst_end = datetime(year, 11, first_sun_nov, 6, 0, tzinfo=timezone.utc)  # 02:00 EDT = 06:00 UTC

    return dst_start <= dt_utc < dst_end


def is_market_open(market: str, dt_utc: datetime | None = None) -> bool:
    """判断给定市场当前(或指定时间)是否在交易时段。

    Args:
        market: "US" / "HK"
        dt_utc: UTC 时间，默认当前
    """
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)

    # 周末全球休市
    if dt_utc.weekday() >= 5:
        return False

    t = dt_utc.time()

    if market == "US":
        if _is_us_dst(dt_utc):
            # 夏令时: 09:30-16:00 EDT = 13:30-20:00 UTC
            return dtime(13, 30) <= t <= dtime(20, 0)
        else:
            # 冬令时: 09:30-16:00 EST = 14:30-21:00 UTC
            return dtime(14, 30) <= t <= dtime(21, 0)

    if market == "HK":
        # 09:30-12:00, 13:00-16:00 HKT = 01:30-04:00, 05:00-08:00 UTC
        morning = dtime(1, 30) <= t <= dtime(4, 0)
        afternoon = dtime(5, 0) <= t <= dtime(8, 0)
        return morning or afternoon

    return False


def valuation_quality(value, market, *, at=None, basis=None):
    """Assess a SOURCE price time against local exchange sessions, never fetch time.

    Trading trigger hours above keep their original semantics. This read-only
    assessment uses the maintained session library, including holidays/half-days.
    Unsupported markets/calendar dates fail unknown, never weekday guessing.
    """
    at = at or datetime.now(timezone.utc)
    result = {"status": "unknown", "reason_code": "PRICE_TIME_UNKNOWN", "price_as_of": value}
    if not value:
        return result
    try:
        import exchange_calendars as calendars
        import pandas as pd
        code = {"US":"XNYS", "HK":"XHKG", "SH":"XSHG", "SZ":"XSHG",
                "LSE":"XLON", "LSEETF":"XLON", "UK":"XLON"}.get(market)
        if not code:
            return {**result, "reason_code":"MARKET_CALENDAR_UNKNOWN"}
        calendar = calendars.get_calendar(code)
        current = pd.Timestamp(at)
        if current.tzinfo is None:
            return result
        schedule = calendar.schedule.loc[(current-pd.Timedelta(days=20)).date():current.date()]
        if schedule.empty or current.date() > calendar.last_session.date():
            return {**result, "reason_code":"MARKET_CALENDAR_OUT_OF_RANGE"}
        finished = schedule[schedule["close"] <= current]
        minute = current.floor("min")
        opened = calendar.is_open_on_minute(minute)
        daily = len(value) == 10
        stamp = pd.Timestamp(value)
        if daily:
            # A date alone is not a timestamp and only qualifies as an explicit close.
            if basis != "close":
                return result
            closed = not finished.empty and stamp.date() == finished.index[-1].date()
        else:
            if stamp.tzinfo is None:
                return result
            if stamp > current:
                return {**result, "status":"stale", "reason_code":"PRICE_TIME_FUTURE"}
            if opened and (current-stamp).total_seconds() <= 300 and calendar.is_open_on_minute(stamp.floor("min")):
                return {**result, "status":"intraday", "reason_code":None, "calendar":code}
            closed = not finished.empty and abs((stamp-finished.iloc[-1]["close"]).total_seconds()) <= 300
        if not opened and closed:
            return {**result, "status":"recent_close", "reason_code":None, "calendar":code,
                    "session_date":finished.index[-1].date().isoformat()}
        return {**result, "status":"stale", "reason_code":"PRICE_STALE", "calendar":code}
    except (ImportError, ValueError, TypeError, KeyError, OverflowError):
        return {**result, "reason_code":"MARKET_CALENDAR_OR_TIME_UNVERIFIED"}
