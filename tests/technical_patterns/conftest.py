from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.services.pattern_data.contracts import CanonicalPatternBar, CanonicalPatternSeries


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "tovest_tpg_v1_10_foundation_golden.json"


@pytest.fixture(scope="session")
def golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def canonical_series_from_case(case: dict, *, source_hash: str) -> CanonicalPatternSeries:
    sessions = tuple(date.fromisoformat(item) for item in case["sessions"])
    highs = case.get("highs", [101.0] * len(sessions))
    lows = case.get("lows", [99.0] * len(sessions))
    bars = tuple(
        CanonicalPatternBar(
            date=session,
            open=Decimal(str(low + 0.2)) if "lows" in case else Decimal("100"),
            high=Decimal(str(high)),
            low=Decimal(str(low)),
            close=Decimal(str((high + low) / 2)) if "lows" in case else Decimal("100"),
            volume=Decimal("100"),
        )
        for session, high, low in zip(sessions, highs, lows)
    )
    return CanonicalPatternSeries(
        instrument_id=case["instrument_id"],
        con_id=case["con_id"],
        isin=case.get("isin", "FIXTUREISIN"),
        symbol=case.get("symbol", "FIXTURE"),
        market="TEST",
        currency="USD",
        timezone="US/Eastern",
        adjustment_policy="IBKR_TRADES_SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED",
        calendar_version="fixture-calendar-v1",
        last_closed_session=sessions[-1],
        source_bar_hash=source_hash,
        bars=bars,
    )
