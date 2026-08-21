from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from backend.services.technical_patterns.core.input_mapper import PatternInputError, PatternInputMapper
from tests.technical_patterns.conftest import canonical_series_from_case


def test_mapper_uses_dense_sessions_across_weekend_and_holiday(golden):
    case = golden["pivot_case"]
    series = canonical_series_from_case(case, source_hash="fixture-source-hash-v1")
    expected_sessions = tuple(date.fromisoformat(item) for item in case["sessions"])

    result = PatternInputMapper().map_series(series, expected_sessions=expected_sessions)

    assert [bar.session_ordinal for bar in result.bars] == [0, 1, 2, 3, 4]
    assert result.bars[1].session_date.isoformat() == "2025-01-03"
    assert result.bars[2].session_date.isoformat() == "2025-01-06"
    assert result.bars[2].session_ordinal - result.bars[1].session_ordinal == 1
    assert all(bar.available_from == bar.session_date for bar in result.bars)


def test_mapper_fails_closed_for_missing_expected_session(golden):
    case = golden["pivot_case"]
    series = canonical_series_from_case(case, source_hash="fixture-source-hash-v1")
    expected = tuple(date.fromisoformat(item) for item in case["sessions"]) + (date(2025, 1, 9),)
    series = replace(series, last_closed_session=date(2025, 1, 9))

    with pytest.raises(PatternInputError, match="2025-01-09") as exc_info:
        PatternInputMapper().map_series(series, expected_sessions=expected)

    assert exc_info.value.code == "EXPECTED_SESSION_MISSING"


def test_mapper_trims_bars_after_last_closed_session_and_as_of(golden):
    case = golden["pivot_case"]
    series = canonical_series_from_case(case, source_hash="fixture-source-hash-v1")
    closed = replace(series, last_closed_session=date(2025, 1, 7))

    result = PatternInputMapper().map_series(closed, as_of_session=date(2025, 1, 6))

    assert result.last_closed_session == date(2025, 1, 6)
    assert tuple(bar.session_date for bar in result.bars) == (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6))


def test_mapper_bar_identity_is_repeatable(golden):
    series = canonical_series_from_case(golden["pivot_case"], source_hash="fixture-source-hash-v1")

    first = PatternInputMapper().map_series(series)
    second = PatternInputMapper().map_series(series)

    assert [bar.bar_id for bar in first.bars] == [bar.bar_id for bar in second.bars]
