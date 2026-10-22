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
    schedule = calendar.schedule.loc[frame.index.min().date():pd.Timestamp(at).date()]
    schedule = schedule[schedule["close"] <= pd.Timestamp(at)]
    closed_dates = {v.date() for v in schedule.index}
    frame = frame[[v.date() in closed_dates for v in frame.index]]
    if frame.empty:
        return None
    weekly = frame.resample("W-FRI").agg(dict(open="first",high="max",low="min",close="last",volume="sum")).dropna()
    local = at.astimezone(calendar.tz)
    monday = pd.Timestamp(local.date()) - pd.Timedelta(days=local.weekday())
    weekly = weekly[weekly.index < monday]  # unfinished week never masquerades as closed
    series = {}
    synthetic = any(x in result.source.lower() for x in ("seed","mock","demo","fixture","synthetic"))
    for period, data in (("day",frame),("week",weekly)):
        if data.empty:
            series[period] = dict(status="unavailable", bars=[])
            continue
        closes = data["close"]
        macd, signal, hist = _calc_macd(closes)
        actual = data.index[-1].date()
        expected = schedule if period=="day" else schedule[[v.date() < monday.date() for v in schedule.index]]
        last_session = expected.index[-1].date() if not expected.empty else None
        if period=="week":
            current = last_session is not None and actual == pd.Timestamp(last_session).to_period("W-FRI").end_time.date()
        else:
            current = actual == last_session
        # Missing sessions inside the window are explicit gaps, never interpolated.
        expected_days = {s.date() for s in schedule.index if frame.index[0].date() <= s.date() <= frame.index[-1].date()}
        complete = expected_days == {s.date() for s in frame.index}
        status = "seed" if synthetic else "valid" if current and complete and len(data)>=35 else "stale" if not current else "insufficient"
        rows = [{"date":str(idx.date()), **{key:float(row[key]) for key in ("open","high","low","close","volume")}}
                for idx,row in data.iterrows()]
        series[period] = dict(status=status,symbol=symbol,market=market,currency="HKD" if market=="HK" else "USD",
            period=period,source=result.source,record_id=f"{result.source}:{symbol}:{period}:{actual}",
            start_at=rows[0]["date"],end_at=rows[-1]["date"],last_trading_at=str(last_session) if current else str(actual),
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
            scope=symbol,is_seed=synthetic,is_fallback=False))


def fetch(symbol, bars=300):
    from backend.services.execution_plan.kline_provider import build_kline_registry
    from backend.services.market_data.cache import get_cached, set_cached
    key = f"position-technical:v2:{symbol}:{bars}"
    cached = get_cached(key)
    if cached is not None:
        return cached
    try:
        result, degraded = build_kline_registry().resolve(symbol,symbol.rsplit(":",1)[-1],count=bars)
        if result is None:
            return None
        technical = from_bars(symbol,result)
        if technical:
            technical.provenance["is_fallback"] = bool(degraded)
            set_cached(key,technical,4*3600)
        return technical
    except (KeyError, ValueError, TypeError, IndexError):
        return None
