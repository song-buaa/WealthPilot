"""Offline OHLCV over the pinned original exchange calendar; no provider calls."""
from datetime import datetime,timezone
from dataclasses import asdict
import pandas as pd
import exchange_calendars as ec

from backend.services.execution_plan.kline_provider import KlineResult
from backend.services.market_data.technical_evidence import from_bars
from backend.services.market_data.evidence import technical_period_quality

AT=datetime(2026,10,29,12,tzinfo=timezone.utc)


def bars(start='2025-12-01',end='2026-10-29'):
    dates=ec.get_calendar('XHKG').sessions_in_range(start,end)
    return pd.DataFrame([dict(date=str(d.date()),open=100+i*.1,high=102+i*.1,low=99+i*.1,close=101+i*.1,volume=10000+i) for i,d in enumerate(dates)])


def technical(df,at=AT):
    return from_bars('3690:HK',KlineResult(df,'broker'),at=at)


def test_official_HKEX_2026_holidays_and_half_days_are_not_missing():
    c=ec.get_calendar('XHKG')
    # HKEX CT/075/25; dates are offline source fixtures, not runtime overrides.
    closed=['2026-01-01','2026-02-17','2026-02-18','2026-02-19','2026-04-03','2026-04-06','2026-04-07','2026-05-01','2026-05-25','2026-06-19','2026-07-01','2026-10-01','2026-10-19','2026-12-25']
    assert all(not c.is_session(d) for d in closed)
    for day in ['2026-02-16','2026-12-24','2026-12-31']:
        assert c.session_close(day).tz_convert('Asia/Hong_Kong').hour==12
    t=technical(bars())
    assert t.series['day']['status']==t.series['week']['status']=='valid'
    assert not t.series['day']['coverage']['missing_trading_sessions']


def test_current_unfinished_week_gap_does_not_invalidate_closed_week_history():
    df=bars();df=df[df.date!='2026-10-28']
    t=technical(df)
    assert t.series['day']['status']=='incomplete'
    assert t.series['day']['coverage']['missing_trading_sessions']==['2026-10-28']
    assert t.series['week']['status']=='valid'
    q=technical_period_quality(asdict(t),'3690:HK',AT)
    assert q['day']['quality']=='unknown' and q['week']['quality']=='valid'
    assert 'PERIOD_INSUFFICIENT' not in q['day']['issues']


def test_real_internal_gap_blocks_both_periods_and_reports_actual_date():
    df=bars();df=df[df.date!='2026-10-15'];t=technical(df)
    assert len(t.series['day']['bars'])>35
    for period in ['day','week']:
        assert t.series[period]['status']=='incomplete'
        assert t.series[period]['coverage']['missing_trading_sessions']==['2026-10-15']
        assert 'MISSING_TRADING_SESSIONS' in t.series[period]['issues']
        assert 'INSUFFICIENT_BARS' not in t.series[period]['issues']


def test_count_window_starting_midweek_cannot_certify_a_partial_week():
    t=technical(bars(start='2025-12-02'))
    assert t.series['day']['status']=='valid'
    assert t.series['week']['bars'][0]['date']=='2025-12-12'
    assert t.series['week']['status']=='valid'


def test_holiday_friday_week_is_closed_after_thursday_actual_close():
    at=datetime(2026,4,2,12,tzinfo=timezone.utc)
    t=technical(bars(start='2025-05-05',end='2026-04-02'),at)
    w=t.series['week']
    assert w['status']=='valid' and w['end_at']=='2026-04-03'
    assert w['last_trading_at']=='2026-04-02'  # aggregation label is not a fake Friday session


def test_half_day_is_a_required_session_not_a_holiday_or_assumed_suspension():
    at=datetime(2026,2,20,12,tzinfo=timezone.utc)
    df=bars(start='2025-03-03',end='2026-02-20');df=df[df.date!='2026-02-16']
    t=technical(df,at)
    assert t.series['week']['status']=='incomplete'
    assert '2026-02-16' in t.series['week']['coverage']['missing_trading_sessions']
