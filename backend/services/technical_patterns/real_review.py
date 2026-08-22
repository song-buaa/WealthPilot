"""Stage 1E real-IBKR Development evidence and human review-pack builder.

This module consumes already-canonicalized Stage 0 cache exports.  It never
opens an IBKR connection and has no account, portfolio, execution, or order
surface.  Holdout and untouched partitions are deliberately hashed but never
passed to a detector before the independent human-review gate is complete.
"""

from __future__ import annotations

import hashlib
import html
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from backend.services.pattern_data.contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)

from .calibration import (
    CalibrationKey,
    CalibrationRegistry,
    build_us_ascending_triangle_development_parameter_sets,
    build_us_double_reversal_development_parameter_sets,
    build_us_level_break_development_parameter_sets,
    build_us_rectangle_development_parameter_sets,
)
from .core.identity import stable_hash, stable_id
from .core.input_mapper import PatternInputMapper
from .detectors.ascending_triangle import (
    AscendingTriangleDetector,
    AscendingTriangleDirectionConfirmation,
    AscendingTriangleInvalidation,
    AscendingTriangleStructureConfirmation,
)
from .detectors.double_reversal import (
    DoubleBottomDetector,
    DoubleReversalDirectionConfirmation,
    DoubleReversalInvalidation,
    DoubleReversalStructureConfirmation,
    DoubleTopDetector,
)
from .detectors.framework import DetectorFramework
from .detectors.level_break import (
    BreakdownDetector,
    BreakoutDetector,
    LevelBreakDirectionConfirmation,
    LevelBreakInvalidation,
    LevelBreakStructureConfirmation,
)
from .detectors.rectangle import (
    RectangleDetector,
    RectangleInvalidation,
    RectangleStructureConfirmation,
)
from .indicators import TalibIndicatorLayer


