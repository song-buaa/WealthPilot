"""Closed daily/weekly evidence over the ORIGINAL KlineProvider registry."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd


def from_bars(symbol, result, *, at=None):
    import exchange_calendars as calendars
    from backend.services.market_data.tiger_kline_service import _calc_macd, _calc_rsi, _determine_ma_position, _determine_trend
    from backend.services.market_data.schema import TechnicalData
    at = at or datetime.now(timezone.utc)
    market = symbol.rsplit(":", 1)[-1]
    calendar = calendars.get_calendar({"US":"XNYS", "HK":"XHKG"}[market])
    frame = result.bars.copy()
    dates = frame["date"]
    if pd.api.types.is_numeric_dtype(dates):
        dates = pd.to_datetime(dates, unit="ms", utc=True).dt.tz_convert(str(calendar.tz)).dt.date
    else:
        dates = pd.to_datetime(dates).dt.date
    frame["date"] = pd.to_datetime(dates)
    if frame["date"].duplicated().any():
        raise ValueError("DUPLICATE_KLINE_DATE")
    frame = frame.sort_values("date").set_index("date")
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if (not np.isfinite(frame[["open","high","low","close","volume"]].to_numpy()).all()
            or (frame[["open","high","low","close"]] <= 0).any().any()
            or (frame["volume"] < 0).any()
            or (frame["low"] > frame[["open","close"]].min(axis=1)).any()
            or (frame["high"] < frame[["open","close"]].max(axis=1)).any()):
        raise ValueError("INVALID_OHLCV")
    local_day = pd.Timestamp(at.astimezone(calendar.tz).date())
    week_start = frame.index.min().to_period("W-FRI").start_time.date()
    week_end = local_day.to_period("W-FRI").end_time.date()
    full_schedule = calendar.schedule.loc[week_start:week_end]
    schedule = full_schedule[full_schedule["close"] <= pd.Timestamp(at)]
    closed_dates = {v.date() for v in schedule.index}
    frame = frame[[v.date() in closed_dates for v in frame.index]]
    if frame.empty:
        return None
    week_labels = full_schedule.index.to_period("W-FRI").end_time.normalize()
    week_closes = full_schedule.groupby(week_labels)["close"].max()
    closed_weeks = week_closes[week_closes <= pd.Timestamp(at)].index
    weekly = frame.resample("W-FRI").agg(dict(open="first",high="max",low="min",close="last",volume="sum")).dropna()
    weekly = weekly[weekly.index.isin(closed_weeks)]
    # A count-limited daily window may start midweek. It cannot certify that
    # partial boundary bucket as a complete week; internal gaps are never dropped.
    if not weekly.empty:
        first = weekly.index[0]
        required = full_schedule.loc[(first-pd.Timedelta(days=6)).date():first.date()]
        if any(v.date() < frame.index[0].date() for v in required.index):
            weekly = weekly.iloc[1:]
    series = {}
    synthetic = any(x in result.source.lower() for x in ("seed","mock","demo","fixture","synthetic"))
    for period, data in (("day",frame),("week",weekly)):
        if data.empty:
            series[period] = dict(status="unavailable", bars=[])
            continue
        closes = data["close"]
        macd, signal, hist = _calc_macd(closes)
        actual = data.index[-1].date()
        if period=="day":
            expected = schedule.loc[frame.index[0].date():]
            last_session = expected.index[-1].date() if not expected.empty else None
            current = actual == last_session
            observed = {v.date() for v in frame.index}
        else:
            last_week = closed_weeks[-1] if len(closed_weeks) else None
            expected = full_schedule.loc[(data.index[0]-pd.Timedelta(days=6)).date():last_week.date()] if last_week is not None else full_schedule.iloc[:0]
            last_session = expected.index[-1].date() if not expected.empty else None
            current = last_week is not None and data.index[-1] == last_week
            observed = {v.date() for v in frame.index if not expected.empty and expected.index[0].date() <= v.date() <= expected.index[-1].date()}
        expected_days = {v.date() for v in expected.index}
        missing = sorted(str(d) for d in expected_days-observed)
        complete = expected_days == observed
        status = ("seed" if synthetic else "stale" if not current else
                  "insufficient" if len(data)<35 else "incomplete" if not complete else "valid")
        rows = [{"date":str(idx.date()), **{key:float(row[key]) for key in ("open","high","low","close","volume")}}
                for idx,row in data.iterrows()]
        issues = (["SYNTHETIC_SOURCE"] if synthetic else [])
        if not current: issues.append("STALE_PERIOD")
        if not complete: issues.append("MISSING_TRADING_SESSIONS")
        if len(data)<35: issues.append("INSUFFICIENT_BARS")
        series[period] = dict(status=status,issues=issues,symbol=symbol,market=market,currency="HKD" if market=="HK" else "USD",
            period=period,source=result.source,record_id=f"{result.source}:{symbol}:{period}:{actual}",
            start_at=rows[0]["date"],end_at=rows[-1]["date"],last_trading_at=str(last_session) if current else str(actual),
            coverage=dict(calendar=calendar.name, calendar_version=calendars.__version__,
                missing_trading_sessions=missing,expected_sessions=len(expected_days),observed_sessions=len(observed)),
            bars=rows,parameters=dict(ma=[5,20],rsi=14,macd=[12,26,9],weekly="closed W-FRI OHLCV"),
            indicators=dict(ma5=float(closes.tail(5).mean()),ma20=float(closes.tail(20).mean()),
                rsi14=_calc_rsi(closes),macd=macd,macd_signal=signal,macd_hist=hist,
                volume=float(data["volume"].iloc[-1]),average_volume20=float(data["volume"].tail(20).mean()),
                observed_support20=float(data["low"].tail(20).min()),observed_resistance20=float(data["high"].tail(20).max())),
            price_semantics="historical observation, never an order limit")
    daily = series["day"]["indicators"]
    return TechnicalData(symbol=symbol,current_price=float(frame["close"].iloc[-1]),
        ma5=daily["ma5"],ma20=daily["ma20"],rsi14=daily["rsi14"],macd=daily["macd"],
        macd_signal=daily["macd_signal"],macd_hist=daily["macd_hist"],data_as_of=str(frame.index[-1].date()),
        ma_position=_determine_ma_position(float(frame["close"].iloc[-1]),daily["ma5"],daily["ma20"]),
        trend_signal=_determine_trend(_determine_ma_position(float(frame["close"].iloc[-1]),daily["ma5"],daily["ma20"]),daily["rsi14"],daily["macd_hist"]),
        bars_count=len(frame),source=result.source,series=series,
        provenance=dict(source=result.source,record_id=series["day"]["record_id"],
            data_as_of=str(frame.index[-1].date()),fetched_at=at.isoformat(),period="day/week",
            scope=symbol,is_seed=synthetic,is_fallback=False, provider_diagnostics=getattr(result,"diagnostics",{})))


def fetch(symbol, bars=300):
    from backend.services.execution_plan.kline_provider import build_kline_registry
    from backend.services.market_data.cache import get_cached, set_cached
    key = f"position-technical:v2:{symbol}:{bars}"
    cached = get_cached(key)
    from backend.services.market_data.av_fundamentals_service import get_next_av_key
    credential_token = hash(get_next_av_key())
    if cached is not None and isinstance(cached, tuple) and cached[1] == credential_token:
        from copy import deepcopy
        value = deepcopy(cached[0])
        value.provenance["cache_hit"] = True
        return value
    try:
        result, degraded = build_kline_registry().resolve(symbol,symbol.rsplit(":",1)[-1],count=bars)
        if result is None:
            return None
        technical = from_bars(symbol,result)
        if technical:
            technical.provenance["cache_hit"] = False
            technical.provenance["is_fallback"] = bool(degraded)
            set_cached(key,(technical,credential_token),4*3600)
        return technical
    except (KeyError, ValueError, TypeError, IndexError):
        return None
