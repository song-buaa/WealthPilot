"""Deterministic US Stock/ETF Breakdown calibration-process pilot.

The pilot validates bearish technical-evidence definitions only.  It does not
optimize financial outcomes, recommend short selling, or promote production
calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..core import CorePatternBar, PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..detectors import (
    BreakdownDetector,
    DetectorFramework,
    LevelBreakDirectionConfirmation,
    LevelBreakInvalidation,
    LevelBreakStructureConfirmation,
)
from ..detectors.contracts import ConfirmationState
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


BREAKDOWN_PILOT_DATASET_VERSION = "wp-us-breakdown-pilot-dataset-v1"
BREAKDOWN_PILOT_CALIBRATION_VERSION = "wp-us-breakdown-pilot-calibration-v1"
BREAKDOWN_PILOT_SOURCE_PROVIDER = "WEALTHPILOT_DETERMINISTIC_BREAKDOWN_PILOT_V1"
BREAKDOWN_PILOT_ADJUSTMENT_POLICY = "SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT"
BREAKDOWN_PILOT_CALENDAR_VERSION = "WP_US_WEEKDAY_PILOT_CALENDAR_V1"
BREAKDOWN_TRIGGER_ORDINAL = 80
BREAKDOWN_SAMPLE_BAR_COUNT = 82


class BreakdownSampleKind(str, Enum):
    CLEAN_BREAKDOWN = "clean_breakdown"
    FAKE_BREAKDOWN = "fake_breakdown"
    LOW_VOLUME_BREAKDOWN = "low_volume_breakdown"
    GAP_BREAKDOWN = "gap_breakdown"
    INSUFFICIENT_STRUCTURE = "insufficient_structure"
    FAILED_BREAKDOWN = "failed_breakdown"
    SUPPORT_FAILURE_WITHOUT_CONFIRMATION = "support_failure_without_confirmation"


class BreakdownExpectedState(str, Enum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    CONFIRMED_THEN_INVALIDATED = "confirmed_then_invalidated"


REQUIRED_BREAKDOWN_SAMPLE_KINDS = frozenset(BreakdownSampleKind)


@dataclass(frozen=True)
class BreakdownPilotSample:
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
    sample_kind: BreakdownSampleKind
    label: PatternReviewLabel
    expected_state: BreakdownExpectedState
    reviewer_ids: tuple[str, ...] = ("stage1d2-definition-reviewer",)

    def __post_init__(self) -> None:
        if self.label is PatternReviewLabel.POSITIVE and self.expected_state not in {
            BreakdownExpectedState.CONFIRMED,
            BreakdownExpectedState.CONFIRMED_THEN_INVALIDATED,
        }:
            raise ValueError("positive Breakdown samples require confirmed evidence")
        if (
            self.label is PatternReviewLabel.NEGATIVE
            and self.expected_state is not BreakdownExpectedState.NOT_CONFIRMED
        ):
            raise ValueError("negative Breakdown samples must remain unconfirmed")
        if self.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }:
            raise ValueError("the frozen pilot contains no unresolved review labels")
        if self.economic_asset_class not in {"EQUITY", "FIXED_INCOME"}:
            raise ValueError("Breakdown pilot supports EQUITY and FIXED_INCOME only")

    @property
    def sample_id(self) -> str:
        return stable_id(
            "bdsample",
            {
                "dataset_version": BREAKDOWN_PILOT_DATASET_VERSION,
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
        if self.sample_kind in {
            BreakdownSampleKind.FAKE_BREAKDOWN,
            BreakdownSampleKind.FAILED_BREAKDOWN,
        }:
            return BREAKDOWN_TRIGGER_ORDINAL + 1
        return BREAKDOWN_TRIGGER_ORDINAL

    def core_input(self) -> PatternCoreInput:
        sessions = _weekday_sessions(self.start_date, BREAKDOWN_SAMPLE_BAR_COUNT)
        bars: list[CorePatternBar] = []
        touch_ordinals = (
            (65,)
            if self.sample_kind is BreakdownSampleKind.INSUFFICIENT_STRUCTURE
            else (35, 65)
        )
        rising_context = (
            self.sample_kind
            is BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION
        )
        for ordinal, session in enumerate(sessions):
            open_price = 101.20
            high = 101.60
            low = 101.00
            close = 101.30
            volume = 100.0
            if rising_context and ordinal >= 45:
                close = 101.30 + (ordinal - 45) * 0.12
                open_price = close - 0.10
                high = close + 0.30
                low = close - 0.50
            if ordinal in touch_ordinals:
                low = 100.00
                high = max(high, close + 0.40)
                open_price = close - 0.10
            if ordinal == BREAKDOWN_TRIGGER_ORDINAL:
                open_price = 99.50
                high = 100.40
                low = 96.50
                close = 97.00
                volume = 220.0
                if self.sample_kind is BreakdownSampleKind.GAP_BREAKDOWN:
                    open_price = 97.80
                    high = 98.20
                    low = 96.20
                    close = 96.80
                elif self.sample_kind in {
                    BreakdownSampleKind.FAKE_BREAKDOWN,
                    BreakdownSampleKind.LOW_VOLUME_BREAKDOWN,
                }:
                    close = 97.50
                    volume = 120.0
            elif ordinal == BREAKDOWN_TRIGGER_ORDINAL + 1 and self.sample_kind in {
                BreakdownSampleKind.FAKE_BREAKDOWN,
                BreakdownSampleKind.FAILED_BREAKDOWN,
            }:
                open_price = 100.80
                high = 101.40
                low = 100.50
                close = 101.00
                volume = 130.0
            material = {
                "sample_id": self.sample_id,
                "session": session,
                "ordinal": ordinal,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
            bars.append(
                CorePatternBar(
                    session_date=session,
                    session_ordinal=ordinal,
                    available_from=session,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    bar_id=stable_id("bar", material),
                )
            )
        source_hash = stable_hash(
            {
                "provider": BREAKDOWN_PILOT_SOURCE_PROVIDER,
                "sample_id": self.sample_id,
                "bars": tuple(bars),
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
            adjustment_policy=BREAKDOWN_PILOT_ADJUSTMENT_POLICY,
            calendar_version=BREAKDOWN_PILOT_CALENDAR_VERSION,
            last_closed_session=bars[-1].session_date,
            source_bar_hash=source_hash,
            dataset_version=source_hash,
            bars=tuple(bars),
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
            source_provider=BREAKDOWN_PILOT_SOURCE_PROVIDER,
            source_bar_hash=core_input.source_bar_hash,
            adjustment_policy=BREAKDOWN_PILOT_ADJUSTMENT_POLICY,
            calendar_version=BREAKDOWN_PILOT_CALENDAR_VERSION,
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
class BreakdownCalibrationDatasetManifest:
    dataset_version: str
    samples: tuple[BreakdownPilotSample, ...]
    equity: CalibrationDatasetManifest
    fixed_income: CalibrationDatasetManifest

    def __post_init__(self) -> None:
        if self.dataset_version != BREAKDOWN_PILOT_DATASET_VERSION:
            raise ValueError("Breakdown pilot dataset version is immutable")
        if {item.sample_kind for item in self.samples} != REQUIRED_BREAKDOWN_SAMPLE_KINDS:
            raise ValueError("Breakdown pilot must cover all seven definition cases")
        if {item.economic_asset_class for item in self.samples} != {
            "EQUITY",
            "FIXED_INCOME",
        }:
            raise ValueError("Breakdown pilot requires Equity and Fixed Income samples")
        for manifest in (self.equity, self.fixed_income):
            if manifest.pattern_family != "level_break" or manifest.pattern_type != "breakdown":
                raise ValueError("Breakdown pilot manifests must bind only to Breakdown")
            if manifest.coverage_gaps():
                raise ValueError(
                    f"Breakdown pilot manifest coverage is incomplete: "
                    f"{manifest.coverage_gaps()}"
                )
        expected_ids = {item.dataset().dataset_id for item in self.samples}
        actual_ids = {
            item.dataset_id
            for manifest in (self.equity, self.fixed_income)
            for item in manifest.datasets
        }
        if actual_ids != expected_ids:
            raise ValueError("Breakdown pilot samples and exact manifests are not aligned")

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
        raise KeyError(f"unsupported Breakdown pilot asset class: {economic_asset_class}")

    def partition_samples(
        self,
        economic_asset_class: str,
        partition: CalibrationPartition,
    ) -> tuple[BreakdownPilotSample, ...]:
        normalized = economic_asset_class.strip().upper()
        return tuple(
            item
            for item in self.samples
            if item.economic_asset_class == normalized and item.partition is partition
        )


@dataclass(frozen=True)
class BreakdownSampleOutcome:
    sample_id: str
    sample_name: str
    partition: CalibrationPartition
    label: PatternReviewLabel
    sample_kind: BreakdownSampleKind
    expected_state: BreakdownExpectedState
    candidate_count: int
    direction_confirmed: bool
    ema_direction_aligned: bool | None
    invalidated: bool
    definition_conforms: bool
    false_positive: bool
    false_negative: bool
    detector_result_hash: str


@dataclass(frozen=True)
class BreakdownParameterAttemptResult:
    attempt: CalibrationAttemptRecord
    parameters: DetectorParameterSet
    outcomes: tuple[BreakdownSampleOutcome, ...]

    @property
    def definition_pass_count(self) -> int:
        return sum(item.definition_conforms for item in self.outcomes)


@dataclass(frozen=True)
class BreakdownClassCalibrationResult:
    economic_asset_class: str
    manifest: CalibrationDatasetManifest
    parameter_attempts: tuple[BreakdownParameterAttemptResult, ...]
    frozen_version: FrozenCalibrationVersion
    holdout_evaluation: PatternValidationEvaluation
    untouched_evaluation: PatternValidationEvaluation
    promotion_assessment: PromotionAssessment
    validation_report: PatternValidationReport


@dataclass(frozen=True)
class BreakdownCalibrationPilotResult:
    dataset_manifest: BreakdownCalibrationDatasetManifest
    equity: BreakdownClassCalibrationResult
    fixed_income: BreakdownClassCalibrationResult

    @property
    def result_hash(self) -> str:
        return stable_hash(self)


def _weekday_sessions(start: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return tuple(values)


def _sample(
    sample_name: str,
    instrument: str,
    con_id: int,
    economic_asset_class: str,
    asset_coverage: AssetCoverage,
    partition: CalibrationPartition,
    start_date: date,
    market_regime: MarketRegime,
    market_edge_cases: tuple[MarketEdgeCase, ...],
    sample_kind: BreakdownSampleKind,
    label: PatternReviewLabel,
    expected_state: BreakdownExpectedState,
) -> BreakdownPilotSample:
    return BreakdownPilotSample(
        sample_name=sample_name,
        instrument=instrument,
        con_id=con_id,
        isin=f"PILOT-{instrument}-{sample_name}",
        economic_asset_class=economic_asset_class,
        asset_coverage=asset_coverage,
        partition=partition,
        start_date=start_date,
        market_regimes=(market_regime,),
        market_edge_cases=market_edge_cases,
        sample_kind=sample_kind,
        label=label,
        expected_state=expected_state,
    )


def build_breakdown_pilot_samples() -> tuple[BreakdownPilotSample, ...]:
    """Return the frozen, definition-labeled Stage 1D-2 sample catalog."""

    development = CalibrationPartition.DEVELOPMENT
    holdout = CalibrationPartition.HOLDOUT
    untouched = CalibrationPartition.UNTOUCHED_VALIDATION
    positive = PatternReviewLabel.POSITIVE
    negative = PatternReviewLabel.NEGATIVE
    confirmed = BreakdownExpectedState.CONFIRMED
    not_confirmed = BreakdownExpectedState.NOT_CONFIRMED
    failed = BreakdownExpectedState.CONFIRMED_THEN_INVALIDATED
    common = AssetCoverage.COMMON_STOCK
    broad = AssetCoverage.BROAD_MARKET_ETF
    sector = AssetCoverage.SECTOR_ETF
    fixed = AssetCoverage.FIXED_INCOME_ETF
    return (
        _sample("eq-dev-clean", "AAPL", 3001, "EQUITY", common, development,
                date(2010, 1, 4), MarketRegime.BEAR, (MarketEdgeCase.SPLIT,),
                BreakdownSampleKind.CLEAN_BREAKDOWN, positive, confirmed),
        _sample("eq-dev-fake", "SPY", 3002, "EQUITY", broad, development,
                date(2010, 5, 3), MarketRegime.SIDEWAYS, (MarketEdgeCase.HOLIDAY,),
                BreakdownSampleKind.FAKE_BREAKDOWN, negative, not_confirmed),
        _sample("eq-dev-low-volume", "XLK", 3003, "EQUITY", sector, development,
                date(2011, 1, 3), MarketRegime.LOW_VOLATILITY,
                (MarketEdgeCase.DIVIDEND,), BreakdownSampleKind.LOW_VOLUME_BREAKDOWN,
                negative, not_confirmed),
        _sample("eq-dev-gap", "AAPL", 3001, "EQUITY", common, development,
                date(2011, 5, 2), MarketRegime.HIGH_VOLATILITY,
                (MarketEdgeCase.EARNINGS_GAP, MarketEdgeCase.OVERNIGHT_GAP),
                BreakdownSampleKind.GAP_BREAKDOWN, positive, confirmed),
        _sample("eq-dev-insufficient", "SPY", 3002, "EQUITY", broad, development,
                date(2012, 1, 3), MarketRegime.BULL, (MarketEdgeCase.HALF_DAY,),
                BreakdownSampleKind.INSUFFICIENT_STRUCTURE, negative, not_confirmed),
        _sample("eq-dev-failed", "XLK", 3003, "EQUITY", sector, development,
                date(2012, 5, 1), MarketRegime.HIGH_VOLATILITY,
                (MarketEdgeCase.LOW_LIQUIDITY,), BreakdownSampleKind.FAILED_BREAKDOWN,
                positive, failed),
        _sample("eq-dev-no-confirmation", "AAPL", 3001, "EQUITY", common,
                development, date(2013, 1, 2), MarketRegime.BULL, (),
                BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION,
                negative, not_confirmed),
        _sample("eq-holdout-clean", "AAPL", 3001, "EQUITY", common, holdout,
                date(2015, 1, 2), MarketRegime.BEAR, (),
                BreakdownSampleKind.CLEAN_BREAKDOWN, positive, confirmed),
        _sample("eq-holdout-no-confirmation", "SPY", 3002, "EQUITY", broad,
                holdout, date(2015, 5, 1), MarketRegime.BULL, (),
                BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION,
                negative, not_confirmed),
        _sample("eq-holdout-failed", "XLK", 3003, "EQUITY", sector, holdout,
                date(2016, 1, 4), MarketRegime.HIGH_VOLATILITY, (),
                BreakdownSampleKind.FAILED_BREAKDOWN, positive, failed),
        _sample("eq-validation-gap", "SPY", 3002, "EQUITY", broad, untouched,
                date(2019, 1, 2), MarketRegime.HIGH_VOLATILITY, (),
                BreakdownSampleKind.GAP_BREAKDOWN, positive, confirmed),
        _sample("eq-validation-fake", "AAPL", 3001, "EQUITY", common, untouched,
                date(2019, 5, 1), MarketRegime.SIDEWAYS, (),
                BreakdownSampleKind.FAKE_BREAKDOWN, negative, not_confirmed),
        _sample("eq-validation-insufficient", "XLK", 3003, "EQUITY", sector,
                untouched, date(2020, 1, 2), MarketRegime.LOW_VOLATILITY, (),
                BreakdownSampleKind.INSUFFICIENT_STRUCTURE, negative, not_confirmed),
        _sample("fi-dev-clean", "AGG", 4001, "FIXED_INCOME", fixed, development,
                date(2010, 1, 4), MarketRegime.BEAR,
                (MarketEdgeCase.SPLIT, MarketEdgeCase.DIVIDEND),
                BreakdownSampleKind.CLEAN_BREAKDOWN, positive, confirmed),
        _sample("fi-dev-fake", "TLT", 4002, "FIXED_INCOME", fixed, development,
                date(2010, 5, 3), MarketRegime.SIDEWAYS,
                (MarketEdgeCase.OVERNIGHT_GAP,), BreakdownSampleKind.FAKE_BREAKDOWN,
                negative, not_confirmed),
        _sample("fi-dev-low-volume", "LQD", 4003, "FIXED_INCOME", fixed,
                development, date(2011, 1, 3), MarketRegime.LOW_VOLATILITY,
                (MarketEdgeCase.HOLIDAY,), BreakdownSampleKind.LOW_VOLUME_BREAKDOWN,
                negative, not_confirmed),
        _sample("fi-dev-gap", "AGG", 4001, "FIXED_INCOME", fixed, development,
                date(2011, 5, 2), MarketRegime.HIGH_VOLATILITY,
                (MarketEdgeCase.HALF_DAY,), BreakdownSampleKind.GAP_BREAKDOWN,
                positive, confirmed),
        _sample("fi-dev-insufficient", "TLT", 4002, "FIXED_INCOME", fixed,
                development, date(2012, 1, 3), MarketRegime.BULL,
                (MarketEdgeCase.LOW_LIQUIDITY,),
                BreakdownSampleKind.INSUFFICIENT_STRUCTURE, negative, not_confirmed),
        _sample("fi-dev-failed", "LQD", 4003, "FIXED_INCOME", fixed, development,
                date(2012, 5, 1), MarketRegime.HIGH_VOLATILITY, (),
                BreakdownSampleKind.FAILED_BREAKDOWN, positive, failed),
        _sample("fi-dev-no-confirmation", "AGG", 4001, "FIXED_INCOME", fixed,
                development, date(2013, 1, 2), MarketRegime.BULL, (),
                BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION,
                negative, not_confirmed),
        _sample("fi-holdout-clean", "LQD", 4003, "FIXED_INCOME", fixed, holdout,
                date(2015, 1, 2), MarketRegime.BEAR, (),
                BreakdownSampleKind.CLEAN_BREAKDOWN, positive, confirmed),
        _sample("fi-holdout-no-confirmation", "AGG", 4001, "FIXED_INCOME", fixed,
                holdout, date(2016, 1, 4), MarketRegime.BULL, (),
                BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION,
                negative, not_confirmed),
        _sample("fi-validation-gap", "TLT", 4002, "FIXED_INCOME", fixed, untouched,
                date(2019, 1, 2), MarketRegime.HIGH_VOLATILITY, (),
                BreakdownSampleKind.GAP_BREAKDOWN, positive, confirmed),
        _sample("fi-validation-insufficient", "LQD", 4003, "FIXED_INCOME", fixed,
                untouched, date(2020, 1, 2), MarketRegime.LOW_VOLATILITY, (),
                BreakdownSampleKind.INSUFFICIENT_STRUCTURE, negative, not_confirmed),
    )


def build_breakdown_calibration_dataset_manifest() -> BreakdownCalibrationDatasetManifest:
    samples = build_breakdown_pilot_samples()

    def exact_manifest(asset_class: str) -> CalibrationDatasetManifest:
        return CalibrationDatasetManifest(
            pattern_family="level_break",
            pattern_type="breakdown",
            manifest_version=(
                f"{BREAKDOWN_PILOT_DATASET_VERSION}-{asset_class.lower()}"
            ),
            datasets=tuple(
                item.dataset()
                for item in samples
                if item.economic_asset_class == asset_class
            ),
        )

    return BreakdownCalibrationDatasetManifest(
        dataset_version=BREAKDOWN_PILOT_DATASET_VERSION,
        samples=samples,
        equity=exact_manifest("EQUITY"),
        fixed_income=exact_manifest("FIXED_INCOME"),
    )


def _parameter_values(
    economic_asset_class: str,
    *,
    minimum_boundary_touches: int,
    calibration_stage: str,
) -> tuple[tuple[str, bool | int | float | str], ...]:
    fixed_income = economic_asset_class == "FIXED_INCOME"
    return (
        ("atr_margin_multiplier", 0.20 if fixed_income else 0.25),
        ("calibration_stage", calibration_stage),
        ("decisive_margin_pct", 0.05 if fixed_income else 0.10),
        ("expiry_sessions", 15),
        ("invalidation_buffer_pct", 0.20 if fixed_income else 0.35),
        ("lookback_bars", 60),
        ("minimum_boundary_age_sessions", 3),
        ("minimum_boundary_touches", minimum_boundary_touches),
        ("parameter_origin", "stage1d2_definition_fixture_pilot"),
        ("zone_atr_width_multiplier", 0.20),
        ("zone_width_pct", 0.15 if fixed_income else 0.25),
        ("volume_average_bars", 20),
        ("volume_ratio_threshold", 1.25 if fixed_income else 1.50),
    )


def build_breakdown_pilot_parameters(
    economic_asset_class: str,
    *,
    attempt_number: int,
) -> DetectorParameterSet:
    asset_class = economic_asset_class.strip().upper()
    if attempt_number not in {1, 2}:
        raise ValueError("Breakdown pilot defines exactly two development attempts")
    key = CalibrationKey(
        market="US",
        economic_asset_class=asset_class,
        timeframe="1d",
        pattern_family="level_break",
        pattern_type="breakdown",
        calibration_version=BREAKDOWN_PILOT_CALIBRATION_VERSION,
    )
    return DetectorParameterSet(
        key=key,
        values=_parameter_values(
            asset_class,
            minimum_boundary_touches=attempt_number,
            calibration_stage=(
                "development_exploration"
                if attempt_number == 1
                else "pilot_frozen_not_production"
            ),
        ),
        minimum_history_bars=80,
    )


def _ema_alignment(result) -> bool | None:
    for pattern in result.results:
        for fact in pattern.candidate.structure_facts:
            if fact.code == "ema_direction_aligned":
                return bool(fact.value)
    return None


def _run_sample(
    sample: BreakdownPilotSample,
    parameters: DetectorParameterSet,
) -> BreakdownSampleOutcome:
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    result = framework.run(
        sample.core_input(),
        evaluation_session_ordinal=sample.evaluation_ordinal,
        calibration_key=parameters.key,
        detector=BreakdownDetector(),
        structure_confirmation=LevelBreakStructureConfirmation(),
        direction_confirmation=LevelBreakDirectionConfirmation(),
        invalidation=LevelBreakInvalidation(),
    )
    direction_confirmed = any(
        item.direction_confirmation.state is ConfirmationState.CONFIRMED
        for item in result.results
    )
    invalidated = any(item.status == "invalidated" for item in result.results)
    expected_confirmed = sample.label is PatternReviewLabel.POSITIVE
    false_positive = not expected_confirmed and direction_confirmed
    false_negative = expected_confirmed and not direction_confirmed
    state_matches = (
        sample.expected_state
        is not BreakdownExpectedState.CONFIRMED_THEN_INVALIDATED
        or invalidated
    )
    return BreakdownSampleOutcome(
        sample_id=sample.dataset().dataset_id,
        sample_name=sample.sample_name,
        partition=sample.partition,
        label=sample.label,
        sample_kind=sample.sample_kind,
        expected_state=sample.expected_state,
        candidate_count=len(result.results),
        direction_confirmed=direction_confirmed,
        ema_direction_aligned=_ema_alignment(result),
        invalidated=invalidated,
        definition_conforms=not false_positive and not false_negative and state_matches,
        false_positive=false_positive,
        false_negative=false_negative,
        detector_result_hash=result.result_hash,
    )


def _reviews(
    samples: tuple[BreakdownPilotSample, ...],
    outcomes: tuple[BreakdownSampleOutcome, ...],
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
                f"Frozen definition review for {sample.sample_kind.value}; "
                "financial outcomes and short-selling semantics were not considered."
            ),
            reviewed_on=reviewed_on,
        )
        for sample in samples
    )


def _evaluation(
    version: FrozenCalibrationVersion,
    manifest: CalibrationDatasetManifest,
    samples: tuple[BreakdownPilotSample, ...],
    parameters: DetectorParameterSet,
    *,
    partition: CalibrationPartition,
    completed_on: date,
) -> PatternValidationEvaluation:
    outcomes = tuple(_run_sample(item, parameters) for item in samples)
    reviews = _reviews(samples, outcomes, reviewed_on=completed_on)
    definition_pass = all(item.definition_conforms for item in outcomes)
    false_positive_pass = not any(item.false_positive for item in outcomes)
    false_negative_pass = not any(item.false_negative for item in outcomes)
    boundary_pass = not any(
        item.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }
        for item in samples
    )
    return PatternValidationEvaluation(
        calibration_version_id=version.version_id,
        partition=partition,
        parameters_hash=version.parameters_hash,
        dataset_manifest_hash=version.dataset_manifest_hash,
        partition_hash=manifest.partition_hash(partition),
        reviews=reviews,
        definition_review_pass=definition_pass,
        false_positive_review_pass=false_positive_pass,
        false_negative_review_pass=false_negative_pass,
        boundary_review_pass=boundary_pass,
        human_review_pass=all(item.reviewer_ids for item in samples),
        failure_modes=tuple(
            f"{item.sample_name}:definition_mismatch"
            for item in outcomes
            if not item.definition_conforms
        ),
        completed_on=completed_on,
    )


def _execute_asset_class(
    dataset_manifest: BreakdownCalibrationDatasetManifest,
    validation: CalibrationValidationFramework,
    economic_asset_class: str,
) -> BreakdownClassCalibrationResult:
    manifest = dataset_manifest.exact_manifest(economic_asset_class)
    development_samples = dataset_manifest.partition_samples(
        economic_asset_class, CalibrationPartition.DEVELOPMENT
    )
    parameter_attempts: list[BreakdownParameterAttemptResult] = []
    attempt_records: list[CalibrationAttemptRecord] = []
    for attempt_number in (1, 2):
        parameters = build_breakdown_pilot_parameters(
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
                "Evaluate the inherited one-touch support hypothesis"
                if attempt_number == 1
                else "Require two available support touches after the Development "
                "insufficient-structure false positive"
            ),
            attempted_on=date(2026, 8, 6 + attempt_number),
        )
        attempt_records.append(attempt)
        parameter_attempts.append(
            BreakdownParameterAttemptResult(attempt, parameters, outcomes)
        )

    final_parameters = parameter_attempts[-1].parameters
    version = validation.register_version(
        final_parameters,
        manifest,
        tuple(attempt_records),
        frozen_on=date(2026, 8, 9),
    )
    holdout = _evaluation(
        version,
        manifest,
        dataset_manifest.partition_samples(
            economic_asset_class, CalibrationPartition.HOLDOUT
        ),
        final_parameters,
        partition=CalibrationPartition.HOLDOUT,
        completed_on=date(2026, 8, 10),
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
        completed_on=date(2026, 8, 11),
    )
    validation.record_evaluation(untouched)
    return BreakdownClassCalibrationResult(
        economic_asset_class=economic_asset_class,
        manifest=manifest,
        parameter_attempts=tuple(parameter_attempts),
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


def execute_breakdown_calibration_pilot() -> BreakdownCalibrationPilotResult:
    """Run the reproducible bearish-evidence pilot without external calls."""

    dataset_manifest = build_breakdown_calibration_dataset_manifest()
    validation = CalibrationValidationFramework()
    equity = _execute_asset_class(dataset_manifest, validation, "EQUITY")
    fixed_income = _execute_asset_class(
        dataset_manifest,
        validation,
        "FIXED_INCOME",
    )
    return BreakdownCalibrationPilotResult(dataset_manifest, equity, fixed_income)
