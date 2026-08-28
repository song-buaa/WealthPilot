from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from backend.services.pattern_data.contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)
from backend.services.pattern_data.immutable_dataset import (
    ImmutablePatternDataset,
    build_artifact,
    build_partition_record,
    content_hash,
    read_artifact,
    write_artifact,
)
from backend.services.technical_patterns.core.input_mapper import PatternInputMapper


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json"
)


def _series() -> CanonicalPatternSeries:
    bars = tuple(
        CanonicalPatternBar(
            date=date(2024, 1, day),
            open=Decimal(str(100 + day)),
            high=Decimal(str(102 + day)),
            low=Decimal(str(99 + day)),
            close=Decimal(str(101 + day)),
            volume=Decimal(str(1000 + day)),
        )
        for day in range(2, 7)
    )
    return CanonicalPatternSeries(
        instrument_id="IBKR:1",
        con_id=1,
        isin="US0000000001",
        symbol="TEST",
        market="NYSE",
        currency="USD",
        timezone="US/Eastern",
        adjustment_policy="TEST_ADJUSTED",
        calendar_version="TEST_CALENDAR_V1",
        last_closed_session=bars[-1].date,
        source_bar_hash=build_source_bar_hash(bars),
        bars=bars,
    )


def test_artifact_round_trip_preserves_exact_ohlcv_and_hash(tmp_path):
    artifact = build_artifact(
        _series(),
        capture_as_of="2026-08-28T08:00:00+00:00",
        economic_asset_class="EQUITY",
        adapter_version="adapter-v1",
    )
    path = tmp_path / "TEST.json"
    write_artifact(path, artifact)

    restored = read_artifact(path)
    assert restored.series == artifact.series
    assert restored.artifact_hash == artifact.artifact_hash
    assert content_hash(restored.identity_material) == artifact.artifact_hash


def test_partition_hash_is_deterministic_and_ignores_bars_outside_partition():
    artifact = build_artifact(
        _series(),
        capture_as_of="2026-08-28T08:00:00+00:00",
        economic_asset_class="EQUITY",
        adapter_version="adapter-v1",
    )
    kwargs = {"name": "holdout", "start": date(2024, 1, 3), "end": date(2024, 1, 5)}
    expected = build_partition_record(artifact, **kwargs)
    outside = replace(
        artifact.series.bars[0], close=Decimal("999"), high=Decimal("1000")
    )
    changed_bars = (outside,) + artifact.series.bars[1:]
    changed_series = replace(
        artifact.series,
        bars=changed_bars,
        source_bar_hash=build_source_bar_hash(changed_bars),
    )
    changed = build_artifact(
        changed_series,
        capture_as_of=artifact.capture_as_of,
        economic_asset_class=artifact.economic_asset_class,
        adapter_version=artifact.adapter_version,
    )

    assert build_partition_record(artifact, **kwargs) == expected
    assert build_partition_record(changed, **kwargs)["partition_hash"] == expected["partition_hash"]
    assert changed.artifact_hash != artifact.artifact_hash


def test_one_bar_tamper_changes_artifact_and_partition_hash():
    artifact = build_artifact(
        _series(),
        capture_as_of="2026-08-28T08:00:00+00:00",
        economic_asset_class="EQUITY",
        adapter_version="adapter-v1",
    )
    original = build_partition_record(
        artifact, name="holdout", start=date(2024, 1, 3), end=date(2024, 1, 5)
    )
    bars = list(artifact.series.bars)
    bars[2] = replace(bars[2], close=Decimal("103.5"))
    changed_series = replace(
        artifact.series,
        bars=tuple(bars),
        source_bar_hash=build_source_bar_hash(tuple(bars)),
    )
    changed = build_artifact(
        changed_series,
        capture_as_of=artifact.capture_as_of,
        economic_asset_class=artifact.economic_asset_class,
        adapter_version=artifact.adapter_version,
    )
    current = build_partition_record(
        changed, name="holdout", start=date(2024, 1, 3), end=date(2024, 1, 5)
    )
    assert current["partition_hash"] != original["partition_hash"]
    assert changed.artifact_hash != artifact.artifact_hash


def test_committed_dataset_round_trip_is_artifact_only_and_deterministic(monkeypatch):
    def forbidden_live_source(*args, **kwargs):
        raise AssertionError("validation must not construct an IBKR live source")

    monkeypatch.setattr(
        "backend.services.pattern_data.ibkr_source.IBKRHistoricalDataSource",
        forbidden_live_source,
    )
    dataset = ImmutablePatternDataset(MANIFEST_PATH, REPO_ROOT)
    first = dataset.load_series("AAPL", partition="holdout", warmup_bars=200)
    second = dataset.load_series("AAPL", partition="holdout", warmup_bars=200)

    assert first == second
    assert first.source_bar_hash == build_source_bar_hash(first.bars)
    assert PatternInputMapper().map_series(first) == PatternInputMapper().map_series(second)


def test_committed_dataset_rejects_tampered_artifact(tmp_path):
    dataset = ImmutablePatternDataset(MANIFEST_PATH, REPO_ROOT)
    record = dataset.manifest["instrument_artifacts"][0]
    source = REPO_ROOT / record["artifact_path"]
    target = tmp_path / "tampered.json"
    text = source.read_text(encoding="utf-8")
    target.write_text(text.replace('"close": "', '"close": "9', 1), encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        read_artifact(target)
