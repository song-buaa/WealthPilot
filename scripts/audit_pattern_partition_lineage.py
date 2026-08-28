#!/usr/bin/env python3
"""Audit Stage 1E frozen Untouched lineage against fresh read-only IBKR data.

This script deliberately uses only ContractDetails, historical TRADES and
SCHEDULE through ``IBKRHistoricalDataSource``.  It writes its evidence outside
the repository by default and never requests account, portfolio or order data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.pattern_data.contracts import InstrumentQuery, PatternDataStatus
from backend.services.pattern_data.ibkr_adapter import (
    IBKRPatternAdapterConfig,
    IBKRPatternDataAdapter,
)
from backend.services.pattern_data.ibkr_source import IBKRHistoricalDataSource
from backend.services.technical_patterns.calibration.partition_lineage import (
    FrozenPartitionReference,
    PartitionDriftClassification,
    ValidationPartitionSpec,
    build_validation_partition_snapshot,
    compare_partition_lineage,
)


DEFAULT_UNIVERSE = REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_UNIVERSE_MANIFEST.json"
DEFAULT_DATASET = REPO_ROOT / "docs/pattern_review/REAL_IBKR_SIX_PATTERN_DATASET_MANIFEST.json"
DEFAULT_RUNTIME = REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_MANIFEST.json"
DEFAULT_OUTPUT = Path("/tmp/wealthpilot-pattern-partition-lineage-audit.json")


class CountingHistoricalSource:
    """Transparent request counter around the narrow historical source API."""

    def __init__(self, source: IBKRHistoricalDataSource) -> None:
        self._source = source
        self.contract_details_requests = 0
        self.historical_requests = 0
        self.schedule_requests = 0

    def resolve_contract(self, query):
        self.contract_details_requests += 1
        return self._source.resolve_contract(query)

    def fetch_schedule(self, contract, *, end, num_days, use_rth):
        self.schedule_requests += 1
        return self._source.fetch_schedule(
            contract,
            end=end,
            num_days=num_days,
            use_rth=use_rth,
        )

    def fetch_historical_bars(
        self,
        contract,
        *,
        end,
        duration,
        bar_size,
        what_to_show,
        use_rth,
    ):
        self.historical_requests += 1
        return self._source.fetch_historical_bars(
            contract,
            end=end,
            duration=duration,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_rth=use_rth,
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "contract_details_requests": self.contract_details_requests,
            "historical_requests": self.historical_requests,
            "schedule_requests": self.schedule_requests,
            "account_requests": 0,
            "portfolio_requests": 0,
            "order_requests": 0,
            "broker_mutations": 0,
            "order_mutations": 0,
        }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _untouched_entries(dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entry["symbol"]): entry
        for entry in dataset["entries"]
        if entry["partition"] == "untouched_validation"
    }


def _previous_actual_hashes(runtime: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["symbol"]): str(item["actual_hash"])
        for item in runtime["untouched_validation"].get("mismatches", [])
    }


def _previous_matching_symbols(
    runtime: dict[str, Any],
    frozen_symbols: set[str],
) -> set[str]:
    untouched = runtime["untouched_validation"]
    mismatched = {
        str(item["symbol"])
        for item in untouched.get("mismatches", [])
    }
    matched = frozen_symbols - mismatched
    if len(matched) != int(untouched["source_hash_matches"]):
        raise ValueError("prior runtime manifest has inconsistent Untouched accounting")
    return matched


def _iso_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    universe = _load_json(args.universe)
    dataset = _load_json(args.dataset)
    runtime = _load_json(args.runtime)
    untouched = _untouched_entries(dataset)
    previous_actual = _previous_actual_hashes(runtime)
    previous_matching = _previous_matching_symbols(runtime, set(untouched))
    instruments = universe["instruments"]
    if len(instruments) != 17 or set(untouched) != {
        str(item["symbol"]) for item in instruments
    }:
        raise ValueError("audit requires the exact frozen 17-instrument universe")

    observed_at = _iso_datetime(args.as_of)
    raw_source = IBKRHistoricalDataSource(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        timeout=args.timeout,
    )
    source = CountingHistoricalSource(raw_source)
    adapter = IBKRPatternDataAdapter(
        source,
        config=IBKRPatternAdapterConfig(
            target_bar_count=1950,
            durations=("8 Y",),
            schedule_calendar_days=3000,
            schedule_page_sessions=365,
        ),
    )
    rows: list[dict[str, Any]] = []
    try:
        for position, instrument in enumerate(instruments, start=1):
            symbol = str(instrument["symbol"])
            frozen = untouched[symbol]
            date_range = frozen["date_range"]
            spec = ValidationPartitionSpec(
                name="untouched_validation",
                start=date.fromisoformat(str(date_range["requested_start"])),
                end=date.fromisoformat(str(date_range["requested_end"])),
                timeframe=str(frozen["timeframe"]),
                provider=str(frozen["provider"]),
                what_to_show=str(frozen["whatToShow"]),
                use_rth=bool(frozen["useRTH"]),
            )
            query = InstrumentQuery(
                symbol=symbol,
                exchange=str(instrument["query_exchange"]),
                primary_exchange=str(instrument["query_primary_exchange"]),
                currency=str(instrument["currency"]),
                con_id=int(instrument["conId"]),
            )
            result = adapter.get_series(query, as_of=observed_at, refresh=True)
            if result.status is not PatternDataStatus.READY or result.series is None:
                rows.append(
                    {
                        "symbol": symbol,
                        "status": result.status.value,
                        "reason": result.reason,
                        "requested_durations": list(result.requested_durations),
                        "classification": "UNKNOWN_DATA_DRIFT",
                    }
                )
                print(f"[{position:02d}/17] {symbol}: {result.status.value}", flush=True)
                continue

            snapshot = build_validation_partition_snapshot(result.series, spec)
            reference = FrozenPartitionReference.from_manifest_entry(
                frozen,
                source_fetch_hash=str(instrument["full_series_source_bar_hash"]),
            )
            comparison = compare_partition_lineage(reference, snapshot)
            full_envelope_equal = (
                str(instrument["history_start"])
                == result.series.bars[0].date.isoformat()
                and str(instrument["history_end"])
                == result.series.bars[-1].date.isoformat()
                and len(result.series.bars) == 1950
            )
            exact_calendar_lineage_equal = (
                reference.calendar_version == result.series.calendar_version
            )
            classification = comparison.classification
            classification_reason = comparison.reason
            if (
                classification is PartitionDriftClassification.UNKNOWN_DATA_DRIFT
                and full_envelope_equal
                and exact_calendar_lineage_equal
                and reference.actual_start == snapshot.actual_start
                and reference.actual_end == snapshot.actual_end
                and reference.bar_count == snapshot.bar_count
            ):
                # The adapter already fail-closes on any expected SCHEDULE date
                # missing from the canonical bars.  With the same exact full
                # envelope and full SCHEDULE digest, the frozen date set is
                # therefore identical and the remaining bars-only hash drift is
                # an OHLCV value change.
                classification = (
                    PartitionDriftClassification.FROZEN_PARTITION_BAR_VALUE_DRIFT
                )
                classification_reason = (
                    "exact full envelope and SCHEDULE lineage are identical, "
                    "but canonical frozen-partition OHLCV hash changed"
                )
            rows.append(
                {
                    "symbol": symbol,
                    "economic_asset_class": instrument["economic_asset_class"],
                    "identity": {
                        "instrument_id": snapshot.instrument_id,
                        "conId": snapshot.con_id,
                        "ISIN": snapshot.isin,
                    },
                    "status": result.status.value,
                    "original_full_hash": reference.source_fetch_hash,
                    "previous_stage2_actual_hash": previous_actual.get(
                        symbol,
                        reference.partition_bars_hash
                        if symbol in previous_matching
                        else None,
                    ),
                    "current_full_hash": snapshot.source_fetch_hash,
                    "original_frozen_partition_hash": reference.partition_bars_hash,
                    "current_frozen_partition_hash": snapshot.partition_bars_hash,
                    "validation_partition_hash": snapshot.validation_partition_hash,
                    "session_set_hash": snapshot.session_set_hash,
                    "partition_date_count": snapshot.bar_count,
                    "first_partition_date": snapshot.actual_start.isoformat(),
                    "last_partition_date": snapshot.actual_end.isoformat(),
                    "adjustment_policy": snapshot.adjustment_policy,
                    "calendar_policy_version": snapshot.calendar_policy_version,
                    "current_calendar_version": result.series.calendar_version,
                    "full_series_first_date": result.series.bars[0].date.isoformat(),
                    "full_series_last_date": result.series.bars[-1].date.isoformat(),
                    "full_series_bar_count": len(result.series.bars),
                    "requested_durations": list(result.requested_durations),
                    "classification": classification.value,
                    "frozen_partition_identical": comparison.partition_identical,
                    "partition_bars_hash_equal": comparison.partition_bars_hash_equal,
                    "source_fetch_hash_equal": comparison.source_fetch_hash_equal,
                    "session_set_equal": comparison.session_set_equal,
                    "full_series_envelope_equal": full_envelope_equal,
                    "exact_calendar_lineage_equal": exact_calendar_lineage_equal,
                    "reason": classification_reason,
                }
            )
            print(
                f"[{position:02d}/17] {symbol}: {classification.value}",
                flush=True,
            )
    finally:
        raw_source.shutdown()

    classifications = Counter(row["classification"] for row in rows)
    payload = {
        "audit_version": "wp-pattern-partition-lineage-audit-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "as_of": observed_at.isoformat(),
        "frozen_universe_manifest_hash": universe["manifest_hash"],
        "frozen_dataset_manifest_hash": dataset["manifest_hash"],
        "prior_runtime_validation_manifest_hash": runtime["manifest_hash"],
        "provider_contract": {
            "provider": "IBKR",
            "bar_size": "1 day",
            "whatToShow": "TRADES",
            "useRTH": True,
            "target_bar_count": 1950,
            "duration": "8 Y",
            "read_only": True,
        },
        "classification_counts": dict(sorted(classifications.items())),
        "read_accounting": source.counts,
        "instruments": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=34)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--as-of", help="timezone-aware ISO timestamp; default is now")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run_audit(args)
    print(f"output={args.output}")
    print(f"classification_counts={payload['classification_counts']}")
    print(f"read_accounting={payload['read_accounting']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
