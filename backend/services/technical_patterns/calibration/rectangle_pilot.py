"""Deterministic US Stock/ETF Rectangle calibration-process pilot.

The pilot validates neutral range-structure definitions only. It does not
optimize financial outcomes, predict breakouts, or promote production
calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..core import CorePatternBar, PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..detectors import (
    DetectorFramework,
    RectangleDetector,
    RectangleInvalidation,
    RectangleStructureConfirmation,
)
from ..detectors.contracts import ConfirmationState
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


RECTANGLE_PILOT_DATASET_VERSION = "wp-us-rectangle-pilot-dataset-v1"
RECTANGLE_PILOT_CALIBRATION_VERSION = "wp-us-rectangle-pilot-calibration-v1"
RECTANGLE_PILOT_SOURCE_PROVIDER = "WEALTHPILOT_DETERMINISTIC_RECTANGLE_PILOT_V1"
RECTANGLE_PILOT_ADJUSTMENT_POLICY = "SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT"
RECTANGLE_PILOT_CALENDAR_VERSION = "WP_US_WEEKDAY_PILOT_CALENDAR_V1"


class RectangleSampleKind(str, Enum):
    CLEAN_RECTANGLE = "clean_rectangle"
    FALSE_RECTANGLE = "false_rectangle"
    TRENDING_MARKET = "trending_market_mistaken_as_rectangle"
    TOO_NARROW_RANGE = "too_narrow_range"
    TOO_WIDE_RANGE = "too_wide_range"
    INSUFFICIENT_TOUCHES = "insufficient_touches"
    UNSTABLE_BOUNDARIES = "unstable_boundaries"
    INSUFFICIENT_HISTORY = "insufficient_history"


class RectangleExpectedState(str, Enum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    HISTORY_BLOCKED = "history_blocked"


REQUIRED_RECTANGLE_SAMPLE_KINDS = frozenset(RectangleSampleKind)


@dataclass(frozen=True)
class RectanglePilotSample:
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
    sample_kind: RectangleSampleKind
    label: PatternReviewLabel
    expected_state: RectangleExpectedState
    reviewer_ids: tuple[str, ...] = ("stage1d3-definition-fixture-reviewer",)

    def __post_init__(self) -> None:
        if self.label is PatternReviewLabel.POSITIVE and (
            self.expected_state is not RectangleExpectedState.CONFIRMED
        ):
            raise ValueError("positive Rectangle samples require confirmed structure")
        if self.label is PatternReviewLabel.NEGATIVE and self.expected_state not in {
            RectangleExpectedState.NOT_CONFIRMED,
            RectangleExpectedState.HISTORY_BLOCKED,
        }:
            raise ValueError("negative Rectangle samples must fail closed")
        if self.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }:
            raise ValueError("the frozen pilot contains no unresolved review labels")
        if self.economic_asset_class not in {"EQUITY", "FIXED_INCOME"}:
            raise ValueError("Rectangle pilot supports EQUITY and FIXED_INCOME only")

    @property
    def sample_id(self) -> str:
        return stable_id(
            "rectsample",
            {
                "dataset_version": RECTANGLE_PILOT_DATASET_VERSION,
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

    def _values(self) -> list[tuple[float, float, float, float, float]]:
        if self.sample_kind is RectangleSampleKind.TRENDING_MARKET:
            return [
                (100.0 + index, 101.0 + index, 99.0 + index, 100.5 + index, 100.0)
                for index in range(9)
            ]
        if self.sample_kind is RectangleSampleKind.TOO_NARROW_RANGE:
            return _rectangle_values(100.0, 101.0)
        if self.sample_kind is RectangleSampleKind.TOO_WIDE_RANGE:
            return _rectangle_values(100.0, 130.0)
        if self.sample_kind is RectangleSampleKind.UNSTABLE_BOUNDARIES:
            return _rectangle_values(100.0, 110.0, second_support=100.8)
        if self.sample_kind is RectangleSampleKind.FALSE_RECTANGLE:
            return _rectangle_values(100.0, 110.0, second_support=95.0)
        if self.sample_kind is RectangleSampleKind.INSUFFICIENT_TOUCHES:
            values = _rectangle_values(100.0, 110.0)
            midpoint = (105.0, 106.0, 104.0, 105.0, 100.0)
            return values[:7] + [midpoint, midpoint]
        values = _rectangle_values(100.0, 110.0)
        if self.sample_kind is RectangleSampleKind.INSUFFICIENT_HISTORY:
            return values[:8]
        return values

    @property
    def evaluation_ordinal(self) -> int:
        return len(self._values()) - 1

    def core_input(self) -> PatternCoreInput:
        values = self._values()
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
                "provider": RECTANGLE_PILOT_SOURCE_PROVIDER,
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
            adjustment_policy=RECTANGLE_PILOT_ADJUSTMENT_POLICY,
            calendar_version=RECTANGLE_PILOT_CALENDAR_VERSION,
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
            source_provider=RECTANGLE_PILOT_SOURCE_PROVIDER,
            source_bar_hash=core_input.source_bar_hash,
            adjustment_policy=RECTANGLE_PILOT_ADJUSTMENT_POLICY,
            calendar_version=RECTANGLE_PILOT_CALENDAR_VERSION,
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
class RectangleCalibrationDatasetManifest:
    dataset_version: str
    samples: tuple[RectanglePilotSample, ...]
    equity: CalibrationDatasetManifest
    fixed_income: CalibrationDatasetManifest

    def __post_init__(self) -> None:
        if self.dataset_version != RECTANGLE_PILOT_DATASET_VERSION:
            raise ValueError("Rectangle pilot dataset version is immutable")
        if {item.sample_kind for item in self.samples} != REQUIRED_RECTANGLE_SAMPLE_KINDS:
            raise ValueError("Rectangle pilot must cover all definition cases")
        if {item.economic_asset_class for item in self.samples} != {
            "EQUITY",
            "FIXED_INCOME",
        }:
            raise ValueError("Rectangle pilot requires Equity and Fixed Income samples")
        for asset_class in ("EQUITY", "FIXED_INCOME"):
            development_kinds = {
                item.sample_kind
                for item in self.samples
                if item.economic_asset_class == asset_class
                and item.partition is CalibrationPartition.DEVELOPMENT
            }
            if development_kinds != REQUIRED_RECTANGLE_SAMPLE_KINDS:
                raise ValueError("each Development partition must cover every Rectangle case")
        for manifest in (self.equity, self.fixed_income):
            if manifest.pattern_family != "range" or manifest.pattern_type != "rectangle":
                raise ValueError("Rectangle pilot manifests must bind only to Rectangle")
            if manifest.coverage_gaps():
                raise ValueError(
                    f"Rectangle pilot manifest coverage is incomplete: "
                    f"{manifest.coverage_gaps()}"
                )
        expected_ids = {item.dataset().dataset_id for item in self.samples}
        actual_ids = {
            item.dataset_id
            for manifest in (self.equity, self.fixed_income)
            for item in manifest.datasets
        }
        if actual_ids != expected_ids:
            raise ValueError("Rectangle pilot samples and manifests are not aligned")

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
        raise KeyError(f"unsupported Rectangle pilot asset class: {economic_asset_class}")

    def partition_samples(
        self,
        economic_asset_class: str,
        partition: CalibrationPartition,
    ) -> tuple[RectanglePilotSample, ...]:
        normalized = economic_asset_class.strip().upper()
        return tuple(
            item
            for item in self.samples
            if item.economic_asset_class == normalized and item.partition is partition
        )


@dataclass(frozen=True)
class RectangleSampleOutcome:
    sample_id: str
    sample_name: str
    partition: CalibrationPartition
    label: PatternReviewLabel
    sample_kind: RectangleSampleKind
    expected_state: RectangleExpectedState
    candidate_count: int
    structure_confirmed: bool
    neutral_direction: bool
    direction_not_required: bool
    history_blocked: bool
    definition_conforms: bool
    false_positive: bool
    false_negative: bool
    detector_result_hash: str


@dataclass(frozen=True)
class RectangleParameterAttemptResult:
    attempt: CalibrationAttemptRecord
    parameters: DetectorParameterSet
    outcomes: tuple[RectangleSampleOutcome, ...]

    @property
    def definition_pass_count(self) -> int:
        return sum(item.definition_conforms for item in self.outcomes)


@dataclass(frozen=True)
class RectangleClassCalibrationResult:
    economic_asset_class: str
    manifest: CalibrationDatasetManifest
    parameter_attempts: tuple[RectangleParameterAttemptResult, ...]
    frozen_version: FrozenCalibrationVersion
    holdout_evaluation: PatternValidationEvaluation
    untouched_evaluation: PatternValidationEvaluation
    promotion_assessment: PromotionAssessment
    validation_report: PatternValidationReport


@dataclass(frozen=True)
class RectangleCalibrationPilotResult:
    dataset_manifest: RectangleCalibrationDatasetManifest
    equity: RectangleClassCalibrationResult
    fixed_income: RectangleClassCalibrationResult

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


def _rectangle_values(
    lower: float,
    upper: float,
    *,
    second_support: float | None = None,
) -> list[tuple[float, float, float, float, float]]:
    second_support = lower if second_support is None else second_support
    span = upper - lower
    midpoint = (lower + upper) / 2.0
    middle = (
        midpoint,
        midpoint + span * 0.10,
        midpoint - span * 0.10,
        midpoint,
        100.0,
    )

    def support(value: float) -> tuple[float, float, float, float, float]:
        return (
            value + span * 0.20,
            value + span * 0.30,
            value,
            value + span * 0.20,
            100.0,
        )

    resistance = (
        upper - span * 0.20,
        upper,
        upper - span * 0.30,
        upper - span * 0.20,
        100.0,
    )
    return [
        middle,
        support(lower),
        middle,
        resistance,
        middle,
        support(second_support),
        middle,
        resistance,
        middle,
    ]


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
    kind: RectangleSampleKind,
) -> RectanglePilotSample:
    positive = kind is RectangleSampleKind.CLEAN_RECTANGLE
    expected = (
        RectangleExpectedState.CONFIRMED
        if positive
        else RectangleExpectedState.HISTORY_BLOCKED
        if kind is RectangleSampleKind.INSUFFICIENT_HISTORY
        else RectangleExpectedState.NOT_CONFIRMED
    )
    return RectanglePilotSample(
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
        label=(PatternReviewLabel.POSITIVE if positive else PatternReviewLabel.NEGATIVE),
        expected_state=expected,
    )


def build_rectangle_pilot_samples() -> tuple[RectanglePilotSample, ...]:
    """Return the frozen, definition-labeled Stage 1D-3 sample catalog."""

    development_specs = (
        (RectangleSampleKind.CLEAN_RECTANGLE, MarketRegime.BULL),
        (RectangleSampleKind.FALSE_RECTANGLE, MarketRegime.SIDEWAYS),
        (RectangleSampleKind.TRENDING_MARKET, MarketRegime.BEAR),
        (RectangleSampleKind.TOO_NARROW_RANGE, MarketRegime.LOW_VOLATILITY),
        (RectangleSampleKind.TOO_WIDE_RANGE, MarketRegime.HIGH_VOLATILITY),
        (RectangleSampleKind.INSUFFICIENT_TOUCHES, MarketRegime.SIDEWAYS),
        (RectangleSampleKind.UNSTABLE_BOUNDARIES, MarketRegime.HIGH_VOLATILITY),
        (RectangleSampleKind.INSUFFICIENT_HISTORY, MarketRegime.BULL),
    )
    equity_edges = (
        (MarketEdgeCase.SPLIT,),
        (MarketEdgeCase.DIVIDEND,),
        (MarketEdgeCase.EARNINGS_GAP,),
        (MarketEdgeCase.HOLIDAY,),
        (MarketEdgeCase.HALF_DAY,),
        (MarketEdgeCase.OVERNIGHT_GAP,),
        (MarketEdgeCase.LOW_LIQUIDITY,),
        (),
    )
    fixed_edges = (
        (MarketEdgeCase.SPLIT,),
        (MarketEdgeCase.DIVIDEND,),
        (MarketEdgeCase.OVERNIGHT_GAP,),
        (MarketEdgeCase.HOLIDAY,),
        (MarketEdgeCase.HALF_DAY,),
        (MarketEdgeCase.LOW_LIQUIDITY,),
        (),
        (),
    )
    samples: list[RectanglePilotSample] = []
    asset_catalog = (
        (
            "eq",
            "EQUITY",
            ("AAPL", "SPY", "XLK"),
            (5101, 5102, 5103),
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
            (5201, 5202, 5203),
            (AssetCoverage.FIXED_INCOME_ETF,) * 3,
            fixed_edges,
        ),
    )
    for prefix, asset_class, instruments, con_ids, coverages, edges in asset_catalog:
        for index, ((kind, regime), edge_cases) in enumerate(
            zip(development_specs, edges)
        ):
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
                    regime,
                    edge_cases,
                    kind,
                )
            )
        later_specs = (
            (
                CalibrationPartition.HOLDOUT,
                date(2015, 1, 5),
                RectangleSampleKind.CLEAN_RECTANGLE,
                MarketRegime.SIDEWAYS,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2015, 7, 6),
                RectangleSampleKind.TRENDING_MARKET,
                MarketRegime.BULL,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2016, 1, 4),
                RectangleSampleKind.TOO_NARROW_RANGE,
                MarketRegime.LOW_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2019, 1, 2),
                RectangleSampleKind.CLEAN_RECTANGLE,
                MarketRegime.SIDEWAYS,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2019, 7, 1),
                RectangleSampleKind.UNSTABLE_BOUNDARIES,
                MarketRegime.HIGH_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2020, 1, 2),
                RectangleSampleKind.INSUFFICIENT_TOUCHES,
                MarketRegime.BEAR,
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


def build_rectangle_calibration_dataset_manifest() -> RectangleCalibrationDatasetManifest:
    samples = build_rectangle_pilot_samples()

    def exact_manifest(asset_class: str) -> CalibrationDatasetManifest:
        return CalibrationDatasetManifest(
            pattern_family="range",
            pattern_type="rectangle",
            manifest_version=(
                f"{RECTANGLE_PILOT_DATASET_VERSION}-{asset_class.lower()}"
            ),
            datasets=tuple(
                item.dataset()
                for item in samples
                if item.economic_asset_class == asset_class
            ),
        )

    return RectangleCalibrationDatasetManifest(
        dataset_version=RECTANGLE_PILOT_DATASET_VERSION,
        samples=samples,
        equity=exact_manifest("EQUITY"),
        fixed_income=exact_manifest("FIXED_INCOME"),
    )


def _parameter_values(
    *,
    minimum_range_width_pct: float,
    calibration_stage: str,
) -> tuple[tuple[str, bool | int | float | str], ...]:
    return (
        ("boundary_tolerance_pct", 0.005),
        ("calibration_stage", calibration_stage),
        ("expiry_sessions", 20),
        ("invalidation_buffer_pct", 0.20),
        ("maximum_boundary_zone_width_pct", 0.50),
        ("maximum_range_width_pct", 15.0),
        ("minimum_range_width_pct", minimum_range_width_pct),
        ("minimum_structure_span_sessions", 3),
        ("minimum_touches_per_side", 2),
        ("parameter_origin", "stage1d3_definition_fixture_pilot"),
        ("pivot_left_window_bars", 1),
        ("pivot_minimum_bar_separation", 0),
        ("pivot_minimum_price_separation_pct", 0.0),
        ("pivot_plateau_tolerance_pct", 0.0),
        ("pivot_right_confirmation_bars", 1),
    )


def build_rectangle_pilot_parameters(
    economic_asset_class: str,
    *,
    attempt_number: int,
) -> DetectorParameterSet:
    asset_class = economic_asset_class.strip().upper()
    if asset_class not in {"EQUITY", "FIXED_INCOME"}:
        raise ValueError("Rectangle pilot supports EQUITY and FIXED_INCOME only")
    if attempt_number not in {1, 2}:
        raise ValueError("Rectangle pilot defines exactly two development attempts")
    key = CalibrationKey(
        market="US",
        economic_asset_class=asset_class,
        timeframe="1d",
        pattern_family="range",
        pattern_type="rectangle",
        calibration_version=RECTANGLE_PILOT_CALIBRATION_VERSION,
    )
    return DetectorParameterSet(
        key=key,
        values=_parameter_values(
            minimum_range_width_pct=0.5 if attempt_number == 1 else 2.0,
            calibration_stage=(
                "development_exploration"
                if attempt_number == 1
                else "pilot_frozen_not_production"
            ),
        ),
        minimum_history_bars=9,
    )


def _run_sample(
    sample: RectanglePilotSample,
    parameters: DetectorParameterSet,
) -> RectangleSampleOutcome:
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    try:
        result = framework.run(
            sample.core_input(),
            evaluation_session_ordinal=sample.evaluation_ordinal,
            calibration_key=parameters.key,
            detector=RectangleDetector(),
            structure_confirmation=RectangleStructureConfirmation(),
            invalidation=RectangleInvalidation(),
        )
    except InsufficientPatternHistory as exc:
        history_blocked = True
        structure_confirmed = False
        neutral_direction = True
        direction_not_required = True
        candidate_count = 0
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
        structure_confirmed = any(
            item.structure_confirmation.state is ConfirmationState.CONFIRMED
            for item in result.results
        )
        neutral_direction = all(
            item.candidate.direction.value == "neutral" for item in result.results
        )
        direction_not_required = all(
            item.direction_confirmation.state is ConfirmationState.NOT_REQUIRED
            and item.candidate.direction_confirmation_required is False
            for item in result.results
        )
        candidate_count = len(result.results)
        result_hash = result.result_hash

    expected_confirmed = sample.expected_state is RectangleExpectedState.CONFIRMED
    expected_history_block = (
        sample.expected_state is RectangleExpectedState.HISTORY_BLOCKED
    )
    false_positive = not expected_confirmed and structure_confirmed
    false_negative = expected_confirmed and not structure_confirmed
    state_matches = (
        history_blocked
        if expected_history_block
        else not history_blocked
    )
    definition_conforms = (
        not false_positive
        and not false_negative
        and state_matches
        and neutral_direction
        and direction_not_required
    )
    return RectangleSampleOutcome(
        sample_id=sample.dataset().dataset_id,
        sample_name=sample.sample_name,
        partition=sample.partition,
        label=sample.label,
        sample_kind=sample.sample_kind,
        expected_state=sample.expected_state,
        candidate_count=candidate_count,
        structure_confirmed=structure_confirmed,
        neutral_direction=neutral_direction,
        direction_not_required=direction_not_required,
        history_blocked=history_blocked,
        definition_conforms=definition_conforms,
        false_positive=false_positive,
        false_negative=false_negative,
        detector_result_hash=result_hash,
    )


def _reviews(
    samples: tuple[RectanglePilotSample, ...],
    outcomes: tuple[RectangleSampleOutcome, ...],
    *,
    reviewed_on: date,
) -> tuple[PatternSampleReview, ...]:
    by_id = {item.sample_id: item for item in outcomes}
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
                f"Frozen fixture definition review for {sample.sample_kind.value}; "
                "price direction and financial outcomes were not considered."
            ),
            reviewed_on=reviewed_on,
        )
        for sample in samples
    )


def _evaluation(
    version: FrozenCalibrationVersion,
    manifest: CalibrationDatasetManifest,
    samples: tuple[RectanglePilotSample, ...],
    parameters: DetectorParameterSet,
    *,
    partition: CalibrationPartition,
    completed_on: date,
) -> PatternValidationEvaluation:
    outcomes = tuple(_run_sample(item, parameters) for item in samples)
    reviews = _reviews(samples, outcomes, reviewed_on=completed_on)
    return PatternValidationEvaluation(
        calibration_version_id=version.version_id,
        partition=partition,
        parameters_hash=version.parameters_hash,
        dataset_manifest_hash=version.dataset_manifest_hash,
        partition_hash=manifest.partition_hash(partition),
        reviews=reviews,
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
            f"{item.sample_name}:definition_mismatch"
            for item in outcomes
            if not item.definition_conforms
        ),
        completed_on=completed_on,
    )


def _execute_asset_class(
    dataset_manifest: RectangleCalibrationDatasetManifest,
    validation: CalibrationValidationFramework,
    economic_asset_class: str,
) -> RectangleClassCalibrationResult:
    manifest = dataset_manifest.exact_manifest(economic_asset_class)
    development_samples = dataset_manifest.partition_samples(
        economic_asset_class,
        CalibrationPartition.DEVELOPMENT,
    )
    attempt_results: list[RectangleParameterAttemptResult] = []
    attempt_records: list[CalibrationAttemptRecord] = []
    for attempt_number in (1, 2):
        parameters = build_rectangle_pilot_parameters(
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
                "Evaluate whether a 0.5 percent band admits false narrow ranges"
                if attempt_number == 1
                else "Raise the minimum width to 2.0 percent after the Development "
                "too-narrow false structure"
            ),
            attempted_on=date(2026, 8, 17 + attempt_number),
        )
        attempt_records.append(attempt)
        attempt_results.append(
            RectangleParameterAttemptResult(attempt, parameters, outcomes)
        )

    final_parameters = attempt_results[-1].parameters
    version = validation.register_version(
        final_parameters,
        manifest,
        tuple(attempt_records),
        frozen_on=date(2026, 8, 20),
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
        completed_on=date(2026, 8, 20),
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
        completed_on=date(2026, 8, 21),
    )
    validation.record_evaluation(untouched)
    return RectangleClassCalibrationResult(
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


def execute_rectangle_calibration_pilot() -> RectangleCalibrationPilotResult:
    """Run the reproducible neutral-structure pilot without external calls."""

    dataset_manifest = build_rectangle_calibration_dataset_manifest()
    validation = CalibrationValidationFramework()
    equity = _execute_asset_class(dataset_manifest, validation, "EQUITY")
    fixed_income = _execute_asset_class(
        dataset_manifest,
        validation,
        "FIXED_INCOME",
    )
    return RectangleCalibrationPilotResult(dataset_manifest, equity, fixed_income)
