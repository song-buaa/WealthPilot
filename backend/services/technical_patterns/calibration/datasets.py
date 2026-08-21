"""Dataset partition contracts for detector calibration studies.

The contracts keep development, holdout and untouched validation evidence
separate.  They do not load market data or perform calibration themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.identity import stable_id


class CalibrationPartition(str, Enum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"
    VALIDATION = "validation"


@dataclass(frozen=True)
class CalibrationDataset:
    dataset_id: str
    partition: CalibrationPartition
    market: str
    economic_asset_class: str
    timeframe: str
    instrument_ids: tuple[str, ...]
    source_bar_hashes: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        if not self.dataset_id or not self.instrument_ids or not self.source_bar_hashes or not self.description:
            raise ValueError("calibration datasets require identity, instruments, hashes and description")
        if len(self.source_bar_hashes) != len(self.instrument_ids):
            raise ValueError("each calibration instrument requires one frozen source hash")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("calibration dataset instruments must be unique")
        object.__setattr__(self, "market", self.market.strip().upper())
        object.__setattr__(self, "economic_asset_class", self.economic_asset_class.strip().upper())
        object.__setattr__(self, "timeframe", self.timeframe.strip().lower())


@dataclass(frozen=True)
class CalibrationDatasetManifest:
    development: CalibrationDataset
    holdout: CalibrationDataset
    validation: CalibrationDataset
    manifest_id: str = ""

    def __post_init__(self) -> None:
        expected = (
            (self.development, CalibrationPartition.DEVELOPMENT),
            (self.holdout, CalibrationPartition.HOLDOUT),
            (self.validation, CalibrationPartition.VALIDATION),
        )
        if any(dataset.partition is not partition for dataset, partition in expected):
            raise ValueError("development, holdout and validation slots must match their partitions")
        if len({item.dataset_id for item, _ in expected}) != 3:
            raise ValueError("calibration partitions require distinct dataset identities")
        bindings = {
            (item.market, item.economic_asset_class, item.timeframe)
            for item, _ in expected
        }
        if len(bindings) != 1:
            raise ValueError("calibration partitions must share market/asset/timeframe binding")
        ids = [set(item.instrument_ids) for item, _ in expected]
        hashes = [set(item.source_bar_hashes) for item, _ in expected]
        if any(ids[left] & ids[right] for left in range(3) for right in range(left + 1, 3)):
            raise ValueError("calibration partitions must have disjoint instruments")
        if any(hashes[left] & hashes[right] for left in range(3) for right in range(left + 1, 3)):
            raise ValueError("calibration partitions must have disjoint frozen source hashes")
        expected_id = stable_id("caldata", {"development": self.development, "holdout": self.holdout, "validation": self.validation})
        if self.manifest_id and self.manifest_id != expected_id:
            raise ValueError("manifest_id does not match canonical dataset partitions")
        object.__setattr__(self, "manifest_id", expected_id)
