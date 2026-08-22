from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from backend.services.pattern_data.contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)
from backend.services.technical_patterns import real_review
from backend.services.technical_patterns.real_review import (
    ALLOWED_HUMAN_LABELS,
    PATTERN_TYPES,
    build_dataset_manifest,
    build_review_cases,
    enrich_universe_manifest,
    load_cached_series,
    render_case_svg,
)


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    values = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def _write_cache(cache_dir: Path, symbol: str, con_id: int) -> None:
    dates = _weekdays(date(2018, 11, 15), date(2026, 8, 21))
    bars = tuple(
        CanonicalPatternBar(
            session,
            Decimal("100") + Decimal(index) / Decimal("100"),
            Decimal("101") + Decimal(index) / Decimal("100"),
            Decimal("99") + Decimal(index) / Decimal("100"),
            Decimal("100.5") + Decimal(index) / Decimal("100"),
            Decimal("1000000") + index,
        )
        for index, session in enumerate(dates)
    )
    series = CanonicalPatternSeries(
        instrument_id=f"IBKR:{con_id}",
        con_id=con_id,
        isin=f"US{con_id:010d}"[:12],
        symbol=symbol,
        market="ARCA" if symbol == "AGG" else "NASDAQ",
        currency="USD",
        timezone="US/Eastern",
        adjustment_policy="IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED",
        calendar_version="IBKR_SCHEDULE_V1:test",
        last_closed_session=dates[-1],
        source_bar_hash=build_source_bar_hash(bars),
        bars=bars,
    )
    (cache_dir / f"{symbol}.json").write_text(
        json.dumps({"status": "READY", "series": series.as_dict()}),
        encoding="utf-8",
    )


def _universe() -> dict:
    return {
        "manifest_version": "test",
        "freeze_stage": "BEFORE_CONTRACT_RESOLUTION_AND_DETECTOR_OUTPUT",
        "instruments": [
            {
                "symbol": "AAPL",
                "universe_group": "US_COMMON_STOCK",
                "economic_asset_class": "EQUITY",
                "query_exchange": "SMART",
                "query_primary_exchange": "NASDAQ",
                "currency": "USD",
                "identity_status": "PENDING_READ_ONLY_RESOLUTION",
            },
            {
                "symbol": "AGG",
                "universe_group": "US_FIXED_INCOME_ETF",
                "economic_asset_class": "FIXED_INCOME",
                "query_exchange": "SMART",
                "query_primary_exchange": "ARCA",
                "currency": "USD",
                "identity_status": "PENDING_READ_ONLY_RESOLUTION",
            },
        ],
    }


def _detected(symbol: str, pattern_type: str) -> dict:
    return {
        "symbol": symbol,
        "candidate_id": f"pat_{symbol}_{pattern_type}",
        "pattern_type": pattern_type,
        "status": "confirmed",
        "formed_on": "2020-01-02",
        "formed_session_ordinal": 300,
        "available_from": "2020-01-15",
        "available_from_session_ordinal": 309,
        "candidate_source_bar_hash": "c" * 64,
        "direction_confirmation": {
            "observed_on": "2020-01-16",
            "observed_session_ordinal": 310,
        },
        "invalidation": {
            "observed_on": None,
            "observed_session_ordinal": None,
        },
    }


def _runs() -> list[dict]:
    values = []
    for symbol, asset_class, group in (
        ("AAPL", "EQUITY", "US_COMMON_STOCK"),
        ("AGG", "FIXED_INCOME", "US_FIXED_INCOME_ETF"),
    ):
        values.append(
            {
                "symbol": symbol,
                "economic_asset_class": asset_class,
                "universe_group": group,
                "runs": {
                    pattern_type: {
                        "calibration_version": "development-v1",
                        "parameter_set_id": "cal_test",
                        "parameter_hash": "p" * 64,
                        "results": [_detected(symbol, pattern_type)],
                    }
                    for pattern_type in PATTERN_TYPES
                },
            }
        )
    return values


def test_real_dataset_manifest_hashes_all_partitions_without_detector_access(tmp_path):
    _write_cache(tmp_path, "AAPL", 265598)
    _write_cache(tmp_path, "AGG", 25985141)
    universe = enrich_universe_manifest(_universe(), tmp_path)
    manifest = build_dataset_manifest(tmp_path, universe)

    assert len(manifest["entries"]) == 6
    assert all(item["status"] == "READY" for item in manifest["entries"])
    assert all(len(item["source_bar_hash"]) == 64 for item in manifest["entries"])
    assert manifest["partition_detection_access"] == {
        "development": "OPENED_FOR_DETECTOR_REVIEW_PACK",
        "holdout": "HASHED_NOT_OPENED_TO_DETECTOR",
        "untouched_validation": "HASHED_NOT_OPENED_TO_DETECTOR",
    }
    assert len(manifest["manifest_hash"]) == 64
    assert enrich_universe_manifest(universe, tmp_path)["manifest_hash"] == universe["manifest_hash"]


def test_review_cases_leave_human_judgment_blank_and_use_negative_controls(tmp_path):
    _write_cache(tmp_path, "AAPL", 265598)
    _write_cache(tmp_path, "AGG", 25985141)
    universe = enrich_universe_manifest(_universe(), tmp_path)
    dataset_manifest = build_dataset_manifest(tmp_path, universe)
    cases, inventory = build_review_cases(
        tmp_path,
        universe,
        dataset_manifest,
        _runs(),
    )

    assert cases
    assert all(case["human_review_label"] is None for case in cases)
    assert all(case["human_review_notes"] is None for case in cases)
    assert all(case["reviewer"] is None for case in cases)
    assert all(case["reviewed_at"] is None for case in cases)
    assert any(
        case["review_case_kind"] == "NEGATIVE_CONTROL_NO_DETECTION"
        for case in cases
    )
    assert all(
        item["scope_status"] == "INSUFFICIENT_REAL_CASE_EVIDENCE"
        for item in inventory
    )
    assert ALLOWED_HUMAN_LABELS == (
        "PASS",
        "FALSE_POSITIVE",
        "FALSE_NEGATIVE",
        "AMBIGUOUS",
        "REVIEW_DISAGREEMENT",
    )


def test_svg_is_static_evidence_and_escapes_untrusted_text(tmp_path):
    _write_cache(tmp_path, "AAPL", 265598)
    series = load_cached_series(tmp_path, "AAPL")
    case = {
        "case_id": "review_test",
        "symbol": "AAPL<script>",
        "pattern_type": "breakout",
        "status": "NO_PATTERN",
        "economic_asset_class": "EQUITY",
        "review_case_kind": "NEGATIVE_CONTROL_NO_DETECTION",
        "source_bar_hash": "a" * 64,
        "detector_result": {
            "anchor_date": "2022-09-30",
        },
    }
    svg = render_case_svg(case, series)

    assert svg.startswith("<svg")
    assert "AAPL&lt;script&gt;" in svg
    assert "AAPL<script>" not in svg
    assert "human review: UNSET" in svg
    assert "Evidence presentation only" in svg


def test_review_module_has_no_live_broker_or_order_surface():
    source = inspect.getsource(real_review)
    forbidden = (
        "placeOrder",
        "cancelOrder",
        "modifyOrder",
        "place_order",
        "cancel_order",
        "OrderManager",
        "IBKRHistoricalDataSource(",
        "from backend.services.pattern_data.ibkr_source import",
    )
    assert all(token not in source for token in forbidden)