REVIEW_PACK_VERSION = "wp-real-ibkr-six-pattern-review-v1"
ADAPTER_VERSION = "wp-ibkr-pattern-adapter-v1-schedule-paging-v1"
DEVELOPMENT_START = date(2019, 1, 1)
DEVELOPMENT_END = date(2022, 12, 31)
HOLDOUT_START = date(2023, 1, 1)
HOLDOUT_END = date(2024, 12, 31)
UNTOUCHED_START = date(2025, 1, 1)
PATTERN_TYPES = (
    "breakout",
    "breakdown",
    "rectangle",
    "ascending_triangle",
    "double_top",
    "double_bottom",
)
ALLOWED_HUMAN_LABELS = (
    "PASS",
    "FALSE_POSITIVE",
    "FALSE_NEGATIVE",
    "AMBIGUOUS",
    "REVIEW_DISAGREEMENT",
)
CALIBRATION_VERSIONS = {
    "level_break": "wp-us-level-break-development-v1",
    "range": "wp-us-rectangle-development-v1",
    "triangle": "wp-us-ascending-triangle-development-v1",
    "reversal": "wp-us-double-reversal-development-v1",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def load_cached_series(cache_dir: Path, symbol: str) -> CanonicalPatternSeries:
    record = json.loads((cache_dir / f"{symbol}.json").read_text(encoding="utf-8"))
    if record["status"] != "READY" or "series" not in record:
        raise ValueError(f"{symbol} canonical cache is not READY: {record['status']}")
    item = record["series"]
    return CanonicalPatternSeries(
        instrument_id=item["instrument_id"],
        con_id=int(item["conId"]),
        isin=item["ISIN"],
        symbol=item["symbol"],
        market=item["market"],
        currency=item["currency"],
        timezone=item["timezone"],
        adjustment_policy=item["adjustment_policy"],
        calendar_version=item["calendar_version"],
        last_closed_session=date.fromisoformat(item["last_closed_session"]),
        source_bar_hash=item["source_bar_hash"],
        bars=tuple(
            CanonicalPatternBar(
                date=date.fromisoformat(bar["date"]),
                open=Decimal(bar["open"]),
                high=Decimal(bar["high"]),
                low=Decimal(bar["low"]),
                close=Decimal(bar["close"]),
                volume=Decimal(bar["volume"]),
            )
            for bar in item["bars"]
        ),
    )


def _parameter_sets():
    return (
        build_us_level_break_development_parameter_sets()
        + build_us_rectangle_development_parameter_sets()
        + build_us_ascending_triangle_development_parameter_sets()
        + build_us_double_reversal_development_parameter_sets()
    )


def _bindings():
    return (
        (
            "breakout",
            "level_break",
            BreakoutDetector(),
            LevelBreakStructureConfirmation(),
            LevelBreakInvalidation(),
            LevelBreakDirectionConfirmation(),
        ),
        (
            "breakdown",
            "level_break",
            BreakdownDetector(),
            LevelBreakStructureConfirmation(),
            LevelBreakInvalidation(),
            LevelBreakDirectionConfirmation(),
        ),
        (
            "rectangle",
            "range",
            RectangleDetector(),
            RectangleStructureConfirmation(),
            RectangleInvalidation(),
            None,
        ),
        (
            "ascending_triangle",
            "triangle",
            AscendingTriangleDetector(),
            AscendingTriangleStructureConfirmation(),
            AscendingTriangleInvalidation(),
            AscendingTriangleDirectionConfirmation(),
        ),
        (
            "double_top",
            "reversal",
            DoubleTopDetector(),
            DoubleReversalStructureConfirmation(),
            DoubleReversalInvalidation(),
            DoubleReversalDirectionConfirmation(),
        ),
        (
            "double_bottom",
            "reversal",
            DoubleBottomDetector(),
            DoubleReversalStructureConfirmation(),
            DoubleReversalInvalidation(),
            DoubleReversalDirectionConfirmation(),
        ),
    )


def _fact_dict(facts: Iterable[Any]) -> dict[str, Any]:
    return {fact.code: fact.value for fact in facts}


def _serialize_result(symbol: str, result: Any) -> dict[str, Any]:
    candidate = result.candidate
    return {
        "symbol": symbol,
        "candidate_id": candidate.candidate_id,
        "pattern_type": candidate.pattern_type.value,
        "pattern_family": candidate.pattern_family.value,
        "direction": candidate.direction.value,
        "status": result.status,
        "formed_on": candidate.formed_on.isoformat(),
        "formed_session_ordinal": candidate.formed_session_ordinal,
        "available_from": candidate.available_from.isoformat(),
        "available_from_session_ordinal": candidate.available_from_session_ordinal,
        "evaluated_on": candidate.evaluated_on.isoformat(),
        "evaluation_session_ordinal": candidate.evaluation_session_ordinal,
        "candidate_source_bar_hash": candidate.source_bar_hash,
        "candidate_result_hash": result.result_hash,
        "detector_version": candidate.detector_version,
        "calibration_version": candidate.calibration_version,
        "parameter_set_id": candidate.parameter_set_id,
        "indicator_layer_version": candidate.indicator_layer_version,
        "source_pivots": [
            {
                "source_id": item.source_id,
                "available_from": item.available_from.isoformat(),
                "available_from_session_ordinal": item.available_from_session_ordinal,
            }
            for item in candidate.source_pivots
        ],
        "source_boundaries": [
            {
                "source_id": item.source_id,
                "available_from": item.available_from.isoformat(),
                "available_from_session_ordinal": item.available_from_session_ordinal,
            }
            for item in candidate.source_boundaries
        ],
        "geometry_facts": _fact_dict(candidate.geometry_facts),
        "structure_facts": _fact_dict(candidate.structure_facts),
        "structure_confirmation": {
            "state": result.structure_confirmation.state.value,
            "reason": result.structure_confirmation.reason,
            "observed_on": (
                result.structure_confirmation.observed_on.isoformat()
                if result.structure_confirmation.observed_on
                else None
            ),
            "observed_session_ordinal": (
                result.structure_confirmation.observed_session_ordinal
            ),
        },
        "direction_confirmation": {
            "state": result.direction_confirmation.state.value,
            "reason": result.direction_confirmation.reason,
            "observed_on": (
                result.direction_confirmation.observed_on.isoformat()
                if result.direction_confirmation.observed_on
                else None
            ),
            "observed_session_ordinal": (
                result.direction_confirmation.observed_session_ordinal
            ),
            "facts": _fact_dict(result.direction_confirmation.facts),
        },
        "invalidation": {
            "invalidated": result.invalidation.invalidated,
            "condition": result.invalidation.condition,
            "reason": result.invalidation.reason,
            "observed_on": (
                result.invalidation.observed_on.isoformat()
                if result.invalidation.observed_on
                else None
            ),
            "observed_session_ordinal": result.invalidation.observed_session_ordinal,
            "facts": _fact_dict(result.invalidation.facts),
        },
    }


def run_symbol_development(
    cache_dir_text: str,
    instrument: dict[str, Any],
) -> dict[str, Any]:
    """Run all six detectors on Development only; safe for a worker process."""

    series = load_cached_series(Path(cache_dir_text), instrument["symbol"])
    core_input = PatternInputMapper().map_series(
        series,
        as_of_session=DEVELOPMENT_END,
    )
    # IBKR's canonical `market` is the listing venue (NASDAQ/NYSE/ARCA).
    # Calibration binds to the frozen regional market from the Universe manifest.
    core_input = replace(core_input, market=instrument["calibration_market"])
    registry = CalibrationRegistry(_parameter_sets())
    framework = DetectorFramework(
        calibrations=registry,
        indicators=TalibIndicatorLayer(),
    )
    runs: dict[str, Any] = {}
    for pattern_type, family, detector, structure, invalidation, direction in _bindings():
        key = CalibrationKey(
            market=instrument["calibration_market"],
            economic_asset_class=instrument["economic_asset_class"],
            timeframe="1d",
            pattern_family=family,
            pattern_type=pattern_type,
            calibration_version=CALIBRATION_VERSIONS[family],
        )
        output = framework.run(
            core_input,
            evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
            calibration_key=key,
            detector=detector,
            structure_confirmation=structure,
            invalidation=invalidation,
            direction_confirmation=direction,
        )
        filtered = tuple(
            item
            for item in output.results
            if DEVELOPMENT_START <= item.candidate.available_from <= DEVELOPMENT_END
        )
        parameters = registry.resolve(key)
        runs[pattern_type] = {
            "pattern_type": pattern_type,
            "pattern_family": family,
            "calibration_version": key.calibration_version,
            "parameter_set_id": parameters.parameter_set_id,
            "parameter_hash": parameters.parameters_hash,
            "result_count": len(filtered),
            "framework_result_hash": output.result_hash,
            "rejected_candidates": [
                {"proposal_index": item.proposal_index, "reason": item.reason}
                for item in output.rejected_candidates
            ],
            "results": [_serialize_result(instrument["symbol"], item) for item in filtered],
        }
    return {
        "symbol": instrument["symbol"],
        "economic_asset_class": instrument["economic_asset_class"],
        "universe_group": instrument["universe_group"],
        "development_bar_count": len(core_input.bars),
        "development_first_session": core_input.bars[0].session_date.isoformat(),
        "development_last_session": core_input.bars[-1].session_date.isoformat(),
        "runs": runs,
    }


def run_development_universe(
    cache_dir: Path,
    instruments: list[dict[str, Any]],
    *,
    workers: int = 4,
) -> list[dict[str, Any]]:
    if workers <= 0:
        raise ValueError("workers must be positive")
    completed: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_symbol_development, str(cache_dir), instrument): instrument
            for instrument in instruments
        }
        for future in as_completed(futures):
            result = future.result()
            completed.append(result)
            counts = ", ".join(
                f"{pattern}={result['runs'][pattern]['result_count']}"
                for pattern in PATTERN_TYPES
            )
            print(
                f"development detector complete: {result['symbol']} ({counts})",
                flush=True,
            )
    return sorted(completed, key=lambda item: item["symbol"])


