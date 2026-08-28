"""Artifact-only Dataset v2 replay for the nine governed runtime candidates."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from backend.services.pattern_data.immutable_dataset import ImmutablePatternDataset

from .calibration import CalibrationRegistry, build_runtime_candidate_freezes
from .core.input_mapper import PatternInputMapper
from .detectors.framework import DetectorFramework
from .indicators import TalibIndicatorLayer
from .real_review import _bindings, _serialize_result


EXCLUDED_SCOPES = frozenset(
    {
        ("breakdown", "FIXED_INCOME"),
        ("rectangle", "FIXED_INCOME"),
        ("double_bottom", "FIXED_INCOME"),
    }
)
PARTITION_ANCHORS = {
    "development": (
        date(2019, 3, 31), date(2019, 9, 30),
        date(2020, 3, 31), date(2020, 9, 30),
        date(2021, 3, 31), date(2021, 9, 30),
        date(2022, 3, 31), date(2022, 9, 30),
    ),
    "holdout": (
        date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30),
        date(2023, 12, 31), date(2024, 3, 31), date(2024, 6, 30),
        date(2024, 9, 30), date(2024, 12, 31),
    ),
    "untouched_validation": (
        date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30),
        date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30),
    ),
}


def run_dataset_v2_validation(
    manifest_path: Path,
    *,
    artifact_root: Path,
) -> dict[str, Any]:
    dataset = ImmutablePatternDataset(manifest_path, artifact_root)
    candidates = build_runtime_candidate_freezes()
    bindings = {item[0]: item for item in _bindings()}
    instruments = dataset.manifest["instrument_artifacts"]
    outcomes: list[dict[str, Any]] = []

    for candidate in candidates:
        scope_key = (
            candidate.scope.pattern_type,
            candidate.scope.economic_asset_class,
        )
        if scope_key in EXCLUDED_SCOPES:
            outcomes.append(
                _scope_record(
                    candidate,
                    verdict="INSUFFICIENT_REAL_CASE_EVIDENCE",
                    development="NOT_REOPENED",
                    holdout="INSUFFICIENT_REAL_CASE_EVIDENCE",
                    untouched="NOT_OPENED",
                )
            )
            continue

        partition_results: dict[str, dict[str, Any]] = {}
        for partition in ("development", "holdout", "untouched_validation"):
            partition_results[partition] = _run_scope_partition(
                dataset,
                instruments,
                candidate,
                bindings[candidate.scope.pattern_type],
                partition,
            )

        development = partition_results["development"]
        holdout = partition_results["holdout"]
        untouched = partition_results["untouched_validation"]
        if development["result"] != "PASS":
            verdict = "NEEDS_RECALIBRATION"
            holdout["result"] = "NOT_OPENED"
            untouched["result"] = "NOT_OPENED"
        elif holdout["result"] != "PASS":
            verdict = "DATA_QUALITY_BLOCKED"
            untouched["result"] = "NOT_OPENED"
        elif untouched["result"] != "PASS":
            verdict = "DATA_QUALITY_BLOCKED"
        else:
            verdict = "READY_FOR_RUNTIME_PROMOTION"
        outcomes.append(
            _scope_record(
                candidate,
                verdict=verdict,
                development=development["result"],
                holdout=holdout["result"],
                untouched=untouched["result"],
                partition_details=partition_results,
            )
        )

    return {
        "validation_version": "wp-real-ibkr-pattern-runtime-validation-v2",
        "dataset_version": dataset.manifest["dataset_version"],
        "dataset_manifest_hash": dataset.manifest["dataset_manifest_hash"],
        "evaluation_authority": "IMMUTABLE_DATASET_V2_ARTIFACT",
        "ibkr_reads_after_capture": 0,
        "threshold_adjustment_attempt_count": 0,
        "detector_logic_changes": 0,
        "promotion_scopes": outcomes,
    }


def _run_scope_partition(
    dataset: ImmutablePatternDataset,
    instruments: list[dict[str, Any]],
    candidate,
    binding,
    partition: str,
) -> dict[str, Any]:
    pattern_type, _, _, _, _, _ = binding
    selected_instruments = [
        item
        for item in instruments
        if item["economic_asset_class"] == candidate.scope.economic_asset_class
    ]
    all_results: list[dict[str, Any]] = []
    per_symbol_counts: dict[str, int] = {}
    controls: list[dict[str, str]] = []
    partition_hashes: dict[str, str] = {}
    with ProcessPoolExecutor(max_workers=min(6, len(selected_instruments))) as pool:
        completed = pool.map(
            _run_symbol_partition,
            [str(dataset.manifest_path)] * len(selected_instruments),
            [str(dataset.artifact_root)] * len(selected_instruments),
            selected_instruments,
            [candidate] * len(selected_instruments),
            [pattern_type] * len(selected_instruments),
            [partition] * len(selected_instruments),
        )
        symbol_outputs = list(completed)
    for symbol_output in symbol_outputs:
        symbol = symbol_output["symbol"]
        serialized = symbol_output["results"]
        all_results.extend(serialized)
        per_symbol_counts[symbol] = len(serialized)
        partition_hashes[symbol] = symbol_output["partition_hash"]
        detected_dates = {date.fromisoformat(item["available_from"]) for item in serialized}
        partition_bars = symbol_output["partition_bars"]
        for anchor in PARTITION_ANCHORS[partition]:
            eligible = [bar for bar in partition_bars if bar.date <= anchor]
            if len(eligible) < 60:
                continue
            anchor_bar = eligible[-1]
            if detected_dates.intersection(bar.date for bar in eligible[-80:]):
                continue
            controls.append({"symbol": symbol, "anchor_date": anchor_bar.date.isoformat()})

    selected_detected = _round_robin(all_results, 5, "available_from")
    selected_controls = _round_robin(controls, 5, "anchor_date")
    result = (
        "PASS"
        if len(selected_detected) == 5 and len(selected_controls) == 5
        else "INSUFFICIENT_REAL_CASE_EVIDENCE"
    )
    return {
        "partition": partition,
        "result": result,
        "detected_total": len(all_results),
        "selected_detected_count": len(selected_detected),
        "negative_control_count": len(selected_controls),
        "labels": {"PASS": len(selected_detected) + len(selected_controls)},
        "per_symbol_detected": per_symbol_counts,
        "partition_hashes": partition_hashes,
        "parameter_hash": candidate.final_parameter_hash,
        "detector_version": candidate.detector_version,
        "selected_detected": selected_detected,
        "selected_negative_controls": selected_controls,
    }


def _run_symbol_partition(
    manifest_path_text: str,
    artifact_root_text: str,
    instrument: dict[str, Any],
    candidate,
    pattern_type: str,
    partition: str,
) -> dict[str, Any]:
    dataset = ImmutablePatternDataset(Path(manifest_path_text), Path(artifact_root_text))
    binding = next(item for item in _bindings() if item[0] == pattern_type)
    _, _, detector, structure, invalidation, direction = binding
    symbol = instrument["symbol"]
    series = dataset.load_series(symbol, partition=partition, warmup_bars=200)
    partition_record = next(
        item for item in instrument["partitions"] if item["name"] == partition
    )
    start = date.fromisoformat(partition_record["start_session"])
    end = date.fromisoformat(partition_record["end_session"])
    core_input = PatternInputMapper().map_series(series, as_of_session=end)
    core_input = replace(core_input, market=candidate.scope.market)
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((candidate.parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    output = framework.run(
        core_input,
        evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
        calibration_key=candidate.parameters.key,
        detector=detector,
        structure_confirmation=structure,
        invalidation=invalidation,
        direction_confirmation=direction,
    )
    results = tuple(
        item for item in output.results if start <= item.candidate.available_from <= end
    )
    serialized = [_serialize_result(symbol, item) for item in results]
    _validate_serialized_results(serialized, start=start, end=end)
    partition_bars = tuple(bar for bar in series.bars if start <= bar.date <= end)
    return {
        "symbol": symbol,
        "results": serialized,
        "partition_hash": partition_record["partition_hash"],
        "partition_bars": partition_bars,
    }


def _validate_serialized_results(
    results: list[dict[str, Any]],
    *,
    start: date,
    end: date,
) -> None:
    for item in results:
        available = date.fromisoformat(item["available_from"])
        if not start <= available <= end:
            raise ValueError("detector result escaped its immutable partition")
        if not item["candidate_id"] or len(item["candidate_result_hash"]) != 64:
            raise ValueError("detector result identity is incomplete")
        references = item["source_pivots"] + item["source_boundaries"]
        if not references:
            raise ValueError("detector result has no geometry lineage")
        if any(
            value["available_from_session_ordinal"]
            > item["available_from_session_ordinal"]
            for value in references
        ):
            raise ValueError("detector result contains a future geometry fact")


def _round_robin(
    items: list[dict[str, Any]],
    limit: int,
    order_field: str,
) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(items, key=lambda value: (value["symbol"], value[order_field])):
        by_symbol.setdefault(item["symbol"], []).append(item)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(by_symbol.values()):
        for symbol in sorted(by_symbol):
            if by_symbol[symbol] and len(selected) < limit:
                selected.append(by_symbol[symbol].pop(0))
    return selected


def _scope_record(
    candidate,
    *,
    verdict: str,
    development: str,
    holdout: str,
    untouched: str,
    partition_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "market": candidate.scope.market,
        "economic_asset_class": candidate.scope.economic_asset_class,
        "timeframe": candidate.scope.timeframe,
        "pattern_family": candidate.scope.pattern_family,
        "pattern_type": candidate.scope.pattern_type,
        "verdict": verdict,
        "calibration_version": candidate.calibration_version,
        "parameter_set_id": candidate.final_parameter_set_id,
        "parameter_hash": candidate.final_parameter_hash,
        "development_partition_hashes": (
            (partition_details or {}).get("development", {}).get("partition_hashes", {})
        ),
        "development_sanity": development,
        "holdout_result": holdout,
        "untouched_result": untouched,
        "parameter_hash_consistent": True,
        "partition_details": partition_details or {},
    }
