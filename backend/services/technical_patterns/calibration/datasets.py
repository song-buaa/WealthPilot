"""Immutable dataset manifests for US Pattern calibration and validation.

This module describes frozen evidence only. It does not load market data,
search parameters, score returns, or promote a detector into production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..core.identity import stable_hash, stable_id


class CalibrationPartition(str, Enum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"
    UNTOUCHED_VALIDATION = "untouched_validation"
    VALIDATION = "untouched_validation"  # Stage 1C compatibility alias


class PatternReviewLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    AMBIGUOUS = "ambiguous"
    REVIEW_DISAGREEMENT = "review_disagreement"


class DatasetReviewStatus(str, Enum):
    SEALED = "sealed"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"


class AssetCoverage(str, Enum):
    COMMON_STOCK = "common_stock"
    BROAD_MARKET_ETF = "broad_market_etf"
    SECTOR_ETF = "sector_etf"
    FIXED_INCOME_ETF = "fixed_income_etf"


class MarketRegime(str, Enum):
    BULL = "bull_market"
    BEAR = "bear_market"
    SIDEWAYS = "sideways"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"


class MarketEdgeCase(str, Enum):
    EARNINGS_GAP = "earnings_gap"
    OVERNIGHT_GAP = "overnight_gap"
    SPLIT = "split"
    DIVIDEND = "dividend"
    HOLIDAY = "holiday"
    HALF_DAY = "half_day"
    LOW_LIQUIDITY = "low_liquidity"


REQUIRED_MARKET_REGIMES = frozenset(MarketRegime)
REQUIRED_ASSET_COVERAGE_BY_CLASS = {
    "EQUITY": frozenset(
        {
            AssetCoverage.COMMON_STOCK,
            AssetCoverage.BROAD_MARKET_ETF,
            AssetCoverage.SECTOR_ETF,
        }
    ),
    "FIXED_INCOME": frozenset({AssetCoverage.FIXED_INCOME_ETF}),
}
REQUIRED_MARKET_EDGE_CASES_BY_CLASS = {
    "EQUITY": frozenset(MarketEdgeCase),
    "FIXED_INCOME": frozenset(
        {
            MarketEdgeCase.OVERNIGHT_GAP,
            MarketEdgeCase.SPLIT,
            MarketEdgeCase.DIVIDEND,
            MarketEdgeCase.HOLIDAY,
            MarketEdgeCase.HALF_DAY,
            MarketEdgeCase.LOW_LIQUIDITY,
        }
    ),
}


@dataclass(frozen=True)
class CalibrationDataset:
    """One frozen instrument/date-range sample in a Pattern manifest."""

    instrument: str
    market: str
    economic_asset_class: str
    timeframe: str
    date_range: tuple[date, date]
    source_provider: str
    source_bar_hash: str
    adjustment_policy: str
    calendar_version: str
    label: PatternReviewLabel | None
    partition: CalibrationPartition
    review_status: DatasetReviewStatus
    asset_coverage: AssetCoverage
    market_regimes: tuple[MarketRegime, ...]
    edge_cases: tuple[MarketEdgeCase, ...] = ()
    dataset_id: str = ""

    def __post_init__(self) -> None:
        text_fields = {
            "instrument": self.instrument,
            "market": self.market,
            "economic_asset_class": self.economic_asset_class,
            "timeframe": self.timeframe,
            "source_provider": self.source_provider,
            "source_bar_hash": self.source_bar_hash,
            "adjustment_policy": self.adjustment_policy,
            "calendar_version": self.calendar_version,
        }
        if any(not str(value).strip() for value in text_fields.values()):
            raise ValueError("calibration dataset fields must be non-empty")
        start, end = self.date_range
        if end < start:
            raise ValueError("calibration dataset date_range must be chronological")
        if not isinstance(self.partition, CalibrationPartition):
            raise ValueError("calibration dataset partition must be explicit")
        if not isinstance(self.review_status, DatasetReviewStatus):
            raise ValueError("calibration dataset review_status must be explicit")
        if self.label is not None and not isinstance(self.label, PatternReviewLabel):
            raise ValueError("calibration dataset label must use the review contract")
        if self.review_status is DatasetReviewStatus.COMPLETED and self.label is None:
            raise ValueError("completed review requires a frozen label")
        if self.review_status is DatasetReviewStatus.SEALED and self.label is not None:
            raise ValueError("sealed holdout/validation labels must remain hidden")
        if not self.market_regimes or len(set(self.market_regimes)) != len(self.market_regimes):
            raise ValueError("calibration dataset requires unique market regime evidence")
        if len(set(self.edge_cases)) != len(self.edge_cases):
            raise ValueError("calibration dataset edge cases must be unique")
        object.__setattr__(self, "instrument", self.instrument.strip())
        object.__setattr__(self, "market", self.market.strip().upper())
        object.__setattr__(self, "economic_asset_class", self.economic_asset_class.strip().upper())
        object.__setattr__(self, "timeframe", self.timeframe.strip().lower())
        object.__setattr__(self, "source_provider", self.source_provider.strip())
        expected_id = stable_id(
            "calitem",
            {
                "instrument": self.instrument,
                "market": self.market,
                "economic_asset_class": self.economic_asset_class,
                "timeframe": self.timeframe,
                "date_range": self.date_range,
                "source_provider": self.source_provider,
                "source_bar_hash": self.source_bar_hash,
                "adjustment_policy": self.adjustment_policy,
                "calendar_version": self.calendar_version,
                "partition": self.partition,
            },
        )
        if self.dataset_id and self.dataset_id != expected_id:
            raise ValueError("dataset_id does not match canonical source identity")
        object.__setattr__(self, "dataset_id", expected_id)

    @property
    def dataset_hash(self) -> str:
        return stable_hash(self)


@dataclass(frozen=True)
class CalibrationDatasetManifest:
    """One Pattern's chronological development/holdout/untouched manifest."""

    pattern_family: str
    pattern_type: str
    manifest_version: str
    datasets: tuple[CalibrationDataset, ...]
    manifest_id: str = ""

    def __post_init__(self) -> None:
        if (
            not self.pattern_family.strip()
            or not self.pattern_type.strip()
            or not self.manifest_version.strip()
        ):
            raise ValueError("manifest requires pattern family, type and version")
        if not self.datasets:
            raise ValueError("manifest requires frozen dataset entries")
        object.__setattr__(self, "pattern_family", self.pattern_family.strip().lower())
        object.__setattr__(self, "pattern_type", self.pattern_type.strip().lower())
        object.__setattr__(self, "manifest_version", self.manifest_version.strip())
        if len({item.dataset_id for item in self.datasets}) != len(self.datasets):
            raise ValueError("manifest dataset identities must be unique")
        if len({item.source_bar_hash for item in self.datasets}) != len(self.datasets):
            raise ValueError("manifest source bar hashes must be disjoint")
        bindings = {
            (item.market, item.economic_asset_class, item.timeframe)
            for item in self.datasets
        }
        if len(bindings) != 1:
            raise ValueError(
                "Stage 1D manifests require one exact US/economic-asset-class/1d binding"
            )
        market, economic_asset_class, timeframe = next(iter(bindings))
        if market != "US" or timeframe != "1d":
            raise ValueError(
                "Stage 1D manifests require one exact US/economic-asset-class/1d binding"
            )
        if economic_asset_class not in REQUIRED_ASSET_COVERAGE_BY_CLASS:
            raise ValueError(
                "Stage 1D manifests support EQUITY or FIXED_INCOME calibration only"
            )
        partitions = {item.partition for item in self.datasets}
        required = {
            CalibrationPartition.DEVELOPMENT,
            CalibrationPartition.HOLDOUT,
            CalibrationPartition.UNTOUCHED_VALIDATION,
        }
        if partitions != required:
            raise ValueError("manifest requires development, holdout and untouched_validation")
        self._validate_review_blinding()
        self._validate_chronology()
        expected_id = stable_id(
            "caldata",
            {
                "pattern_family": self.pattern_family,
                "pattern_type": self.pattern_type,
                "manifest_version": self.manifest_version,
                "datasets": tuple(sorted(self.datasets, key=lambda item: item.dataset_id)),
            },
        )
        if self.manifest_id and self.manifest_id != expected_id:
            raise ValueError("manifest_id does not match canonical dataset manifest")
        object.__setattr__(self, "manifest_id", expected_id)

    def _validate_review_blinding(self) -> None:
        development = self.partition(CalibrationPartition.DEVELOPMENT)
        sealed = self.partition(CalibrationPartition.HOLDOUT) + self.partition(
            CalibrationPartition.UNTOUCHED_VALIDATION
        )
        if any(item.review_status is not DatasetReviewStatus.COMPLETED for item in development):
            raise ValueError("development labels must be reviewed before parameter freeze")
        if any(item.review_status is not DatasetReviewStatus.SEALED for item in sealed):
            raise ValueError("holdout and untouched validation labels must remain sealed at freeze")

    def _validate_chronology(self) -> None:
        development = self.partition(CalibrationPartition.DEVELOPMENT)
        holdout = self.partition(CalibrationPartition.HOLDOUT)
        untouched = self.partition(CalibrationPartition.UNTOUCHED_VALIDATION)
        if not (
            max(item.date_range[1] for item in development)
            < min(item.date_range[0] for item in holdout)
            <= max(item.date_range[1] for item in holdout)
            < min(item.date_range[0] for item in untouched)
        ):
            raise ValueError(
                "dataset windows must be chronological: "
                "development < holdout < untouched_validation"
            )

    @property
    def manifest_hash(self) -> str:
        return stable_hash(self)

    @property
    def market(self) -> str:
        return self.datasets[0].market

    @property
    def economic_asset_class(self) -> str:
        return self.datasets[0].economic_asset_class

    @property
    def timeframe(self) -> str:
        return self.datasets[0].timeframe

    def partition(self, partition: CalibrationPartition) -> tuple[CalibrationDataset, ...]:
        return tuple(item for item in self.datasets if item.partition is partition)

    def partition_hash(self, partition: CalibrationPartition) -> str:
        return stable_hash(
            {
                "manifest_id": self.manifest_id,
                "partition": partition,
                "datasets": self.partition(partition),
            }
        )

    def coverage_gaps(self) -> tuple[str, ...]:
        assets = {item.asset_coverage for item in self.datasets}
        regimes = {regime for item in self.datasets for regime in item.market_regimes}
        edge_cases = {edge for item in self.datasets for edge in item.edge_cases}
        required_assets = REQUIRED_ASSET_COVERAGE_BY_CLASS[self.economic_asset_class]
        required_edge_cases = REQUIRED_MARKET_EDGE_CASES_BY_CLASS[
            self.economic_asset_class
        ]
        gaps = [
            f"asset:{item.value}"
            for item in sorted(required_assets - assets, key=lambda item: item.value)
        ]
        gaps.extend(
            f"regime:{item.value}"
            for item in sorted(REQUIRED_MARKET_REGIMES - regimes, key=lambda item: item.value)
        )
        gaps.extend(
            f"edge_case:{item.value}"
            for item in sorted(required_edge_cases - edge_cases, key=lambda item: item.value)
        )
        return tuple(gaps)
