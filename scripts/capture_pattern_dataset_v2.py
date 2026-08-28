#!/usr/bin/env python3
"""One-time governed, read-only capture of immutable Pattern Dataset v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.services.pattern_data.contracts import InstrumentQuery  # noqa: E402
from backend.services.pattern_data.ibkr_adapter import (  # noqa: E402
    IBKRPatternAdapterConfig,
    IBKRPatternDataAdapter,
)
from backend.services.pattern_data.ibkr_source import IBKRHistoricalDataSource  # noqa: E402
from backend.services.pattern_data.immutable_dataset import (  # noqa: E402
    DATASET_VERSION,
    build_artifact,
    build_partition_record,
    content_hash,
    write_artifact,
)
from backend.services.technical_patterns.real_review import ADAPTER_VERSION  # noqa: E402


UNIVERSE_PATH = Path("docs/pattern_review/REAL_IBKR_PATTERN_UNIVERSE_MANIFEST.json")
MANIFEST_PATH = Path("docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json")
ARTIFACT_ROOT = Path("data/pattern_evaluation/v2")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True, help="One timezone-aware ISO-8601 capture instant")
    parser.add_argument("--client-id", type=int, default=37)
    return parser.parse_args()


def main() -> int:
    args = _args()
    capture_as_of = datetime.fromisoformat(args.as_of)
    if capture_as_of.tzinfo is None or capture_as_of.utcoffset() is None:
        raise SystemExit("--as-of must be timezone-aware")
    capture_as_of = capture_as_of.astimezone(timezone.utc)

    universe_path = REPO_ROOT / UNIVERSE_PATH
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    instruments = universe["instruments"]
    if len(instruments) != 17:
        raise SystemExit(f"frozen universe must contain exactly 17 instruments, got {len(instruments)}")

    source = IBKRHistoricalDataSource(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", "4001")),
        client_id=args.client_id,
        timeout=30,
    )
    adapter = IBKRPatternDataAdapter(
        source,
        config=IBKRPatternAdapterConfig(
            target_bar_count=1950,
            durations=("8 Y",),
            schedule_calendar_days=3000,
            schedule_page_sessions=365,
        ),
    )
    records = []
    reads = {"contract_details": 0, "historical_data": 0, "schedule": 0}
    try:
        for item in instruments:
            query = InstrumentQuery(
                symbol=item["symbol"],
                exchange=item["query_exchange"],
                primary_exchange=item["query_primary_exchange"],
                currency=item["currency"],
                con_id=int(item["conId"]),
            )
            result = adapter.get_series(query, as_of=capture_as_of, refresh=True)
            reads["contract_details"] += 1
            reads["historical_data"] += len(result.requested_durations)
            if not result.is_ready or result.series is None:
                raise RuntimeError(f"{item['symbol']} capture failed: {result.status.value}: {result.reason}")
            series = result.series
            reads["schedule"] += 1
            reads["schedule"] += max(0, (len(series.bars) - 1) // 365)
            if series.con_id != int(item["conId"]) or series.isin != item["ISIN"]:
                raise RuntimeError(f"{item['symbol']} identity drifted from frozen universe")
            artifact = build_artifact(
                series,
                capture_as_of=capture_as_of.isoformat(),
                economic_asset_class=item["economic_asset_class"],
                adapter_version=ADAPTER_VERSION,
            )
            artifact_rel = ARTIFACT_ROOT / f"{series.symbol}.json"
            write_artifact(REPO_ROOT / artifact_rel, artifact)
            partitions = (
                build_partition_record(
                    artifact,
                    name="development",
                    start=date(2019, 1, 1),
                    end=date(2022, 12, 31),
                ),
                build_partition_record(
                    artifact,
                    name="holdout",
                    start=date(2023, 1, 1),
                    end=date(2024, 12, 31),
                ),
                build_partition_record(
                    artifact,
                    name="untouched_validation",
                    start=date(2025, 1, 1),
                    end=series.last_closed_session,
                ),
            )
            records.append(
                {
                    "instrument_id": series.instrument_id,
                    "symbol": series.symbol,
                    "conId": series.con_id,
                    "ISIN": series.isin,
                    "market": series.market,
                    "economic_asset_class": item["economic_asset_class"],
                    "currency": series.currency,
                    "timezone": series.timezone,
                    "adjustment_policy": series.adjustment_policy,
                    "calendar_version": series.calendar_version,
                    "artifact_path": artifact_rel.as_posix(),
                    "artifact_hash": artifact.artifact_hash,
                    "bar_count": len(series.bars),
                    "first_session": series.bars[0].date.isoformat(),
                    "last_session": series.bars[-1].date.isoformat(),
                    "partitions": list(partitions),
                }
            )
            print(f"captured {series.symbol}: {len(series.bars)} bars {artifact.artifact_hash[:12]}")
    finally:
        source.shutdown()

    material = {
        "dataset_version": DATASET_VERSION,
        "capture_as_of": capture_as_of.isoformat(),
        "universe_manifest_path": UNIVERSE_PATH.as_posix(),
        "universe_manifest_hash": content_hash(universe),
        "provider_semantics": {
            "provider": "IBKR",
            "whatToShow": "TRADES",
            "useRTH": True,
            "timeframe": "1d",
            "fully_closed_sessions_only": True,
        },
        "adapter_version": ADAPTER_VERSION,
        "partition_definitions": {
            "development": {"start": "2019-01-01", "end": "2022-12-31"},
            "holdout": {"start": "2023-01-01", "end": "2024-12-31"},
            "untouched_validation": {
                "start": "2025-01-01",
                "end": max(item["last_session"] for item in records),
            },
        },
        "instrument_artifacts": sorted(records, key=lambda item: item["symbol"]),
        "capture_read_accounting": {
            **reads,
            "account_requests": 0,
            "portfolio_requests": 0,
            "order_requests": 0,
            "broker_mutations": 0,
            "order_mutations": 0,
        },
    }
    manifest = {**material, "dataset_manifest_hash": content_hash(material)}
    path = REPO_ROOT / MANIFEST_PATH
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"dataset manifest: {manifest['dataset_manifest_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
