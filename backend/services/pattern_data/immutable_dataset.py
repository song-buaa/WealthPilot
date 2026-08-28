"""Immutable canonical OHLCV artifacts for Pattern calibration and validation.

This module is deliberately provider-free.  It can serialize already-canonical
series and read them back, but it cannot open IBKR or any other live source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)


DATASET_VERSION = "wp-real-ibkr-pattern-dataset-v2"
ARTIFACT_SCHEMA_VERSION = "wp-canonical-pattern-series-artifact-v1"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ImmutablePatternArtifact:
    dataset_version: str
    capture_as_of: str
    economic_asset_class: str
    adapter_version: str
    series: CanonicalPatternSeries
    artifact_hash: str

    @property
    def identity_material(self) -> dict[str, Any]:
        return {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "dataset_version": self.dataset_version,
            "provider_semantics": {
                "provider": "IBKR",
                "whatToShow": "TRADES",
                "useRTH": True,
                "timeframe": "1d",
            },
            "adapter_version": self.adapter_version,
            "economic_asset_class": self.economic_asset_class,
            "series": self.series.as_dict(),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity_material,
            "capture_as_of": self.capture_as_of,
            "artifact_hash": self.artifact_hash,
            "bar_count": len(self.series.bars),
            "first_session": self.series.bars[0].date.isoformat(),
            "last_session": self.series.bars[-1].date.isoformat(),
        }


def build_artifact(
    series: CanonicalPatternSeries,
    *,
    capture_as_of: str,
    economic_asset_class: str,
    adapter_version: str,
    dataset_version: str = DATASET_VERSION,
) -> ImmutablePatternArtifact:
    if not series.bars:
        raise ValueError("immutable Pattern artifact cannot be empty")
    if build_source_bar_hash(series.bars) != series.source_bar_hash:
        raise ValueError("canonical series source hash does not match its bars")
    artifact = ImmutablePatternArtifact(
        dataset_version=dataset_version,
        capture_as_of=capture_as_of,
        economic_asset_class=economic_asset_class.strip().upper(),
        adapter_version=adapter_version,
        series=series,
        artifact_hash="",
    )
    return ImmutablePatternArtifact(
        **{
            **artifact.__dict__,
            "artifact_hash": content_hash(artifact.identity_material),
        }
    )


def write_artifact(path: Path, artifact: ImmutablePatternArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact.as_dict(), ensure_ascii=True, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def read_artifact(path: Path) -> ImmutablePatternArtifact:
    value = json.loads(path.read_text(encoding="utf-8"))
    series = _series_from_dict(value["series"])
    artifact = build_artifact(
        series,
        capture_as_of=str(value["capture_as_of"]),
        economic_asset_class=str(value["economic_asset_class"]),
        adapter_version=str(value["adapter_version"]),
        dataset_version=str(value["dataset_version"]),
    )
    if value.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ValueError("unsupported immutable Pattern artifact schema")
    if value.get("artifact_hash") != artifact.artifact_hash:
        raise ValueError(f"immutable Pattern artifact hash mismatch: {path}")
    if int(value.get("bar_count", -1)) != len(series.bars):
        raise ValueError(f"immutable Pattern artifact bar count mismatch: {path}")
    return artifact


def build_partition_record(
    artifact: ImmutablePatternArtifact,
    *,
    name: str,
    start: date,
    end: date,
) -> dict[str, Any]:
    bars = tuple(bar for bar in artifact.series.bars if start <= bar.date <= end)
    if not bars:
        raise ValueError(f"{artifact.series.symbol} {name} partition is empty")
    material = {
        "instrument_id": artifact.series.instrument_id,
        "conId": artifact.series.con_id,
        "symbol": artifact.series.symbol,
        "partition": name,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "ordered_sessions": [bar.date.isoformat() for bar in bars],
        "bars": [bar.as_dict() for bar in bars],
        "adjustment_policy": artifact.series.adjustment_policy,
        "calendar_version": artifact.series.calendar_version,
        "provider_semantics": {
            "provider": "IBKR",
            "whatToShow": "TRADES",
            "useRTH": True,
            "timeframe": "1d",
        },
    }
    return {
        "name": name,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "start_session": bars[0].date.isoformat(),
        "end_session": bars[-1].date.isoformat(),
        "bar_count": len(bars),
        "ordered_session_set_hash": content_hash(material["ordered_sessions"]),
        "partition_bars_hash": build_source_bar_hash(bars),
        "partition_hash": content_hash(material),
    }


def load_dataset_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    recorded = manifest.get("dataset_manifest_hash")
    material = {key: value for key, value in manifest.items() if key != "dataset_manifest_hash"}
    if recorded != content_hash(material):
        raise ValueError("Dataset v2 manifest hash mismatch")
    return manifest


class ImmutablePatternDataset:
    """Artifact-only evaluation authority; no live-provider dependency exists."""

    def __init__(self, manifest_path: Path, artifact_root: Path | None = None) -> None:
        self.manifest_path = manifest_path
        self.manifest = load_dataset_manifest(manifest_path)
        self.artifact_root = artifact_root or manifest_path.parents[2]
        self._records = {
            item["symbol"].upper(): item
            for item in self.manifest["instrument_artifacts"]
        }

    def load_series(
        self,
        symbol: str,
        *,
        partition: str | None = None,
        warmup_bars: int = 0,
    ) -> CanonicalPatternSeries:
        record = self._records[symbol.upper()]
        path = self.artifact_root / record["artifact_path"]
        artifact = read_artifact(path)
        if artifact.artifact_hash != record["artifact_hash"]:
            raise ValueError("artifact hash drifted from Dataset v2 manifest")
        if partition is None:
            return artifact.series
        partition_record = next(
            item for item in record["partitions"] if item["name"] == partition
        )
        start = date.fromisoformat(partition_record["start_session"])
        end = date.fromisoformat(partition_record["end_session"])
        prior = [bar for bar in artifact.series.bars if bar.date < start]
        selected = tuple(prior[-warmup_bars:]) + tuple(
            bar for bar in artifact.series.bars if start <= bar.date <= end
        )
        if build_partition_record(
            artifact,
            name=partition,
            start=date.fromisoformat(partition_record["requested_start"]),
            end=date.fromisoformat(partition_record["requested_end"]),
        )["partition_hash"] != partition_record["partition_hash"]:
            raise ValueError("partition content drifted from Dataset v2 manifest")
        return CanonicalPatternSeries(
            instrument_id=artifact.series.instrument_id,
            con_id=artifact.series.con_id,
            isin=artifact.series.isin,
            symbol=artifact.series.symbol,
            market=artifact.series.market,
            currency=artifact.series.currency,
            timezone=artifact.series.timezone,
            adjustment_policy=artifact.series.adjustment_policy,
            calendar_version=artifact.series.calendar_version,
            last_closed_session=selected[-1].date,
            source_bar_hash=build_source_bar_hash(selected),
            bars=selected,
        )


def _series_from_dict(value: Mapping[str, Any]) -> CanonicalPatternSeries:
    bars = tuple(
        CanonicalPatternBar(
            date=date.fromisoformat(item["date"]),
            open=Decimal(item["open"]),
            high=Decimal(item["high"]),
            low=Decimal(item["low"]),
            close=Decimal(item["close"]),
            volume=Decimal(item["volume"]),
        )
        for item in value["bars"]
    )
    return CanonicalPatternSeries(
        instrument_id=str(value["instrument_id"]),
        con_id=int(value["conId"]),
        isin=str(value["ISIN"]),
        symbol=str(value["symbol"]),
        market=str(value["market"]),
        currency=str(value["currency"]),
        timezone=str(value["timezone"]),
        adjustment_policy=str(value["adjustment_policy"]),
        calendar_version=str(value["calendar_version"]),
        last_closed_session=date.fromisoformat(value["last_closed_session"]),
        source_bar_hash=str(value["source_bar_hash"]),
        bars=bars,
    )
