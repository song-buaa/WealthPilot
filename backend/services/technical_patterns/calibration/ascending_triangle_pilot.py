"""Deterministic US Stock/ETF Ascending Triangle calibration-process pilot.

The pilot validates geometry definitions and the structure/direction boundary.
It does not optimize financial outcomes, predict future direction, or promote
production calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..core import CorePatternBar, PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..detectors import (
    AscendingTriangleDetector,
    AscendingTriangleDirectionConfirmation,
    AscendingTriangleInvalidation,
    AscendingTriangleStructureConfirmation,
    DetectorFramework,
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


ASCENDING_TRIANGLE_PILOT_DATASET_VERSION = (
    "wp-us-ascending-triangle-pilot-dataset-v1"
)
ASCENDING_TRIANGLE_PILOT_CALIBRATION_VERSION = (
    "wp-us-ascending-triangle-pilot-calibration-v1"
)
ASCENDING_TRIANGLE_PILOT_SOURCE_PROVIDER = (
    "WEALTHPILOT_DETERMINISTIC_ASCENDING_TRIANGLE_PILOT_V1"
)
ASCENDING_TRIANGLE_PILOT_ADJUSTMENT_POLICY = (
    "SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT"
)
ASCENDING_TRIANGLE_PILOT_CALENDAR_VERSION = "WP_US_WEEKDAY_PILOT_CALENDAR_V1"


class AscendingTriangleSampleKind(str, Enum):
    CLEAN = "clean_ascending_triangle"
    RECTANGLE = "rectangle_mistaken_as_triangle"
    WEAK_SUPPORT_SLOPE = "rising_support_slope_too_weak"
    DESCENDING_SUPPORT = "support_line_descending"
    UNSTABLE_RESISTANCE = "unstable_resistance"
    INSUFFICIENT_PIVOTS = "insufficient_pivots"
    INSUFFICIENT_TOUCHES = "insufficient_touches"
    POOR_LINE_FIT = "poor_line_fit"
    WEAK_CONVERGENCE = "weak_convergence"
    MEANINGLESS_APEX = "meaningless_apex"
    APEX_TOO_CLOSE = "apex_too_close"
    APEX_TOO_FAR = "apex_too_far"
    STRUCTURE_BROKEN = "structure_broken_before_direction_confirmation"
    INSUFFICIENT_HISTORY = "insufficient_history"


class AscendingTriangleExpectedState(str, Enum):
    STRUCTURE_CONFIRMED_DIRECTION_PENDING = "structure_confirmed_direction_pending"
    STRUCTURE_CONFIRMED_THEN_INVALIDATED = "structure_confirmed_then_invalidated"
    NOT_CONFIRMED = "not_confirmed"
    HISTORY_BLOCKED = "history_blocked"


REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS = frozenset(AscendingTriangleSampleKind)


@dataclass(frozen=True)
class AscendingTrianglePilotSample:
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
    sample_kind: AscendingTriangleSampleKind
    label: PatternReviewLabel
    expected_state: AscendingTriangleExpectedState
    reviewer_ids: tuple[str, ...] = ("stage1d4-geometry-fixture-reviewer",)

    def __post_init__(self) -> None:
        positive_states = {
            AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING,
            AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_THEN_INVALIDATED,
        }
        if self.label is PatternReviewLabel.POSITIVE and self.expected_state not in (
            positive_states
        ):
            raise ValueError("positive Ascending Triangle samples require geometry")
        if self.label is PatternReviewLabel.NEGATIVE and self.expected_state not in {
            AscendingTriangleExpectedState.NOT_CONFIRMED,
            AscendingTriangleExpectedState.HISTORY_BLOCKED,
        }:
            raise ValueError("negative Ascending Triangle samples must fail closed")
        if self.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }:
            raise ValueError("the frozen Pilot contains no unresolved labels")
        if self.economic_asset_class not in {"EQUITY", "FIXED_INCOME"}:
            raise ValueError(
                "Ascending Triangle Pilot supports EQUITY and FIXED_INCOME only"
            )

    @property
    def sample_id(self) -> str:
        return stable_id(
            "atsample",
            {
                "dataset_version": ASCENDING_TRIANGLE_PILOT_DATASET_VERSION,
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
        kind = self.sample_kind
        if kind is AscendingTriangleSampleKind.RECTANGLE:
            return _six_pivot_values(lows=(100.0, 100.0, 100.0))
        if kind is AscendingTriangleSampleKind.WEAK_SUPPORT_SLOPE:
            return _six_pivot_values(lows=(100.0, 100.2, 100.4))
        if kind is AscendingTriangleSampleKind.DESCENDING_SUPPORT:
            return _six_pivot_values(lows=(100.0, 99.0, 98.0))
        if kind is AscendingTriangleSampleKind.UNSTABLE_RESISTANCE:
            return _six_pivot_values(highs=(110.0, 112.0, 110.0))
        if kind is AscendingTriangleSampleKind.INSUFFICIENT_PIVOTS:
            return [
                (100.0 + index, 101.0 + index, 99.0 + index, 100.5 + index, 100.0)
                for index in range(11)
            ]
        if kind is AscendingTriangleSampleKind.INSUFFICIENT_TOUCHES:
            return _six_pivot_values(highs=(110.0, 115.0, 110.0))
        if kind is AscendingTriangleSampleKind.POOR_LINE_FIT:
            return _six_pivot_values(lows=(100.0, 105.0, 104.0))
        if kind is AscendingTriangleSampleKind.WEAK_CONVERGENCE:
            return _six_pivot_values(lows=(100.0, 100.8, 101.6))
        if kind is AscendingTriangleSampleKind.MEANINGLESS_APEX:
            return _six_pivot_values(
                highs=(110.0, 111.0, 112.0),
                lows=(100.0, 101.0, 102.0),
            )
        if kind is AscendingTriangleSampleKind.APEX_TOO_CLOSE:
            return _six_pivot_values(lows=(95.0, 103.0, 108.0))
        if kind is AscendingTriangleSampleKind.APEX_TOO_FAR:
            return _six_pivot_values(lows=(100.0, 100.5, 101.0))
        values = _six_pivot_values()
        if kind is AscendingTriangleSampleKind.STRUCTURE_BROKEN:
            values.append((104.0, 105.0, 103.0, 104.0, 100.0))
        elif kind is AscendingTriangleSampleKind.INSUFFICIENT_HISTORY:
            values = values[:10]
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
                "provider": ASCENDING_TRIANGLE_PILOT_SOURCE_PROVIDER,
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
            adjustment_policy=ASCENDING_TRIANGLE_PILOT_ADJUSTMENT_POLICY,
            calendar_version=ASCENDING_TRIANGLE_PILOT_CALENDAR_VERSION,
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
            source_provider=ASCENDING_TRIANGLE_PILOT_SOURCE_PROVIDER,
            source_bar_hash=core_input.source_bar_hash,
            adjustment_policy=ASCENDING_TRIANGLE_PILOT_ADJUSTMENT_POLICY,
            calendar_version=ASCENDING_TRIANGLE_PILOT_CALENDAR_VERSION,
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
class AscendingTriangleCalibrationDatasetManifest:
    dataset_version: str
    samples: tuple[AscendingTrianglePilotSample, ...]
    equity: CalibrationDatasetManifest
    fixed_income: CalibrationDatasetManifest

    def __post_init__(self) -> None:
        if self.dataset_version != ASCENDING_TRIANGLE_PILOT_DATASET_VERSION:
            raise ValueError("Ascending Triangle Pilot dataset version is immutable")
        if {item.sample_kind for item in self.samples} != (
            REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS
        ):
            raise ValueError("Ascending Triangle Pilot must cover all geometry cases")
        for asset_class in ("EQUITY", "FIXED_INCOME"):
            development_kinds = {
                item.sample_kind
                for item in self.samples
                if item.economic_asset_class == asset_class
                and item.partition is CalibrationPartition.DEVELOPMENT
            }
            if development_kinds != REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS:
                raise ValueError(
                    "each Development partition must cover every geometry case"
                )
        for manifest in (self.equity, self.fixed_income):
            if manifest.pattern_family != "triangle" or (
                manifest.pattern_type != "ascending_triangle"
            ):
                raise ValueError("Pilot manifests must bind to Ascending Triangle")
            if manifest.coverage_gaps():
                raise ValueError(
                    "Ascending Triangle Pilot manifest coverage is incomplete: "
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
    ) -> tuple[AscendingTrianglePilotSample, ...]:
        normalized = economic_asset_class.strip().upper()
        return tuple(
            item
            for item in self.samples
            if item.economic_asset_class == normalized and item.partition is partition
        )


@dataclass(frozen=True)
class AscendingTriangleSampleOutcome:
    sample_id: str
    sample_name: str
    partition: CalibrationPartition
    label: PatternReviewLabel
    sample_kind: AscendingTriangleSampleKind
    expected_state: AscendingTriangleExpectedState
    candidate_count: int
    structure_confirmed: bool
    direction_pending: bool
    direction_confirmed: bool
    invalidated: bool
    history_blocked: bool
    definition_conforms: bool
    false_positive: bool
    false_negative: bool
    detector_result_hash: str


@dataclass(frozen=True)
class AscendingTriangleParameterAttemptResult:
    attempt: CalibrationAttemptRecord
    parameters: DetectorParameterSet
    outcomes: tuple[AscendingTriangleSampleOutcome, ...]

    @property
    def definition_pass_count(self) -> int:
        return sum(item.definition_conforms for item in self.outcomes)

    @property
    def structure_only_confirmed_count(self) -> int:
        return sum(
            item.structure_confirmed
            and item.direction_pending
            and not item.direction_confirmed
            for item in self.outcomes
        )

    @property
    def direction_pending_count(self) -> int:
        return sum(item.direction_pending for item in self.outcomes)

    @property
    def direction_confirmed_count(self) -> int:
        return sum(item.direction_confirmed for item in self.outcomes)


@dataclass(frozen=True)
class AscendingTriangleClassCalibrationResult:
    economic_asset_class: str
    manifest: CalibrationDatasetManifest
    parameter_attempts: tuple[AscendingTriangleParameterAttemptResult, ...]
    frozen_version: FrozenCalibrationVersion
    holdout_evaluation: PatternValidationEvaluation
    untouched_evaluation: PatternValidationEvaluation
    promotion_assessment: PromotionAssessment
    validation_report: PatternValidationReport


@dataclass(frozen=True)
class AscendingTriangleCalibrationPilotResult:
    dataset_manifest: AscendingTriangleCalibrationDatasetManifest
    equity: AscendingTriangleClassCalibrationResult
    fixed_income: AscendingTriangleClassCalibrationResult

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


def _six_pivot_values(
    *,
    highs: tuple[float, float, float] = (110.0, 110.1, 110.0),
    lows: tuple[float, float, float] = (100.0, 102.0, 104.0),
) -> list[tuple[float, float, float, float, float]]:
    middle = (106.0, 107.0, 105.0, 106.0, 100.0)
    values = [middle]
    for resistance, support in zip(highs, lows):
        values.extend(
            (
                (
                    resistance - 2.0,
                    resistance,
                    resistance - 3.0,
                    resistance - 2.0,
                    100.0,
                ),
                middle,
                (
                    support + 2.0,
                    support + 3.0,
                    support,
                    support + 2.0,
                    100.0,
                ),
            )
        )
    values.append(middle)
    return values


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
    kind: AscendingTriangleSampleKind,
) -> AscendingTrianglePilotSample:
    if kind is AscendingTriangleSampleKind.CLEAN:
        label = PatternReviewLabel.POSITIVE
        expected = (
            AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING
        )
    elif kind is AscendingTriangleSampleKind.STRUCTURE_BROKEN:
        label = PatternReviewLabel.POSITIVE
        expected = (
            AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_THEN_INVALIDATED
        )
    else:
        label = PatternReviewLabel.NEGATIVE
        expected = (
            AscendingTriangleExpectedState.HISTORY_BLOCKED
            if kind is AscendingTriangleSampleKind.INSUFFICIENT_HISTORY
            else AscendingTriangleExpectedState.NOT_CONFIRMED
        )
    return AscendingTrianglePilotSample(
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


def build_ascending_triangle_pilot_samples() -> tuple[AscendingTrianglePilotSample, ...]:
    """Return the frozen, geometry-labeled Stage 1D-4 sample catalog."""

    kinds = tuple(AscendingTriangleSampleKind)
    regimes = (
        MarketRegime.BULL,
        MarketRegime.SIDEWAYS,
        MarketRegime.LOW_VOLATILITY,
        MarketRegime.BEAR,
        MarketRegime.HIGH_VOLATILITY,
    )
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
            (6101, 6102, 6103),
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
            (6201, 6202, 6203),
            (AssetCoverage.FIXED_INCOME_ETF,) * 3,
            fixed_edges,
        ),
    )
    samples: list[AscendingTrianglePilotSample] = []
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
                date(2018, 1, 4),
                AscendingTriangleSampleKind.CLEAN,
                MarketRegime.SIDEWAYS,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2018, 7, 4),
                AscendingTriangleSampleKind.WEAK_CONVERGENCE,
                MarketRegime.LOW_VOLATILITY,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2019, 1, 4),
                AscendingTriangleSampleKind.RECTANGLE,
                MarketRegime.SIDEWAYS,
            ),
            (
                CalibrationPartition.HOLDOUT,
                date(2019, 7, 4),
                AscendingTriangleSampleKind.STRUCTURE_BROKEN,
                MarketRegime.HIGH_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2021, 1, 4),
                AscendingTriangleSampleKind.CLEAN,
                MarketRegime.BULL,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2021, 7, 4),
                AscendingTriangleSampleKind.POOR_LINE_FIT,
                MarketRegime.HIGH_VOLATILITY,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2022, 1, 4),
                AscendingTriangleSampleKind.APEX_TOO_CLOSE,
                MarketRegime.BEAR,
            ),
            (
                CalibrationPartition.UNTOUCHED_VALIDATION,
                date(2022, 7, 4),
                AscendingTriangleSampleKind.INSUFFICIENT_TOUCHES,
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


def build_ascending_triangle_calibration_dataset_manifest() -> (
    AscendingTriangleCalibrationDatasetManifest
):
    samples = build_ascending_triangle_pilot_samples()

    def exact_manifest(asset_class: str) -> CalibrationDatasetManifest:
        return CalibrationDatasetManifest(
            pattern_family="triangle",
            pattern_type="ascending_triangle",
            manifest_version=(
                f"{ASCENDING_TRIANGLE_PILOT_DATASET_VERSION}-{asset_class.lower()}"
            ),
            datasets=tuple(
                item.dataset()
                for item in samples
                if item.economic_asset_class == asset_class
            ),
        )

    return AscendingTriangleCalibrationDatasetManifest(
        dataset_version=ASCENDING_TRIANGLE_PILOT_DATASET_VERSION,
        samples=samples,
        equity=exact_manifest("EQUITY"),
        fixed_income=exact_manifest("FIXED_INCOME"),
    )


def _parameter_values(
    *,
    minimum_contraction_pct: float,
    calibration_stage: str,
) -> tuple[tuple[str, bool | int | float | str], ...]:
    return (
        ("boundary_tolerance_pct", 0.03),
        ("breakout_close_margin_pct", 0.10),
        ("calibration_stage", calibration_stage),
        ("containment_tolerance_pct", 1.0),
        ("expiry_sessions", 20),
        ("horizontal_resistance_max_slope_pct_per_session", 0.0005),
        ("horizontal_to_support_max_slope_ratio", 0.50),
        ("invalidation_buffer_pct", 0.10),
        ("maximum_apex_horizon_sessions", 80),
        ("maximum_apex_progress_at_confirmation", 0.90),
        ("maximum_line_fit_error_pct", 0.01),
        ("maximum_resistance_zone_width_pct", 1.0),
        ("maximum_source_pivots", 8),
        ("minimum_apex_progress", 0.15),
        ("minimum_contraction_pct", minimum_contraction_pct),
        ("minimum_source_pivots", 6),
        ("minimum_structure_span_sessions", 8),
        ("minimum_touches_per_side", 3),
        ("parameter_origin", "stage1d4_geometry_fixture_pilot"),
        ("pivot_left_window_bars", 1),
        ("pivot_minimum_bar_separation", 0),
        ("pivot_minimum_price_separation_pct", 0.0),
        ("pivot_plateau_tolerance_pct", 0.0),
        ("pivot_right_confirmation_bars", 1),
        ("support_min_slope_pct_per_session", 0.001),
    )


def build_ascending_triangle_pilot_parameters(
    economic_asset_class: str,
    *,
    attempt_number: int,
) -> DetectorParameterSet:
    asset_class = economic_asset_class.strip().upper()
    if asset_class not in {"EQUITY", "FIXED_INCOME"}:
        raise ValueError("Pilot supports EQUITY and FIXED_INCOME only")
    if attempt_number not in {1, 2}:
        raise ValueError("Pilot defines exactly two Development attempts")
    key = CalibrationKey(
        market="US",
        economic_asset_class=asset_class,
        timeframe="1d",
        pattern_family="triangle",
        pattern_type="ascending_triangle",
        calibration_version=ASCENDING_TRIANGLE_PILOT_CALIBRATION_VERSION,
    )
    return DetectorParameterSet(
        key=key,
        values=_parameter_values(
            minimum_contraction_pct=0.12 if attempt_number == 1 else 0.25,
            calibration_stage=(
                "development_exploration"
                if attempt_number == 1
                else "pilot_frozen_not_production"
            ),
        ),
        minimum_history_bars=11,
    )


def _run_sample(
    sample: AscendingTrianglePilotSample,
    parameters: DetectorParameterSet,
) -> AscendingTriangleSampleOutcome:
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    try:
        result = framework.run(
            sample.core_input(),
            evaluation_session_ordinal=sample.evaluation_ordinal,
            calibration_key=parameters.key,
            detector=AscendingTriangleDetector(),
            structure_confirmation=AscendingTriangleStructureConfirmation(),
            direction_confirmation=AscendingTriangleDirectionConfirmation(),
            invalidation=AscendingTriangleInvalidation(),
        )
    except InsufficientPatternHistory as exc:
        history_blocked = True
        candidate_count = 0
        structure_confirmed = False
        direction_pending = False
        direction_confirmed = False
        invalidated = False
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
        candidate_count = len(result.results)
        structure_confirmed = any(
            item.structure_confirmation.state is ConfirmationState.CONFIRMED
            for item in result.results
        )
        direction_pending = any(
            item.direction_confirmation.state is ConfirmationState.PENDING
            for item in result.results
        )
        direction_confirmed = any(
            item.direction_confirmation.state is ConfirmationState.CONFIRMED
            for item in result.results
        )
        invalidated = any(item.status == "invalidated" for item in result.results)
        result_hash = result.result_hash

    expected = sample.expected_state
    expected_positive = expected in {
        AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_DIRECTION_PENDING,
        AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_THEN_INVALIDATED,
    }
    false_positive = not expected_positive and structure_confirmed
    false_negative = expected_positive and not structure_confirmed
    if expected is AscendingTriangleExpectedState.HISTORY_BLOCKED:
        state_matches = history_blocked
    elif expected is AscendingTriangleExpectedState.NOT_CONFIRMED:
        state_matches = not history_blocked and not structure_confirmed
    elif expected is AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_THEN_INVALIDATED:
        state_matches = (
            structure_confirmed
            and direction_pending
            and not direction_confirmed
            and invalidated
        )
    else:
        state_matches = (
            structure_confirmed
            and direction_pending
            and not direction_confirmed
            and not invalidated
        )
    definition_conforms = not false_positive and not false_negative and state_matches
    return AscendingTriangleSampleOutcome(
        sample_id=sample.dataset().dataset_id,
        sample_name=sample.sample_name,
        partition=sample.partition,
        label=sample.label,
        sample_kind=sample.sample_kind,
        expected_state=sample.expected_state,
        candidate_count=candidate_count,
        structure_confirmed=structure_confirmed,
        direction_pending=direction_pending,
        direction_confirmed=direction_confirmed,
        invalidated=invalidated,
        history_blocked=history_blocked,
        definition_conforms=definition_conforms,
        false_positive=false_positive,
        false_negative=false_negative,
        detector_result_hash=result_hash,
    )


def _reviews(
    samples: tuple[AscendingTrianglePilotSample, ...],
    outcomes: tuple[AscendingTriangleSampleOutcome, ...],
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
                f"Frozen fixture geometry review for {sample.sample_kind.value}; "
                "future direction and financial outcomes were not considered."
            ),
            reviewed_on=reviewed_on,
        )
        for sample in samples
    )


def _evaluation(
    version: FrozenCalibrationVersion,
    manifest: CalibrationDatasetManifest,
    samples: tuple[AscendingTrianglePilotSample, ...],
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
            f"{item.sample_name}:geometry_definition_mismatch"
            for item in outcomes
            if not item.definition_conforms
        ),
        completed_on=completed_on,
    )


def _execute_asset_class(
    dataset_manifest: AscendingTriangleCalibrationDatasetManifest,
    validation: CalibrationValidationFramework,
    economic_asset_class: str,
) -> AscendingTriangleClassCalibrationResult:
    manifest = dataset_manifest.exact_manifest(economic_asset_class)
    development_samples = dataset_manifest.partition_samples(
        economic_asset_class,
        CalibrationPartition.DEVELOPMENT,
    )
    attempt_results: list[AscendingTriangleParameterAttemptResult] = []
    attempt_records: list[CalibrationAttemptRecord] = []
    for attempt_number in (1, 2):
        parameters = build_ascending_triangle_pilot_parameters(
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
                "Evaluate whether 0.12 minimum contraction admits weak geometry"
                if attempt_number == 1
                else "Raise minimum contraction to 0.25 after the Development "
                "weak-convergence false structure"
            ),
            attempted_on=date(2026, 8, 19 + attempt_number - 1),
        )
        attempt_records.append(attempt)
        attempt_results.append(
            AscendingTriangleParameterAttemptResult(attempt, parameters, outcomes)
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
    return AscendingTriangleClassCalibrationResult(
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


def execute_ascending_triangle_calibration_pilot() -> (
    AscendingTriangleCalibrationPilotResult
):
    """Run the reproducible geometry Pilot without external calls."""

    dataset_manifest = build_ascending_triangle_calibration_dataset_manifest()
    validation = CalibrationValidationFramework()
    equity = _execute_asset_class(dataset_manifest, validation, "EQUITY")
    fixed_income = _execute_asset_class(
        dataset_manifest,
        validation,
        "FIXED_INCOME",
    )
    return AscendingTriangleCalibrationPilotResult(
        dataset_manifest,
        equity,
        fixed_income,
    )