def build_dataset_manifest(
    cache_dir: Path,
    universe: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for instrument in universe["instruments"]:
        series = load_cached_series(cache_dir, instrument["symbol"])
        partitions = (
            ("development", DEVELOPMENT_START, DEVELOPMENT_END),
            ("holdout", HOLDOUT_START, HOLDOUT_END),
            ("untouched_validation", UNTOUCHED_START, series.last_closed_session),
        )
        for partition, start, end in partitions:
            bars = tuple(bar for bar in series.bars if start <= bar.date <= end)
            status = "READY" if bars else "INSUFFICIENT_HISTORY"
            entries.append(
                {
                    "instrument_id": series.instrument_id,
                    "conId": series.con_id,
                    "ISIN": series.isin,
                    "symbol": series.symbol,
                    "market": series.market,
                    "calibration_market": instrument["calibration_market"],
                    "economic_asset_class": instrument["economic_asset_class"],
                    "timeframe": "1d",
                    "date_range": {
                        "requested_start": start.isoformat(),
                        "requested_end": end.isoformat(),
                        "actual_start": bars[0].date.isoformat() if bars else None,
                        "actual_end": bars[-1].date.isoformat() if bars else None,
                    },
                    "provider": "IBKR",
                    "whatToShow": "TRADES",
                    "useRTH": True,
                    "adjustment_policy": series.adjustment_policy,
                    "calendar_version": series.calendar_version,
                    "source_bar_hash": build_source_bar_hash(bars) if bars else None,
                    "adapter_version": ADAPTER_VERSION,
                    "last_closed_session": series.last_closed_session.isoformat(),
                    "partition": partition,
                    "bar_count": len(bars),
                    "status": status,
                }
            )
    payload = {
        "manifest_version": "wp-real-ibkr-six-pattern-dataset-v1",
        "provider_contract": {
            "provider": "IBKR",
            "whatToShow": "TRADES",
            "useRTH": True,
            "bar_size": "1 day",
            "unfinished_daily_bar_allowed": False,
            "forward_fill_allowed": False,
            "fake_volume_allowed": False,
        },
        "partition_detection_access": {
            "development": "OPENED_FOR_DETECTOR_REVIEW_PACK",
            "holdout": "HASHED_NOT_OPENED_TO_DETECTOR",
            "untouched_validation": "HASHED_NOT_OPENED_TO_DETECTOR",
        },
        "entries": entries,
    }
    payload["manifest_hash"] = _hash_json(payload)
    return payload


def enrich_universe_manifest(
    universe: dict[str, Any],
    cache_dir: Path,
) -> dict[str, Any]:
    enriched = json.loads(json.dumps(universe))
    enriched.pop("manifest_hash", None)
    enriched["identity_resolution"] = {
        "status": "ALL_RESOLVED_BEFORE_DETECTOR_OUTPUT",
        "method": "IBKR ContractDetails via IBKRHistoricalDataSource",
        "account_data_requested": False,
    }
    for instrument in enriched["instruments"]:
        series = load_cached_series(cache_dir, instrument["symbol"])
        instrument.update(
            {
                "calibration_market": "US",
                "conId": series.con_id,
                "ISIN": series.isin,
                "stockType": (
                    "COMMON"
                    if instrument["universe_group"] == "US_COMMON_STOCK"
                    else "ETF"
                ),
                "secType": "STK",
                "exchange": instrument["query_exchange"],
                "primaryExchange": instrument["query_primary_exchange"],
                "provider_market": series.market,
                "timezone": series.timezone,
                "history_start": series.bars[0].date.isoformat(),
                "history_end": series.bars[-1].date.isoformat(),
                "full_series_source_bar_hash": series.source_bar_hash,
                "calendar_version": series.calendar_version,
                "adjustment_policy": series.adjustment_policy,
                "identity_status": "RESOLVED",
                "data_status": "READY",
            }
        )
    enriched["manifest_hash"] = _hash_json(enriched)
    return enriched


def _round_robin(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(items, key=lambda value: (
        value["symbol"], value.get("available_from", value.get("anchor_date", ""))
    )):
        by_symbol.setdefault(item["symbol"], []).append(item)
    selected: list[dict[str, Any]] = []
    symbols = sorted(by_symbol)
    while len(selected) < limit and any(by_symbol.values()):
        for symbol in symbols:
            if by_symbol[symbol] and len(selected) < limit:
                selected.append(by_symbol[symbol].pop(0))
    return selected


def _negative_controls(
    cache_dir: Path,
    instruments: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    pattern_type: str,
    asset_class: str,
) -> list[dict[str, Any]]:
    run_by_symbol = {item["symbol"]: item for item in runs}
    anchors = (
        date(2019, 3, 31), date(2019, 9, 30),
        date(2020, 3, 31), date(2020, 9, 30),
        date(2021, 3, 31), date(2021, 9, 30),
        date(2022, 3, 31), date(2022, 9, 30),
    )
    controls: list[dict[str, Any]] = []
    for instrument in instruments:
        if instrument["economic_asset_class"] != asset_class:
            continue
        series = load_cached_series(cache_dir, instrument["symbol"])
        development_bars = tuple(
            bar for bar in series.bars if DEVELOPMENT_START <= bar.date <= DEVELOPMENT_END
        )
        detected_dates = {
            date.fromisoformat(item["available_from"])
            for item in run_by_symbol[instrument["symbol"]]["runs"][pattern_type]["results"]
        }
        for anchor in anchors:
            eligible = [bar for bar in development_bars if bar.date <= anchor]
            if len(eligible) < 100:
                continue
            anchor_bar = eligible[-1]
            recent_dates = {bar.date for bar in eligible[-80:]}
            if detected_dates & recent_dates:
                continue
            controls.append(
                {
                    "symbol": instrument["symbol"],
                    "pattern_type": pattern_type,
                    "anchor_date": anchor_bar.date.isoformat(),
                    "anchor_session_ordinal": next(
                        index for index, bar in enumerate(series.bars)
                        if bar.date == anchor_bar.date
                    ),
                    "selection_reason": (
                        "fixed_pre_registered_quarter_anchor_with_no_target_"
                        "pattern_available_in_prior_80_sessions"
                    ),
                }
            )
    return controls


def _dataset_entry(
    dataset_manifest: dict[str, Any],
    symbol: str,
    partition: str,
) -> dict[str, Any]:
    return next(
        item for item in dataset_manifest["entries"]
        if item["symbol"] == symbol and item["partition"] == partition
    )


def build_review_cases(
    cache_dir: Path,
    universe: dict[str, Any],
    dataset_manifest: dict[str, Any],
    runs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    instruments = universe["instruments"]
    run_by_symbol = {item["symbol"]: item for item in runs}
    for pattern_type in PATTERN_TYPES:
        for asset_class in ("EQUITY", "FIXED_INCOME"):
            detected = [
                result
                for instrument in instruments
                if instrument["economic_asset_class"] == asset_class
                for result in run_by_symbol[instrument["symbol"]]["runs"][pattern_type]["results"]
            ]
            selected_detected = _round_robin(detected, 5)
            controls = _round_robin(
                _negative_controls(
                    cache_dir, instruments, runs, pattern_type, asset_class
                ),
                5,
            )
            scope_status = (
                "READY_FOR_HUMAN_CHART_REVIEW"
                if len(detected) >= 5 and len(controls) >= 5
                else "INSUFFICIENT_REAL_CASE_EVIDENCE"
            )
            inventory.append(
                {
                    "pattern_type": pattern_type,
                    "economic_asset_class": asset_class,
                    "detected_case_count": len(detected),
                    "selected_detected_count": len(selected_detected),
                    "negative_control_count": len(controls),
                    "scope_status": scope_status,
                }
            )
            for kind, selected in (
                ("DETECTED_CANDIDATE", selected_detected),
                ("NEGATIVE_CONTROL_NO_DETECTION", controls),
            ):
                for item in selected:
                    symbol = item["symbol"]
                    instrument = next(
                        value for value in instruments if value["symbol"] == symbol
                    )
                    run = run_by_symbol[symbol]["runs"][pattern_type]
                    dataset = _dataset_entry(dataset_manifest, symbol, "development")
                    identity_material = {
                        "review_pack_version": REVIEW_PACK_VERSION,
                        "kind": kind,
                        "symbol": symbol,
                        "pattern_type": pattern_type,
                        "economic_asset_class": asset_class,
                        "candidate_id": item.get("candidate_id"),
                        "anchor_date": item.get("anchor_date"),
                        "source_bar_hash": dataset["source_bar_hash"],
                    }
                    case_id = stable_id("review", identity_material)
                    visualization_path = f"reports/pattern-review/{case_id}.svg"
                    detector_result = (
                        item
                        if kind == "DETECTED_CANDIDATE"
                        else {
                            "classification": "NO_PATTERN_CONTROL_WINDOW",
                            "status": "NO_PATTERN",
                            "anchor_date": item["anchor_date"],
                            "selection_reason": item["selection_reason"],
                        }
                    )
                    date_range = (
                        {
                            "start": item["formed_on"],
                            "end": max(
                                value for value in (
                                    item["available_from"],
                                    item["direction_confirmation"]["observed_on"],
                                    item["invalidation"]["observed_on"],
                                ) if value is not None
                            ),
                        }
                        if kind == "DETECTED_CANDIDATE"
                        else {"start": None, "end": item["anchor_date"]}
                    )
                    cases.append(
                        {
                            "case_id": case_id,
                            "review_case_kind": kind,
                            "pattern_type": pattern_type,
                            "economic_asset_class": asset_class,
                            "universe_group": instrument["universe_group"],
                            "instrument_id": dataset["instrument_id"],
                            "symbol": symbol,
                            "date_range": date_range,
                            "source_bar_hash": dataset["source_bar_hash"],
                            "candidate_source_bar_hash": item.get(
                                "candidate_source_bar_hash"
                            ),
                            "calibration_version": run["calibration_version"],
                            "parameter_set_id": run["parameter_set_id"],
                            "parameter_hash": run["parameter_hash"],
                            "detector_result": detector_result,
                            "status": item.get("status", "NO_PATTERN"),
                            "visualization_path": visualization_path,
                            "human_review_label": None,
                            "human_review_notes": None,
                            "reviewer": None,
                            "reviewed_at": None,
                        }
                    )
    return sorted(cases, key=lambda item: item["case_id"]), inventory


def _svg_text(x: float, y: float, value: Any, *, size: int = 12, color: str = "#334155") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="ui-monospace, SFMono-Regular, Menlo, monospace" '
        f'font-size="{size}" fill="{color}">{html.escape(str(value))}</text>'
    )


def render_case_svg(
    case: dict[str, Any],
    series: CanonicalPatternSeries,
) -> str:
    width, height = 1320, 640
    left, right, top, price_bottom = 72, 1000, 92, 485
    volume_top, volume_bottom = 505, 575
    by_date = {bar.date.isoformat(): index for index, bar in enumerate(series.bars)}
    result = case["detector_result"]
    if case["review_case_kind"] == "DETECTED_CANDIDATE":
        event_ordinals = [
            result["formed_session_ordinal"],
            result["available_from_session_ordinal"],
            result["direction_confirmation"]["observed_session_ordinal"],
            result["invalidation"]["observed_session_ordinal"],
        ]
        event_ordinals = [value for value in event_ordinals if value is not None]
        start = max(0, min(event_ordinals) - 70)
        end = min(len(series.bars) - 1, max(event_ordinals) + 25)
    else:
        end = by_date[result["anchor_date"]]
        start = max(0, end - 159)
    bars = series.bars[start : end + 1]
    price_low = min(float(bar.low) for bar in bars)
    price_high = max(float(bar.high) for bar in bars)
    price_span = max(price_high - price_low, 1e-9)
    max_volume = max(float(bar.volume) for bar in bars) or 1.0

    def x_for(ordinal: int) -> float:
        return left + (ordinal - start) / max(1, end - start) * (right - left)

    def y_for(price: float) -> float:
        return price_bottom - (price - price_low) / price_span * (price_bottom - top)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{price_bottom - top}" fill="#f8fafc" stroke="#cbd5e1"/>',
        f'<rect x="{left}" y="{volume_top}" width="{right - left}" height="{volume_bottom - volume_top}" fill="#f8fafc" stroke="#cbd5e1"/>',
        '<rect x="1018" y="92" width="270" height="483" fill="#ffffff" stroke="#cbd5e1"/>',
        _svg_text(72, 28, f"{case['symbol']} · {case['pattern_type']} · {case['status']}", size=18, color="#0f172a"),
        _svg_text(72, 52, f"{bars[0].date} → {bars[-1].date} · {case['economic_asset_class']} · {case['review_case_kind']}", size=13),
        _svg_text(72, 72, f"case={case['case_id']} · human review: UNSET", size=11, color="#64748b"),
    ]
    points: list[str] = []
    for local_index, bar in enumerate(bars):
        ordinal = start + local_index
        x = x_for(ordinal)
        high_y, low_y, close_y = y_for(float(bar.high)), y_for(float(bar.low)), y_for(float(bar.close))
        color = "#16a34a" if bar.close >= bar.open else "#dc2626"
        parts.append(f'<line x1="{x:.2f}" y1="{high_y:.2f}" x2="{x:.2f}" y2="{low_y:.2f}" stroke="{color}" stroke-width="0.7"/>')
        volume_height = float(bar.volume) / max_volume * (volume_bottom - volume_top)
        parts.append(f'<rect x="{x - 1:.2f}" y="{volume_bottom - volume_height:.2f}" width="2" height="{volume_height:.2f}" fill="{color}" opacity="0.55"/>')
        points.append(f"{x:.2f},{close_y:.2f}")
    parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#2563eb" stroke-width="1.4"/>')

    if case["review_case_kind"] == "DETECTED_CANDIDATE":
        numeric_lines = {
            "boundary_axis": "#7c3aed",
            "range_low": "#16a34a",
            "range_high": "#dc2626",
            "support_at_confirmation": "#16a34a",
            "resistance_at_confirmation": "#dc2626",
            "neckline_price": "#7c3aed",
            "first_extreme_price": "#ea580c",
            "second_extreme_price": "#ea580c",
        }
        facts = {**result["geometry_facts"], **result["structure_facts"]}
        line_legend: list[tuple[str, str]] = []
        for code, color in numeric_lines.items():
            value = facts.get(code)
            if isinstance(value, (int, float)) and price_low <= float(value) <= price_high:
                y = y_for(float(value))
                parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="{color}" stroke-width="1" stroke-dasharray="5 4"/>')
                line_legend.append((f"{code}={float(value):.4g}", color))
        for index, (label, color) in enumerate(line_legend):
            parts.append(
                _svg_text(
                    1030,
                    top + 17 + index * 13,
                    label,
                    size=9,
                    color=color,
                )
            )
        markers = (
            (result["formed_session_ordinal"], "formed", "#0284c7"),
            (result["available_from_session_ordinal"], "available", "#7c3aed"),
            (result["direction_confirmation"]["observed_session_ordinal"], "direction", "#16a34a"),
            (result["invalidation"]["observed_session_ordinal"], "invalidated", "#dc2626"),
        )
        event_legend: list[tuple[str, str, str]] = []
        for ordinal, label, color in markers:
            if ordinal is not None and start <= ordinal <= end:
                x = x_for(ordinal)
                parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{price_bottom}" stroke="{color}" stroke-width="1.2" stroke-dasharray="3 3"/>')
                event_date = series.bars[ordinal].date.isoformat()
                event_legend.append((label, event_date, color))
        for pivot in result["source_pivots"]:
            ordinal = pivot["available_from_session_ordinal"]
            if start <= ordinal <= end:
                x = x_for(ordinal)
                close = float(series.bars[ordinal].close)
                y = y_for(close)
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.2" fill="#f59e0b"/>')
        event_start = top + 32 + len(line_legend) * 13
        parts.append(_svg_text(1030, event_start, "events", size=10, color="#0f172a"))
        for index, (label, event_date, color) in enumerate(event_legend):
            parts.append(
                _svg_text(
                    1030,
                    event_start + 15 + index * 14,
                    f"{label}: {event_date}",
                    size=9,
                    color=color,
                )
            )
        parts.append(
            _svg_text(
                1030,
                event_start + 30 + len(event_legend) * 14,
                "orange dot = pivot availability",
                size=9,
                color="#92400e",
            )
        )
    else:
        parts.append(_svg_text(1030, 122, "negative control", size=11, color="#0f172a"))
        parts.append(_svg_text(1030, 143, "no target Pattern available", size=9, color="#475569"))
        parts.append(_svg_text(1030, 158, "in prior 80 sessions", size=9, color="#475569"))
        parts.append(_svg_text(1030, 185, f"anchor: {result['anchor_date']}", size=9, color="#475569"))

    parts.extend(
        (
            _svg_text(72, 600, f"development source hash: {case['source_bar_hash']}", size=10, color="#64748b"),
            _svg_text(72, 619, "Evidence presentation only; detector facts remain authoritative; no trade semantics.", size=10, color="#64748b"),
            "</svg>",
        )
    )
    return "\n".join(parts)


def write_review_artifacts(
    *,
    repo_root: Path,
    cache_dir: Path,
    universe: dict[str, Any],
    dataset_manifest: dict[str, Any],
    runs: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    review_dir = repo_root / "docs" / "pattern_review"
    image_dir = repo_root / "reports" / "pattern-review"
    review_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    for case in cases:
        series = load_cached_series(cache_dir, case["symbol"])
        target = repo_root / case["visualization_path"]
        target.write_text(render_case_svg(case, series), encoding="utf-8")

    review_manifest = {
        "manifest_version": REVIEW_PACK_VERSION,
        "gate_status": "READY_FOR_HUMAN_CHART_REVIEW",
        "detector_partition_opened": "development",
        "holdout_detector_run": False,
        "untouched_validation_detector_run": False,
        "allowed_human_review_labels": list(ALLOWED_HUMAN_LABELS),
        "human_review_complete": False,
        "universe_manifest_hash": universe["manifest_hash"],
        "dataset_manifest_hash": dataset_manifest["manifest_hash"],
        "case_count": len(cases),
        "cases": cases,
    }
    review_manifest["manifest_hash"] = _hash_json(review_manifest)
    (review_dir / "REAL_IBKR_SIX_PATTERN_HUMAN_REVIEW_MANIFEST.json").write_text(
        json.dumps(review_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review_dir / "REAL_IBKR_SIX_PATTERN_DATASET_MANIFEST.json").write_text(
        json.dumps(dataset_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (review_dir / "REAL_IBKR_PATTERN_UNIVERSE_MANIFEST.json").write_text(
        json.dumps(universe, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_review_index(review_dir, review_manifest, inventory)
    _write_report(repo_root, universe, dataset_manifest, review_manifest, inventory, runs)
    return review_manifest


def _write_review_index(
    review_dir: Path,
    review_manifest: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> None:
    lines = [
        "# Real IBKR Six-Pattern Human Chart Review Index",
        "",
        "> Gate: `READY_FOR_HUMAN_CHART_REVIEW`",
        "",
        "Codex generated detector evidence and blank review fields. A human reviewer must inspect every selected chart and fill only the manifest fields `human_review_label`, `human_review_notes`, `reviewer`, and `reviewed_at`.",
        "",
        "Allowed labels: `PASS`, `FALSE_POSITIVE`, `FALSE_NEGATIVE`, `AMBIGUOUS`, `REVIEW_DISAGREEMENT`.",
        "",
        "## Scope Inventory",
        "",
        "| Pattern | Asset class | Detected | Selected detected | Negative controls | Status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in inventory:
        lines.append(
            f"| {item['pattern_type']} | {item['economic_asset_class']} | "
            f"{item['detected_case_count']} | {item['selected_detected_count']} | "
            f"{item['negative_control_count']} | {item['scope_status']} |"
        )
    lines.extend(("", "## Review Cases", "", "| Case | Pattern | Asset | Kind | Symbol | Detector status | Evidence |", "| --- | --- | --- | --- | --- | --- | --- |"))
    for case in review_manifest["cases"]:
        image = "../../" + case["visualization_path"]
        lines.append(
            f"| `{case['case_id']}` | {case['pattern_type']} | "
            f"{case['economic_asset_class']} | {case['review_case_kind']} | "
            f"{case['symbol']} | {case['status']} | [SVG]({image}) |"
        )
    lines.extend(("", "## Gate Boundary", "", "- Human labels remain `null`.", "- Holdout detector run: `false`.", "- Untouched Validation detector run: `false`.", "- No Production Promotion verdict has been issued.", ""))
    (review_dir / "REAL_IBKR_SIX_PATTERN_REVIEW_INDEX.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _write_report(
    repo_root: Path,
    universe: dict[str, Any],
    dataset_manifest: dict[str, Any],
    review_manifest: dict[str, Any],
    inventory: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> None:
    ready_datasets = sum(item["status"] == "READY" for item in dataset_manifest["entries"])
    detected_total = sum(item["detected_case_count"] for item in inventory)
    insufficient = [item for item in inventory if item["scope_status"] != "READY_FOR_HUMAN_CHART_REVIEW"]
    scope_evidence_line = (
        "- Some scopes remain below the real detected-case target; see the inventory and keep those scopes at `INSUFFICIENT_REAL_CASE_EVIDENCE`."
        if insufficient
        else "- All 12 Pattern/asset scopes met the five-detected-case Review Pack target; this is evidence coverage, not a production-quality verdict."
    )
    lines = [
        "# Real IBKR Six-Pattern Calibration & Human Chart Review Report",
        "",
        "> Stage 1E · Real data evidence gate · 2026-08-22",
        "",
        "## A. Executive Conclusion",
        "",
        "The frozen 17-instrument Universe resolved successfully and produced source-hashed IBKR Daily TRADES series through the latest fully closed session. Only the Development partition was run through the six detectors. Static evidence and a machine-readable manifest were generated with every human-review field left null.",
        "",
        "No independent human review existed at generation time. Parameter promotion, Holdout detection, Untouched Validation detection, and Production Promotion therefore did not run.",
        "",
        "```text",
        "READY_FOR_HUMAN_CHART_REVIEW",
        "```",
        "",
        "## B. Real IBKR Universe",
        "",
        f"- Instruments: {len(universe['instruments'])} (6 common stocks, 6 equity ETFs, 5 fixed-income ETFs).",
        "- All symbols were fixed before ContractDetails resolution and before any detector output.",
        "- Identity resolution: 17/17 unique; no replacement was required.",
        f"- Universe manifest hash: `{universe['manifest_hash']}`.",
        "",
        "## C. Dataset / Source Hashes",
        "",
        f"- Dataset entries: {len(dataset_manifest['entries'])}; READY: {ready_datasets}.",
        f"- Dataset manifest hash: `{dataset_manifest['manifest_hash']}`.",
        "- Contract: `IBKR / TRADES / 1 day / useRTH=true`.",
        "- Adjustment: `IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED`.",
        "- No forward fill, fake OHLC, fake volume, or unfinished Daily bar.",
        "- Holdout and Untouched bars were hashed for lineage but not opened to Detector execution.",
        "",
        "## D. Pattern Case Inventory",
        "",
        "| Pattern | Asset class | Detected | Review detected | Negative controls | Evidence status |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for item in inventory:
        lines.append(
            f"| {item['pattern_type']} | {item['economic_asset_class']} | "
            f"{item['detected_case_count']} | {item['selected_detected_count']} | "
            f"{item['negative_control_count']} | {item['scope_status']} |"
        )
    lines.extend(
        (
            "",
            f"Development detector candidates across all scopes: {detected_total}.",
            "",
            "Negative controls are fixed quarter-anchor windows with no target Pattern available in the prior 80 sessions. They are intentionally unlabeled and allow a human reviewer to identify false negatives or ambiguity. The current Detector Framework does not expose definition-rejected proposals, so these controls must not be described as detector-rejected near misses.",
            "",
            "## E. Human Review Pack",
            "",
            f"- Selected review cases: {review_manifest['case_count']}.",
            "- Evidence format: static SVG with OHLC path, volume, detector geometry, availability/confirmation/invalidation markers where present.",
            "- `human_review_label`, notes, reviewer, and timestamp are all null.",
            f"- Human manifest hash: `{review_manifest['manifest_hash']}`.",
            "",
            "## F. Calibration Attempts",
            "",
            "Not run. Pilot parameters remain Development starting hypotheses.",
            "",
            "## G. Frozen Versions",
            "",
            "No production calibration version was frozen.",
            "",
            "## H. Holdout",
            "",
            "Not opened to Detector execution. Waiting for a completed and frozen independent Human Review Manifest.",
            "",
            "## I. Untouched Validation",
            "",
            "Not opened to Detector execution.",
            "",
            "## J. Per-Pattern Promotion Matrix",
            "",
            "All scopes are `NOT_EVALUATED_HUMAN_REVIEW_PENDING`. This is not a Production Promotion verdict.",
            "",
            "## K. Known Limitations",
            "",
            "- Static charts mark source-pivot availability; the current result contract does not expose each pivot's original source-bar coordinate.",
            "- Definition-rejected proposals are not surfaced by several detector discovery paths; deterministic negative controls are used instead and require human interpretation.",
            scope_evidence_line,
            "- The real Gateway showed that oversized SCHEDULE requests time out; the Adapter now uses bounded 365-session backward pages.",
            "",
            "## L. Safety and Next Step",
            "",
            "```text",
            "IBKR historical read = authorized",
            "Broker mutation = 0",
            "Order mutation = 0",
            "Portfolio mutation = 0",
            "ExecutionPlan mutation = 0",
            "Production DB change = 0",
            "Decision integration = 0",
            "Public network outside authorized IBKR = 0",
            "```",
            "",
            "A human reviewer must now inspect the Review Index and fill the Human Review Manifest. Only after that manifest is frozen may Development calibration continue, followed by a new parameter freeze, Holdout, and finally Untouched Validation.",
            "",
            "```text",
            "READY_FOR_HUMAN_CHART_REVIEW",
            "```",
            "",
        )
    )
    if insufficient:
        lines.insert(
            lines.index("## E. Human Review Pack"),
            "Some Pattern/asset scopes have fewer than the target five real detected candidates and remain `INSUFFICIENT_REAL_CASE_EVIDENCE`; this does not block independent review of other scopes.\n",
        )
    (repo_root / "docs" / "REAL_IBKR_SIX_PATTERN_CALIBRATION_REVIEW_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def generate_review_pack(
    *,
    repo_root: Path,
    cache_dir: Path,
    workers: int = 4,
) -> dict[str, Any]:
    universe_path = repo_root / "docs" / "pattern_review" / "REAL_IBKR_PATTERN_UNIVERSE_MANIFEST.json"
    preregistered = json.loads(universe_path.read_text(encoding="utf-8"))
    if preregistered.get("freeze_stage") != "BEFORE_CONTRACT_RESOLUTION_AND_DETECTOR_OUTPUT":
        raise ValueError("Universe was not pre-registered before Detector output")
    if any(item.get("identity_status") not in {"PENDING_READ_ONLY_RESOLUTION", "RESOLVED"} for item in preregistered["instruments"]):
        raise ValueError("Universe contains an unapproved identity state")
    universe = enrich_universe_manifest(preregistered, cache_dir)
    dataset_manifest = build_dataset_manifest(cache_dir, universe)
    runs = run_development_universe(cache_dir, universe["instruments"], workers=workers)
    cases, inventory = build_review_cases(
        cache_dir, universe, dataset_manifest, runs
    )
    return write_review_artifacts(
        repo_root=repo_root,
        cache_dir=cache_dir,
        universe=universe,
        dataset_manifest=dataset_manifest,
        runs=runs,
        cases=cases,
        inventory=inventory,
    )
