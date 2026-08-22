"""Deterministic US Stock/ETF Double Bottom calibration-process pilot.

The pilot validates bullish-reversal definition consistency, the causal volume
hard gate, immutable parameter freeze, and chronological out-of-sample workflow.
It does not optimize returns, identify buy points, or promote production use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..core import CorePatternBar, PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..detectors import (
    DetectorFramework,
    DoubleBottomDetector,
    DoubleReversalDirectionConfirmation,
    DoubleReversalInvalidation,
    DoubleReversalStructureConfirmation,
)
from ..detectors.contracts import ConfirmationState, PatternResult
from ..detectors.framework import InsufficientPatternHistory
from ..indicators import TalibIndicatorLayer
from .datasets import (
    AssetCoverage,
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationPartition,
    DatasetReviewStatus,
    MarketEdgeCase,
    MarketRegime,
    PatternReviewLabel,
)
from .registry import CalibrationKey, CalibrationRegistry, DetectorParameterSet
from .validation import (
    CalibrationAttemptRecord,
    CalibrationValidationFramework,
    FrozenCalibrationVersion,
    PatternSampleReview,
    PatternValidationEvaluation,
    PatternValidationReport,
    PromotionAssessment,
)


DOUBLE_BOTTOM_PILOT_DATASET_VERSION = "wp-us-double-bottom-pilot-dataset-v1"
DOUBLE_BOTTOM_PILOT_CALIBRATION_VERSION = (
    "wp-us-double-bottom-pilot-calibration-v1"
)
DOUBLE_BOTTOM_PILOT_SOURCE_PROVIDER = (
    "WEALTHPILOT_DETERMINISTIC_DOUBLE_BOTTOM_PILOT_V1"
)
DOUBLE_BOTTOM_PILOT_ADJUSTMENT_POLICY = (
    "SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT"
)
DOUBLE_BOTTOM_PILOT_CALENDAR_VERSION = "WP_US_WEEKDAY_PILOT_CALENDAR_V1"

Row = tuple[float, float, float, float, float]


class DoubleBottomSampleKind(str, Enum):
    CLEAN = "clean_double_bottom"
    SINGLE_TROUGH = "single_trough"
    ASYMMETRIC_TROUGHS = "asymmetric_double_trough"
    TROUGHS_TOO_CLOSE = "troughs_too_close"
    TROUGHS_TOO_FAR = "troughs_too_far_apart"
    SHALLOW_REACTION = "intervening_reaction_too_shallow"
    WEAK_DOWNTREND = "weak_or_missing_preceding_downtrend"
    UNCLEAR_NECKLINE = "unclear_neckline"
    TREND_CONTINUATION = "trend_continuation_mistaken_as_reversal"
    PRE_CONFIRMATION_INVALIDATED = "structure_invalidated_before_neckline_breakout"
    BREAKOUT_SUFFICIENT_VOLUME = "neckline_breakout_sufficient_relative_volume"
    BREAKOUT_INSUFFICIENT_VOLUME = "neckline_breakout_insufficient_relative_volume"
    DIRECTION_PENDING = "neckline_not_broken_direction_pending"
    POST_CONFIRMATION_INVALIDATED = "post_confirmation_neckline_failure"
    INSUFFICIENT_PIVOTS = "insufficient_pivots"
    INSUFFICIENT_HISTORY = "insufficient_history"


class DoubleBottomExpectedState(str, Enum):
    STRUCTURE_CONFIRMED_DIRECTION_PENDING = "structure_confirmed_direction_pending"
    STRUCTURE_AND_DIRECTION_CONFIRMED = "structure_and_direction_confirmed"
    VOLUME_GATE_BLOCKED = "price_breakout_volume_gate_blocked"
    PRE_CONFIRMATION_INVALIDATED = "pre_confirmation_invalidated"
    POST_CONFIRMATION_INVALIDATED = "post_confirmation_invalidated"
    NOT_CONFIRMED = "not_confirmed"
    HISTORY_BLOCKED = "history_blocked"


REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS = frozenset(DoubleBottomSampleKind)


def _base_values() -> list[Row]:
    return [
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (108.0, 110.0, 107.0, 108.0, 100.0),
        (106.0, 107.0, 105.0, 106.0, 100.0),
        (100.0, 101.0, 99.0, 100.0, 100.0),
        (94.0, 95.0, 93.0, 94.0, 100.0),
        (92.0, 93.0, 90.0, 92.0, 100.0),
        (94.0, 95.0, 93.0, 94.0, 100.0),
        (97.0, 98.0, 96.0, 97.0, 100.0),
        (98.5, 99.5, 97.5, 98.5, 100.0),
        (98.0, 100.0, 97.0, 98.0, 100.0),
        (97.0, 98.0, 96.0, 97.0, 100.0),
        (94.0, 95.0, 93.0, 94.0, 100.0),
        (92.0, 93.0, 91.5, 92.0, 100.0),
        (93.0, 94.0, 91.0, 93.0, 100.0),
        (94.0, 95.0, 93.0, 94.0, 100.0),
    ]


def _values_for(kind: DoubleBottomSampleKind) -> list[Row]:
    values = _base_values()
    if kind is DoubleBottomSampleKind.SINGLE_TROUGH:
        return values[:11] + [(95.0, 96.0, 94.0, 95.0, 100.0)] * 4
    if kind is DoubleBottomSampleKind.ASYMMETRIC_TROUGHS:
        values[13] = (77.0, 78.0, 75.0, 77.0, 100.0)
        return values
    if kind is DoubleBottomSampleKind.TROUGHS_TOO_CLOSE:
        return values[:6] + [
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (98.0, 100.0, 97.0, 98.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (93.0, 94.0, 91.0, 93.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
        ] + [(95.0, 96.0, 94.0, 95.0, 100.0)] * 4
    if kind is DoubleBottomSampleKind.TROUGHS_TOO_FAR:
        return values[:11] + [
            (96.5, 97.5, 95.5, 96.5, 100.0),
            (96.0, 97.0, 95.0, 96.0, 100.0),
            (95.5, 96.5, 94.5, 95.5, 100.0),
            (95.0, 96.0, 94.0, 95.0, 100.0),
        ] + values[11:]
    if kind in {
        DoubleBottomSampleKind.SHALLOW_REACTION,
        DoubleBottomSampleKind.UNCLEAR_NECKLINE,
    }:
        values[6:11] = [
            (90.5, 90.8, 90.2, 90.5, 100.0),
            (90.7, 91.0, 90.4, 90.7, 100.0),
            (90.9, 91.1, 90.6, 90.9, 100.0),
            (91.0, 91.2, 90.7, 91.0, 100.0),
            (90.9, 91.1, 90.6, 90.9, 100.0),
        ]
        return values
    if kind is DoubleBottomSampleKind.WEAK_DOWNTREND:
        values[:5] = [
            (90.5, 90.8, 90.3, 90.5, 100.0),
            (90.8, 91.0, 90.6, 90.8, 100.0),
            (90.6, 90.9, 90.4, 90.6, 100.0),
            (90.5, 90.8, 90.3, 90.5, 100.0),
            (90.3, 90.6, 90.1, 90.3, 100.0),
        ]
        return values
    if kind in {
        DoubleBottomSampleKind.TREND_CONTINUATION,
        DoubleBottomSampleKind.INSUFFICIENT_PIVOTS,
    }:
        return [
            (110.0 - index, 111.0 - index, 109.0 - index, 110.0 - index, 100.0)
            for index in range(15)
        ]
    if kind is DoubleBottomSampleKind.PRE_CONFIRMATION_INVALIDATED:
        return values + [(89.5, 90.0, 88.0, 89.0, 100.0)]
    if kind is DoubleBottomSampleKind.BREAKOUT_SUFFICIENT_VOLUME:
        return values + [(101.0, 103.0, 100.0, 102.0, 150.0)]
    if kind is DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME:
        return values + [(101.0, 103.0, 100.0, 102.0, 110.0)]
    if kind is DoubleBottomSampleKind.POST_CONFIRMATION_INVALIDATED:
        return values + [
            (101.0, 103.0, 100.0, 102.0, 150.0),
            (99.5, 100.5, 98.0, 99.0, 100.0),
        ]
    if kind is DoubleBottomSampleKind.INSUFFICIENT_HISTORY:
        return values[:14]
    return values


@dataclass(frozen=True)
class DoubleBottomPilotSample:
    sample_name: str
    instrument: str
    con_id: int
    isin: str
    economic_asset_class: str
    asset_coverage: AssetCoverage
    partition: CalibrationPartition
    start_date: date
    market_regimes: tuple[MarketRegime, ...]
    market_edge_cases: tuple[MarketEdgeCase, ...]
    sample_kind: DoubleBottomSampleKind
    label: PatternReviewLabel
    expected_state: DoubleBottomExpectedState
    reviewer_ids: tuple[str, ...] = ("stage1d6-volume-fixture-reviewer",)

    def __post_init__(self) -> None:
        positive_states = {
            DoubleBottomExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING,
            DoubleBottomExpectedState.STRUCTURE_AND_DIRECTION_CONFIRMED,
            DoubleBottomExpectedState.VOLUME_GATE_BLOCKED,
            DoubleBottomExpectedState.PRE_CONFIRMATION_INVALIDATED,
            DoubleBottomExpectedState.POST_CONFIRMATION_INVALIDATED,
        }
        if self.label is PatternReviewLabel.POSITIVE and self.expected_state not in (
            positive_states
        ):
            raise ValueError("positive Double Bottom samples require a valid structure")
        if self.label is PatternReviewLabel.NEGATIVE and self.expected_state not in {
            DoubleBottomExpectedState.NOT_CONFIRMED,
            DoubleBottomExpectedState.HISTORY_BLOCKED,
        }:
            raise ValueError("negative Double Bottom samples must fail closed")
        if self.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }:
            raise ValueError("the frozen Pilot contains no unresolved labels")
        if self.economic_asset_class not in {"EQUITY", "FIXED_INCOME"}:
            raise ValueError(
                "Double Bottom Pilot supports EQUITY and FIXED_INCOME only"
            )

    @property
    def sample_id(self) -> str:
        return stable_id(
            "dbsample",
            {
                "dataset_version": DOUBLE_BOTTOM_PILOT_DATASET_VERSION,
                "sample_name": self.sample_name,
                "instrument": self.instrument,
                "economic_asset_class": self.economic_asset_class,
                "partition": self.partition,
                "start_date": self.start_date,
                "sample_kind": self.sample_kind,
                "label": self.label,
                "expected_state": self.expected_state,
            },
        )

    @property
    def evaluation_ordinal(self) -> int:
        return len(_values_for(self.sample_kind)) - 1

    def core_input(self) -> PatternCoreInput:
        values = _values_for(self.sample_kind)
        sessions = _weekday_sessions(self.start_date, len(values))
        bars = tuple(
            CorePatternBar(
                session_date=session,
                session_ordinal=ordinal,
                available_from=session,
                open=row[0],
                high=row[1],
                low=row[2],
                close=row[3],
                volume=row[4],
                bar_id=stable_id(
                    "bar",
                    {
                        "sample_id": self.sample_id,
                        "session": session,
                        "ordinal": ordinal,
                        "row": row,
                    },
                ),
            )
            for ordinal, (session, row) in enumerate(zip(sessions, values))
        )
        source_hash = stable_hash(
            {
                "provider": DOUBLE_BOTTOM_PILOT_SOURCE_PROVIDER,
                "sample_id": self.sample_id,
                "bars": bars,
            }
        )
        return PatternCoreInput(
            instrument_id=f"PILOT:{self.instrument}:{self.sample_id}",
            con_id=self.con_id,
            isin=self.isin,
            symbol=self.instrument,
            market="US",
            currency="USD",
            timezone="America/New_York",
            timeframe="1d",
            adjustment_policy=DOUBLE_BOTTOM_PILOT_ADJUSTMENT_POLICY,
            calendar_version=DOUBLE_BOTTOM_PILOT_CALENDAR_VERSION,
            last_closed_session=bars[-1].session_date,
            source_bar_hash=source_hash,
            dataset_version=source_hash,
            bars=bars,
        )

    def dataset(self) -> CalibrationDataset:
        core_input = self.core_input()
        return CalibrationDataset(
            instrument=self.instrument,
            market="US",
            economic_asset_class=self.economic_asset_class,
            timeframe="1d",
            date_range=(
                core_input.bars[0].session_date,
                core_input.bars[-1].session_date,
            ),
            source_provider=DOUBLE_BOTTOM_PILOT_SOURCE_PROVIDER,
            source_bar_hash=core_input.source_bar_hash,
            adjustment_policy=DOUBLE_BOTTOM_PILOT_ADJUSTMENT_POLICY,
            calendar_version=DOUBLE_BOTTOM_PILOT_CALENDAR_VERSION,
            label=(
                self.label
                if self.partition is CalibrationPartition.DEVELOPMENT
                else None
            ),
            partition=self.partition,
            review_status=(
                DatasetReviewStatus.COMPLETED
                if self.partition is CalibrationPartition.DEVELOPMENT
                else DatasetReviewStatus.SEALED
            ),
            asset_coverage=self.asset_coverage,
            market_regimes=self.market_regimes,
            edge_cases=self.market_edge_cases,
        )


@dataclass(frozen=True)
class DoubleBottomCalibrationDatasetManifest:
    dataset_version: str
    samples: tuple[DoubleBottomPilotSample, ...]
    equity: CalibrationDatasetManifest
    fixed_income: CalibrationDatasetManifest

    def __post_init__(self) -> None:
        if self.dataset_version != DOUBLE_BOTTOM_PILOT_DATASET_VERSION:
            raise ValueError("Double Bottom Pilot dataset version is immutable")
        if {item.sample_kind for item in self.samples} != (
            REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS
        ):
            raise ValueError("Double Bottom Pilot must cover every requested reversal case")
        for asset_class in ("EQUITY", "FIXED_INCOME"):
            development_kinds = {
                item.sample_kind
                for item in self.samples
                if item.economic_asset_class == asset_class
                and item.partition is CalibrationPartition.DEVELOPMENT
            }
            if development_kinds != REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS:
                raise ValueError(
                    "each Development partition must cover every reversal case"
                )
        for manifest in (self.equity, self.fixed_income):
            if manifest.pattern_family != "reversal" or (
                manifest.pattern_type != "double_bottom"
            ):
                raise ValueError("Pilot manifests must bind only to Double Bottom")
            if manifest.coverage_gaps():
                raise ValueError(
                    "Double Bottom Pilot manifest coverage is incomplete: "
                    f"{manifest.coverage_gaps()}"
                )
        expected_ids = {item.dataset().dataset_id for item in self.samples}
        actual_ids = {
            item.dataset_id
            for manifest in (self.equity, self.fixed_income)
            for item in manifest.datasets
        }
        if expected_ids != actual_ids:
            raise ValueError("Pilot samples and exact manifests are not aligned")

    @property
    def manifest_hash(self) -> str:
        return stable_hash(
            {
                "dataset_version": self.dataset_version,
                "equity_manifest_hash": self.equity.manifest_hash,
                "fixed_income_manifest_hash": self.fixed_income.manifest_hash,
                "sample_ids": tuple(item.sample_id for item in self.samples),
            }
        )

    def exact_manifest(self, economic_asset_class: str) -> CalibrationDatasetManifest:
        normalized = economic_asset_class.strip().upper()
        if normalized == "EQUITY":
            return self.equity
        if normalized == "FIXED_INCOME":
            return self.fixed_income
        raise KeyError(f"unsupported Pilot asset class: {economic_asset_class}")

    def partition_samples(
        self,
        economic_asset_class: str,
        partition: CalibrationPartition,
    ) -> tuple[DoubleBottomPilotSample, ...]:
        normalized = economic_asset_class.strip().upper()
        return tuple(
            item
            for item in self.samples
            if item.economic_asset_class == normalized and item.partition is partition
        )


@dataclass(frozen=True)
class DoubleBottomSampleOutcome:
    dataset_id: str
    sample_name: str
    partition: CalibrationPartition
    label: PatternReviewLabel
    sample_kind: DoubleBottomSampleKind
    expected_state: DoubleBottomExpectedState
    candidate_count: int
    structure_confirmed: bool
    direction_pending: bool
    direction_confirmed: bool
    volume_gate_blocked: bool
    confirmation_volume_ratio: float | None
    pre_confirmation_invalidated: bool
    post_confirmation_invalidated: bool
    history_blocked: bool
    definition_conforms: bool
    false_positive: bool
    false_negative: bool
    detector_result_hash: str


@dataclass(frozen=True)
class DoubleBottomParameterAttemptResult:
    attempt: CalibrationAttemptRecord
    parameters: DetectorParameterSet
    outcomes: tuple[DoubleBottomSampleOutcome, ...]

    @property
    def definition_pass_count(self) -> int:
        return sum(item.definition_conforms for item in self.outcomes)

    @property
    def structure_only_confirmed_count(self) -> int:
        return sum(
            item.structure_confirmed
            and item.direction_pending
            and not item.direction_confirmed
            and not item.pre_confirmation_invalidated
            for item in self.outcomes
        )

    @property
    def direction_pending_count(self) -> int:
        return sum(item.direction_pending for item in self.outcomes)

    @property
    def direction_confirmed_count(self) -> int:
        return sum(item.direction_confirmed for item in self.outcomes)

    @property
    def volume_gate_blocked_count(self) -> int:
        return sum(item.volume_gate_blocked for item in self.outcomes)

    @property
    def pre_confirmation_invalidation_count(self) -> int:
        return sum(item.pre_confirmation_invalidated for item in self.outcomes)

    @property
    def post_confirmation_invalidation_count(self) -> int:
        return sum(item.post_confirmation_invalidated for item in self.outcomes)


@dataclass(frozen=True)
class DoubleBottomClassCalibrationResult:
    economic_asset_class: str
    manifest: CalibrationDatasetManifest
    parameter_attempts: tuple[DoubleBottomParameterAttemptResult, ...]
    frozen_version: FrozenCalibrationVersion
    holdout_evaluation: PatternValidationEvaluation
    untouched_evaluation: PatternValidationEvaluation
    promotion_assessment: PromotionAssessment
    validation_report: PatternValidationReport


@dataclass(frozen=True)
class DoubleBottomCalibrationPilotResult:
    dataset_manifest: DoubleBottomCalibrationDatasetManifest
    equity: DoubleBottomClassCalibrationResult
    fixed_income: DoubleBottomClassCalibrationResult

    @property
    def result_hash(self) -> str:
        return stable_hash(self)


def _weekday_sessions(start: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    current = start
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return tuple(sessions)


def _sample(
    prefix: str,
    instrument: str,
    con_id: int,
    economic_asset_class: str,
    asset_coverage: AssetCoverage,
    partition: CalibrationPartition,
    start_date: date,
    regime: MarketRegime,
    edge_cases: tuple[MarketEdgeCase, ...],
    kind: DoubleBottomSampleKind,
) -> DoubleBottomPilotSample:
    if kind in {
        DoubleBottomSampleKind.CLEAN,
        DoubleBottomSampleKind.DIRECTION_PENDING,
    }:
        label = PatternReviewLabel.POSITIVE
        expected = DoubleBottomExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING
    elif kind is DoubleBottomSampleKind.BREAKOUT_SUFFICIENT_VOLUME:
        label = PatternReviewLabel.POSITIVE
        expected = DoubleBottomExpectedState.STRUCTURE_AND_DIRECTION_CONFIRMED
    elif kind is DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME:
        label = PatternReviewLabel.POSITIVE
        expected = DoubleBottomExpectedState.VOLUME_GATE_BLOCKED
    elif kind is DoubleBottomSampleKind.PRE_CONFIRMATION_INVALIDATED:
        label = PatternReviewLabel.POSITIVE
        expected = DoubleBottomExpectedState.PRE_CONFIRMATION_INVALIDATED
    elif kind is DoubleBottomSampleKind.POST_CONFIRMATION_INVALIDATED:
        label = PatternReviewLabel.POSITIVE
        expected = DoubleBottomExpectedState.POST_CONFIRMATION_INVALIDATED
    else:
        label = PatternReviewLabel.NEGATIVE
        expected = (
            DoubleBottomExpectedState.HISTORY_BLOCKED
            if kind is DoubleBottomSampleKind.INSUFFICIENT_HISTORY
            else DoubleBottomExpectedState.NOT_CONFIRMED
        )
    return DoubleBottomPilotSample(
        sample_name=f"{prefix}-{kind.value}",
        instrument=instrument,
        con_id=con_id,
        isin=f"PILOT-{instrument}-{prefix}-{kind.value}",
        economic_asset_class=economic_asset_class,
        asset_coverage=asset_coverage,
        partition=partition,
        start_date=start_date,
        market_regimes=(regime,),
        market_edge_cases=edge_cases,
        sample_kind=kind,
        label=label,
        expected_state=expected,
    )


def build_double_bottom_pilot_samples() -> tuple[DoubleBottomPilotSample, ...]:
    """Return the frozen, definition-labeled Stage 1D-6 sample catalog."""

    kinds = tuple(DoubleBottomSampleKind)
    regimes = tuple(MarketRegime)
    equity_edges = (
        (MarketEdgeCase.SPLIT,),
        (MarketEdgeCase.DIVIDEND,),
        (MarketEdgeCase.EARNINGS_GAP,),
        (MarketEdgeCase.HOLIDAY,),
        (MarketEdgeCase.HALF_DAY,),
        (MarketEdgeCase.OVERNIGHT_GAP,),
        (MarketEdgeCase.LOW_LIQUIDITY,),
    )
    fixed_edges = (
        (MarketEdgeCase.SPLIT,),
        (MarketEdgeCase.DIVIDEND,),
        (MarketEdgeCase.OVERNIGHT_GAP,),
        (MarketEdgeCase.HOLIDAY,),
        (MarketEdgeCase.HALF_DAY,),
        (MarketEdgeCase.LOW_LIQUIDITY,),
    )
    catalog = (
        (
            "eq",
            "EQUITY",
            ("AAPL", "SPY", "XLK"),
            (8101, 8102, 8103),
            (
                AssetCoverage.COMMON_STOCK,
                AssetCoverage.BROAD_MARKET_ETF,
                AssetCoverage.SECTOR_ETF,
            ),
            equity_edges,
        ),
        (
            "fi",
            "FIXED_INCOME",
            ("AGG", "TLT", "LQD"),
            (8201, 8202, 8203),
            (AssetCoverage.FIXED_INCOME_ETF,) * 3,
            fixed_edges,
        ),
    )
    samples: list[DoubleBottomPilotSample] = []
    for prefix, asset_class, instruments, con_ids, coverages, edges in catalog:
        for index, kind in enumerate(kinds):
            slot = index % len(instruments)
            samples.append(
                _sample(
                    f"{prefix}-dev",
                    instruments[slot],
                    con_ids[slot],
                    asset_class,
                    coverages[slot],
                    CalibrationPartition.DEVELOPMENT,
                    date(2010 + index // 2, 1 + (index % 2) * 6, 4),
                    regimes[index % len(regimes)],
                    edges[index] if index < len(edges) else (),
                    kind,
                )
            )
        later_specs = (
            (
                CalibrationPartition.HOLDOUT,
                date(2019, 1, 4),
                DoubleBottomSampleKind.CLEAN,
                MarketRegime.BEAR,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2019, 7, 4),
                DoubleBottomSampleKind.ASYMMETRIC_TROUGHS,
                MarketRegime.HIGH_VOLATILITY,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2020, 1, 4),
                DoubleBottomSampleKind.BREAKOUT_SUFFICIENT_VOLUME,
                MarketRegime.BULL,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2020, 7, 4),
                DoubleBottomSampleKind.TREND_CONTINUATION,
                MarketRegime.BEAR,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2022, 1, 4),
                DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME,
                MarketRegime.SIDEWAYS,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2022, 7, 4),
                DoubleBottomSampleKind.SHALLOW_REACTION,
                MarketRegime.LOW_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2023, 1, 4),
                DoubleBottomSampleKind.PRE_CONFIRMATION_INVALIDATED,
                MarketRegime.HIGH_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2023, 7, 4),
                DoubleBottomSampleKind.TROUGHS_TOO_CLOSE,
                MarketRegime.SIDEWAYS,
            ),
        )
        for index, (partition, start, kind, regime) in enumerate(later_specs):
            slot = index % len(instruments)
            samples.append(
                _sample(
                    f"{prefix}-{partition.value}",
                    instruments[slot],
                    con_ids[slot],
                    asset_class,
                    coverages[slot],
                    partition,
                    start,
                    regime,
                    (),
                    kind,
                )
            )
    return tuple(samples)


def build_double_bottom_calibration_dataset_manifest() -> (
    DoubleBottomCalibrationDatasetManifest
):
    samples = build_double_bottom_pilot_samples()

    def exact_manifest(asset_class: str) -> CalibrationDatasetManifest:
        return CalibrationDatasetManifest(
            pattern_family="reversal",
            pattern_type="double_bottom",
            manifest_version=(
                f"{DOUBLE_BOTTOM_PILOT_DATASET_VERSION}-{asset_class.lower()}"
            ),
            datasets=tuple(
                item.dataset()
                for item in samples
                if item.economic_asset_class == asset_class
            ),
        )

    return DoubleBottomCalibrationDatasetManifest(
        dataset_version=DOUBLE_BOTTOM_PILOT_DATASET_VERSION,
        samples=samples,
        equity=exact_manifest("EQUITY"),
        fixed_income=exact_manifest("FIXED_INCOME"),
    )


def _parameter_values(
    *,
    bottom_volume_ratio_minimum: float,
    calibration_stage: str,
) -> tuple[tuple[str, bool | int | float | str], ...]:
    return (
        ("boundary_tolerance_pct", 0.005),
        ("bottom_volume_ratio_minimum", bottom_volume_ratio_minimum),
        ("calibration_stage", calibration_stage),
        ("direction_break_margin_pct", 0.10),
        ("expiry_sessions", 20),
        ("extreme_similarity_max_ratio", 0.025),
        ("invalidation_buffer_pct", 0.10),
        ("maximum_structure_duration_sessions", 10),
        ("minimum_extreme_separation_sessions", 8),
        ("minimum_intervening_reaction_ratio", 0.03),
        ("minimum_preceding_trend_ratio", 0.05),
        ("neckline_tolerance_pct", 0.25),
        ("parameter_origin", "stage1d6_volume_fixture_pilot"),
        ("pattern_type_contract", "double_bottom"),
        ("pivot_left_window_bars", 1),
        ("pivot_minimum_bar_separation", 0),
        ("pivot_minimum_price_separation_pct", 0.0),
        ("pivot_plateau_tolerance_pct", 0.0),
        ("pivot_right_confirmation_bars", 1),
        ("source_pivot_count", 4),
        ("volume_average_sessions", 5),
        ("volume_role", "hard_confirmation_gate"),
    )


def build_double_bottom_pilot_parameters(
    economic_asset_class: str,
    *,
    attempt_number: int,
) -> DetectorParameterSet:
    asset_class = economic_asset_class.strip().upper()
    if asset_class not in {"EQUITY", "FIXED_INCOME"}:
        raise ValueError("Pilot supports EQUITY and FIXED_INCOME only")
    if attempt_number not in {1, 2}:
        raise ValueError("Pilot defines exactly two Development attempts")
    return DetectorParameterSet(
        key=CalibrationKey(
            market="US",
            economic_asset_class=asset_class,
            timeframe="1d",
            pattern_family="reversal",
            pattern_type="double_bottom",
            calibration_version=DOUBLE_BOTTOM_PILOT_CALIBRATION_VERSION,
        ),
        values=_parameter_values(
            bottom_volume_ratio_minimum=1.05 if attempt_number == 1 else 1.20,
            calibration_stage=(
                "development_exploration"
                if attempt_number == 1
                else "pilot_frozen_not_production"
            ),
        ),
        minimum_history_bars=15,
    )


def _confirmation_volume_ratio(run_results: tuple[PatternResult, ...]) -> float | None:
    for result in run_results:
        for fact in result.direction_confirmation.facts:
            if fact.code == "direction_confirmation_volume_ratio":
                return float(fact.value)
    return None


def _run_sample(
    sample: DoubleBottomPilotSample,
    parameters: DetectorParameterSet,
) -> DoubleBottomSampleOutcome:
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    try:
        run = framework.run(
            sample.core_input(),
            evaluation_session_ordinal=sample.evaluation_ordinal,
            calibration_key=parameters.key,
            detector=DoubleBottomDetector(),
            structure_confirmation=DoubleReversalStructureConfirmation(),
            direction_confirmation=DoubleReversalDirectionConfirmation(),
            invalidation=DoubleReversalInvalidation(),
        )
    except InsufficientPatternHistory as exc:
        history_blocked = True
        candidate_count = 0
        structure_confirmed = False
        direction_pending = False
        direction_confirmed = False
        volume_gate_blocked = False
        confirmation_volume_ratio = None
        pre_invalidated = False
        post_invalidated = False
        result_hash = stable_hash(
            {
                "sample_id": sample.sample_id,
                "parameters_hash": parameters.parameters_hash,
                "failure": type(exc).__name__,
                "message": str(exc),
            }
        )
    else:
        history_blocked = False
        candidate_count = len(run.results)
        structure_confirmed = any(
            item.structure_confirmation.state is ConfirmationState.CONFIRMED
            for item in run.results
        )
        direction_pending = any(
            item.direction_confirmation.state is ConfirmationState.PENDING
            for item in run.results
        )
        direction_confirmed = any(
            item.direction_confirmation.state is ConfirmationState.CONFIRMED
            for item in run.results
        )
        volume_gate_blocked = (
            sample.sample_kind
            is DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME
            and structure_confirmed
            and direction_pending
            and not direction_confirmed
        )
        confirmation_volume_ratio = _confirmation_volume_ratio(run.results)
        pre_invalidated = any(
            item.status == "invalidated"
            and item.direction_confirmation.state is ConfirmationState.PENDING
            for item in run.results
        )
        post_invalidated = any(
            item.status == "invalidated"
            and item.direction_confirmation.state is ConfirmationState.CONFIRMED
            for item in run.results
        )
        result_hash = run.result_hash

    expected = sample.expected_state
    expected_positive = expected in {
        DoubleBottomExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING,
        DoubleBottomExpectedState.STRUCTURE_AND_DIRECTION_CONFIRMED,
        DoubleBottomExpectedState.VOLUME_GATE_BLOCKED,
        DoubleBottomExpectedState.PRE_CONFIRMATION_INVALIDATED,
        DoubleBottomExpectedState.POST_CONFIRMATION_INVALIDATED,
    }
    false_positive = not expected_positive and structure_confirmed
    false_negative = expected_positive and not structure_confirmed
    if expected is DoubleBottomExpectedState.HISTORY_BLOCKED:
        state_matches = history_blocked
    elif expected is DoubleBottomExpectedState.NOT_CONFIRMED:
        state_matches = not history_blocked and not structure_confirmed
    elif expected is DoubleBottomExpectedState.VOLUME_GATE_BLOCKED:
        state_matches = volume_gate_blocked
    elif expected is DoubleBottomExpectedState.PRE_CONFIRMATION_INVALIDATED:
        state_matches = structure_confirmed and direction_pending and pre_invalidated
    elif expected is DoubleBottomExpectedState.POST_CONFIRMATION_INVALIDATED:
        state_matches = structure_confirmed and direction_confirmed and post_invalidated
    elif expected is DoubleBottomExpectedState.STRUCTURE_AND_DIRECTION_CONFIRMED:
        state_matches = (
            structure_confirmed
            and direction_confirmed
            and confirmation_volume_ratio is not None
            and not pre_invalidated
            and not post_invalidated
        )
    else:
        state_matches = (
            structure_confirmed
            and direction_pending
            and not direction_confirmed
            and not pre_invalidated
            and not post_invalidated
        )
    return DoubleBottomSampleOutcome(
        dataset_id=sample.dataset().dataset_id,
        sample_name=sample.sample_name,
        partition=sample.partition,
        label=sample.label,
        sample_kind=sample.sample_kind,
        expected_state=sample.expected_state,
        candidate_count=candidate_count,
        structure_confirmed=structure_confirmed,
        direction_pending=direction_pending,
        direction_confirmed=direction_confirmed,
        volume_gate_blocked=volume_gate_blocked,
        confirmation_volume_ratio=confirmation_volume_ratio,
        pre_confirmation_invalidated=pre_invalidated,
        post_confirmation_invalidated=post_invalidated,
        history_blocked=history_blocked,
        definition_conforms=not false_positive and not false_negative and state_matches,
        false_positive=false_positive,
        false_negative=false_negative,
        detector_result_hash=result_hash,
    )


def _reviews(
    samples: tuple[DoubleBottomPilotSample, ...],
    outcomes: tuple[DoubleBottomSampleOutcome, ...],
    *,
    reviewed_on: date,
) -> tuple[PatternSampleReview, ...]:
    by_id = {item.dataset_id: item for item in outcomes}
    return tuple(
        PatternSampleReview(
            dataset_id=sample.dataset().dataset_id,
            partition=sample.partition,
            reviewer_ids=sample.reviewer_ids,
            label=sample.label,
            definition_conforms=by_id[sample.dataset().dataset_id].definition_conforms,
            false_positive=by_id[sample.dataset().dataset_id].false_positive,
            false_negative=by_id[sample.dataset().dataset_id].false_negative,
            boundary_ambiguous=False,
            notes=(
                f"Frozen fixture reversal/volume review for {sample.sample_kind.value}; "
                "future upside and financial outcomes were not considered."
            ),
            reviewed_on=reviewed_on,
        )
        for sample in samples
    )


def _evaluation(
    version: FrozenCalibrationVersion,
    manifest: CalibrationDatasetManifest,
    samples: tuple[DoubleBottomPilotSample, ...],
    parameters: DetectorParameterSet,
    *,
    partition: CalibrationPartition,
    completed_on: date,
) -> PatternValidationEvaluation:
    outcomes = tuple(_run_sample(item, parameters) for item in samples)
    return PatternValidationEvaluation(
        calibration_version_id=version.version_id,
        partition=partition,
        parameters_hash=version.parameters_hash,
        dataset_manifest_hash=version.dataset_manifest_hash,
        partition_hash=manifest.partition_hash(partition),
        reviews=_reviews(samples, outcomes, reviewed_on=completed_on),
        definition_review_pass=all(item.definition_conforms for item in outcomes),
        false_positive_review_pass=not any(item.false_positive for item in outcomes),
        false_negative_review_pass=not any(item.false_negative for item in outcomes),
        boundary_review_pass=all(
            item.label not in {
                PatternReviewLabel.AMBIGUOUS,
                PatternReviewLabel.REVIEW_DISAGREEMENT,
            }
            for item in samples
        ),
        human_review_pass=all(item.reviewer_ids for item in samples),
        failure_modes=tuple(
            f"{item.sample_name}:reversal_or_volume_definition_mismatch"
            for item in outcomes
            if not item.definition_conforms
        ),
        completed_on=completed_on,
    )


def _execute_asset_class(
    dataset_manifest: DoubleBottomCalibrationDatasetManifest,
    validation: CalibrationValidationFramework,
    economic_asset_class: str,
) -> DoubleBottomClassCalibrationResult:
    manifest = dataset_manifest.exact_manifest(economic_asset_class)
    development_samples = dataset_manifest.partition_samples(
        economic_asset_class,
        CalibrationPartition.DEVELOPMENT,
    )
    attempt_results: list[DoubleBottomParameterAttemptResult] = []
    attempt_records: list[CalibrationAttemptRecord] = []
    for attempt_number in (1, 2):
        parameters = build_double_bottom_pilot_parameters(
            economic_asset_class,
            attempt_number=attempt_number,
        )
        outcomes = tuple(_run_sample(item, parameters) for item in development_samples)
        attempt = CalibrationAttemptRecord(
            calibration_key=parameters.key,
            attempt_number=attempt_number,
            parameters_hash=parameters.parameters_hash,
            development_partition_hash=manifest.partition_hash(
                CalibrationPartition.DEVELOPMENT
            ),
            based_on_partitions=(CalibrationPartition.DEVELOPMENT,),
            development_review_ids=tuple(
                f"development-review:{item.dataset().dataset_id}"
                for item in development_samples
            ),
            change_reason=(
                "Test whether a 1.05 relative-volume gate admits a weak 1.10 breakout"
                if attempt_number == 1
                else "Raise the hard gate to 1.20 after the Development-only weak-volume "
                "direction false confirmation"
            ),
            attempted_on=date(2026, 8, 20 + attempt_number - 1),
        )
        attempt_records.append(attempt)
        attempt_results.append(
            DoubleBottomParameterAttemptResult(attempt, parameters, outcomes)
        )

    final_parameters = attempt_results[-1].parameters
    version = validation.register_version(
        final_parameters,
        manifest,
        tuple(attempt_records),
        frozen_on=date(2026, 8, 21),
    )
    holdout = _evaluation(
        version,
        manifest,
        dataset_manifest.partition_samples(
            economic_asset_class,
            CalibrationPartition.HOLDOUT,
        ),
        final_parameters,
        partition=CalibrationPartition.HOLDOUT,
        completed_on=date(2026, 8, 21),
    )
    validation.record_evaluation(holdout)
    untouched = _evaluation(
        version,
        manifest,
        dataset_manifest.partition_samples(
            economic_asset_class,
            CalibrationPartition.UNTOUCHED_VALIDATION,
        ),
        final_parameters,
        partition=CalibrationPartition.UNTOUCHED_VALIDATION,
        completed_on=date(2026, 8, 22),
    )
    validation.record_evaluation(untouched)
    return DoubleBottomClassCalibrationResult(
        economic_asset_class=economic_asset_class,
        manifest=manifest,
        parameter_attempts=tuple(attempt_results),
        frozen_version=version,
        holdout_evaluation=holdout,
        untouched_evaluation=untouched,
        promotion_assessment=validation.promotion_assessment(
            version.version_id,
            detector_pass=True,
        ),
        validation_report=validation.build_report(
            version.version_id,
            detector_pass=True,
        ),
    )


def execute_double_bottom_calibration_pilot() -> DoubleBottomCalibrationPilotResult:
    """Run the reproducible reversal/volume Pilot without external calls."""

    dataset_manifest = build_double_bottom_calibration_dataset_manifest()
    validation = CalibrationValidationFramework()
    return DoubleBottomCalibrationPilotResult(
        dataset_manifest=dataset_manifest,
        equity=_execute_asset_class(dataset_manifest, validation, "EQUITY"),
        fixed_income=_execute_asset_class(
            dataset_manifest,
            validation,
            "FIXED_INCOME",
        ),
    )
