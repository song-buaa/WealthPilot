from __future__ import annotations

from datetime import date

from backend.services.technical_patterns.core.geometry import SessionPoint, build_two_line_geometry, fit_line, line_price, session_span
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.core.lifecycle import LifecycleCore, LifecycleState


def test_geometry_matches_frozen_tovest_fit(golden):
    case = golden["geometry_case"]
    points = tuple(SessionPoint(item[0], item[1]) for item in case["points"])

    result = fit_line(points, base_price=case["base_price"])

    assert result.slope_per_session == case["expected"]["slope_per_session"]
    assert result.intercept == case["expected"]["intercept"]
    assert result.max_error_pct == case["expected"]["max_error_pct"]
    assert line_price(result, 6) == 107.0


def test_geometry_uses_session_distance_not_weekend_wall_clock():
    friday_ordinal, monday_ordinal = 7, 8
    assert session_span(friday_ordinal, monday_ordinal) == 1

    geometry = build_two_line_geometry(
        (SessionPoint(0, 110), SessionPoint(4, 110)),
        (SessionPoint(0, 100), SessionPoint(4, 104)),
        base_price=105,
        start_session_ordinal=0,
        confirmed_session_ordinal=4,
    )
    assert geometry.start_gap == 10
    assert geometry.confirmed_gap == 6
    assert geometry.contraction_pct == 0.4
    assert geometry.apex_session_offset == 10


def test_identity_matches_frozen_source_helper_and_repeats(golden):
    case = golden["identity_case"]
    first = stable_id(case["prefix"], case["material"])
    second = stable_id(case["prefix"], case["material"])

    assert first == second == case["expected"]
    assert stable_hash({"source": ["p-1", "p-2"]}) == stable_hash({"source": ("p-1", "p-2")})


def test_lifecycle_confirmation_and_expiry_match_frozen_sequence(golden):
    case = golden["lifecycle_case"]
    sessions = (date(2025, 2, 3), date(2025, 2, 4), date(2025, 2, 5), date(2025, 2, 6), date(2025, 2, 7))
    current = LifecycleCore.candidate(case["pattern_id"], formed_on=sessions[0], formed_session_ordinal=0)
    current = LifecycleCore.evaluate(
        current,
        session_date=sessions[1],
        session_ordinal=case["confirmed_session_ordinal"],
        confirmation=True,
        expires_at_session_ordinal=case["expires_at_session_ordinal"],
    )
    current = LifecycleCore.evaluate(
        current,
        session_date=sessions[4],
        session_ordinal=4,
        expires_at_session_ordinal=case["expires_at_session_ordinal"],
    )

    assert current.state.value == case["expected_state"]
    assert [[item.from_state.value, item.to_state.value, item.session_ordinal] for item in current.transitions] == case["expected_transitions"]
    assert current.result_hash == case["expected_result_hash"]


def test_lifecycle_invalidation_wins_over_expiry_and_is_terminal():
    current = LifecycleCore.candidate("pattern", formed_on=date(2025, 2, 3), formed_session_ordinal=0)
    current = LifecycleCore.evaluate(current, session_date=date(2025, 2, 4), session_ordinal=1, confirmation=True)
    current = LifecycleCore.evaluate(
        current,
        session_date=date(2025, 2, 7),
        session_ordinal=4,
        invalidation_reason="closed_session_crossed_invalidation",
        expires_at_session_ordinal=4,
    )
    later = LifecycleCore.evaluate(current, session_date=date(2025, 2, 10), session_ordinal=5, expires_at_session_ordinal=4)

    assert current.state is LifecycleState.INVALIDATED
    assert current.invalidated_session_ordinal == 4
    assert later.state is LifecycleState.INVALIDATED
    assert later.transitions == current.transitions
