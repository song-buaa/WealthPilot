"""Deterministic US Stock/ETF Breakout calibration-process pilot.

The pilot exercises the Stage 1D workflow with definition-focused synthetic
fixtures.  It is not empirical market calibration and cannot promote the
Breakout detector into production by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ..core import CorePatternBar, PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..detectors import (
    BreakoutDetector,
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


BREAKOUT_PILOT_DATASET_VERSION = "wp-us-breakout-pilot-dataset-v1"
BREAKOUT_PILOT_CALIBRATION_VERSION = "wp-us-breakout-pilot-calibration-v1"
BREAKOUT_PILOT_SOURCE_PROVIDER = "WEALTHPILOT_DETERMINISTIC_BREAKOUT_PILOT_V1"
BREAKOUT_PILOT_ADJUSTMENT_POLICY = "SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT"
BREAKOUT_PILOT_CALENDAR_VERSION = "WP_US_WEEKDAY_PILOT_CALENDAR_V1"
BREAKOUT_TRIGGER_ORDINAL = 80
BREAKOUT_SAMPLE_BAR_COUNT = 82


class BreakoutSampleKind(str, Enum):
    CLEAN_BREAKOUT = "clean_breakout"
    FAKE_BREAKOUT = "fake_breakout"
    LOW_VOLUME_BREAKOUT = "low_volume_breakout"
    GAP_BREAKOUT = "gap_breakout"
    INSUFFICIENT_STRUCTURE = "insufficient_structure"
    FAILED_BREAKOUT = "failed_breakout"


class BreakoutExpectedState(str, Enum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    CONFIRMED_THEN_INVALIDATED = "confirmed_then_invalidated"


REQUIRED_BREAKOUT_SAMPLE_KINDS = frozenset(BreakoutSampleKind)


@dataclass(frozen=True)
class BreakoutPilotSample:
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
    sample_kind: BreakoutSampleKind
    label: PatternReviewLabel
    expected_state: BreakoutExpectedState
    reviewer_ids: tuple[str, ...] = ("stage1d1-definition-reviewer",)

    def __post_init__(self) -> None:
        if self.label is PatternReviewLabel.POSITIVE and self.expected_state not in {
            BreakoutExpectedState.CONFIRMED,
            BreakoutExpectedState.CONFIRMED_THEN_INVALIDATED,
        }:
            raise ValueError("positive Breakout samples require confirmed structure evidence")
        if (
            self.label is PatternReviewLabel.NEGATIVE
            and self.expected_state is not BreakoutExpectedState.NOT_CONFIRMED
        ):
            raise ValueError("negative Breakout samples must remain unconfirmed")
        if self.label in {
            PatternReviewLabel.AMBIGUOUS,
            PatternReviewLabel.REVIEW_DISAGREEMENT,
        }:
            raise ValueError("the frozen pilot contains no unresolved review labels")
        if self.economic_asset_class not in {"EQUITY", "FIXED_INCOME"}:
            raise ValueError("Breakout pilot supports EQUITY and FIXED_INCOME only")

    @property
    def sample_id(self) -> str:
        return stable_id(
            "brsample",
            {
                "dataset_version": BREAKOUT_PILOT_DATASET_VERSION,
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
            BreakoutSampleKind.FAKE_BREAKOUT,
            BreakoutSampleKind.FAILED_BREAKOUT,
        }:
            return BREAKOUT_TRIGGER_ORDINAL + 1
        return BREAKOUT_TRIGGER_ORDINAL

    def core_input(self) -> PatternCoreInput:
        sessions = _weekday_sessions(self.start_date, BREAKOUT_SAMPLE_BAR_COUNT)
        bars: list[CorePatternBar] = []
        touch_ordinals = (
            (65,)
            if self.sample_kind is BreakoutSampleKind.INSUFFICIENT_STRUCTURE
            else (35, 65)
        )
        for ordinal, session in enumerate(sessions):
            open_price = 99.10
            high = 99.30
            low = 98.80
            close = 99.20
            volume = 100.0
            if ordinal in touch_ordinals:
                open_price = 99.20
                high = 100.00
                low = 98.80
                close = 99.40
            if ordinal == BREAKOUT_TRIGGER_ORDINAL:
                open_price = 100.50
                high = 103.50
                low = 99.60
                close = 103.00
                volume = 220.0
                if self.sample_kind is BreakoutSampleKind.GAP_BREAKOUT:
                    open_price = 102.20
                    high = 103.80
                    low = 102.00
                    close = 103.30
                elif self.sample_kind in {
                    BreakoutSampleKind.FAKE_BREAKOUT,
                    BreakoutSampleKind.LOW_VOLUME_BREAKOUT,
                }:
                    close = 102.30
                    volume = 120.0
            elif ordinal == BREAKOUT_TRIGGER_ORDINAL + 1 and self.sample_kind in {
                BreakoutSampleKind.FAKE_BREAKOUT,
                BreakoutSampleKind.FAILED_BREAKOUT,
            }:
                open_price = 99.20
                high = 99.50
                low = 98.70
                close = 99.00
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
                "provider": BREAKOUT_PILOT_SOURCE_PROVIDER,
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
            adjustment_policy=BREAKOUT_PILOT_ADJUSTMENT_POLICY,
            calendar_version=BREAKOUT_PILOT_CALENDAR_VERSION,
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
            source_provider=BREAKOUT_PILOT_SOURCE_PROVIDER,
            source_bar_hash=core_input.source_bar_hash,
            adjustment_policy=BREAKOUT_PILOT_ADJUSTMENT_POLICY,
            calendar_version=BREAKOUT_PILOT_CALENDAR_VERSION,
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
class BreakoutCalibrationDatasetManifest:
    dataset_version: str
    samples: tuple[BreakoutPilotSample, ...]
    equity: CalibrationDatasetManifest
    fixed_income: CalibrationDatasetManifest

    def __post_init__(self) -> None:
        if self.dataset_version != BREAKOUT_PILOT_DATASET_VERSION:
            raise ValueError("Breakout pilot dataset version is immutable")
        if {item.sample_kind for item in self.samples} != REQUIRED_BREAKOUT_SAMPLE_KINDS:
            raise ValueError("Breakout pilot must cover all six definition edge cases")
        if {item.economic_asset_class for item in self.samples} != {
            "EQUITY",
            "FIXED_INCOME",
        }:
            raise ValueError("Breakout pilot requires Equity and Fixed Income samples")
        for manifest in (self.equity, self.fixed_income):
            if manifest.pattern_family != "level_break" or manifest.pattern_type != "breakout":
                raise ValueError("Breakout pilot manifests must bind only to Breakout")
            if manifest.coverage_gaps():
                raise ValueError(
                    f"Breakout pilot manifest coverage is incomplete: {manifest.coverage_gaps()}"
                )
        expected_ids = {item.dataset().dataset_id for item in self.samples}
        actual_ids = {
            item.dataset_id
            for manifest in (self.equity, self.fixed_income)
            for item in manifest.datasets
        }
        if actual_ids != expected_ids:
            raise ValueError("Breakout pilot samples and exact manifests are not aligned")

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
        raise KeyError(f"unsupported Breakout pilot asset class: {economic_asset_class}")

    def partition_samples(
        self,
        economic_asset_class: str,
        partition: CalibrationPartition,
    ) -> tuple[BreakoutPilotSample, ...]:
        normalized = economic_asset_class.strip().upper()
        return tuple(
            item
            for item in self.samples
            if item.economic_asset_class == normalized and item.partition is partition
        )


@dataclass(frozen=True)
class BreakoutSampleOutcome:
    sample_id: str
    sample_name: str
    partition: CalibrationPartition
    label: PatternReviewLabel
    sample_kind: BreakoutSampleKind
    expected_state: BreakoutExpectedState
    candidate_count: int
    direction_confirmed: bool
    invalidated: bool
    definition_conforms: bool
    false_positive: bool
    false_negative: bool
    detector_result_hash: str


@dataclass(frozen=True)
class BreakoutParameterAttemptResult:
    attempt: CalibrationAttemptRecord
    parameters: DetectorParameterSet
    outcomes: tuple[BreakoutSampleOutcome, ...]

    @property
    def definition_pass_count(self) -> int:
        return sum(item.definition_conforms for item in self.outcomes)


@dataclass(frozen=True)
class BreakoutClassCalibrationResult:
    economic_asset_class: str
    manifest: CalibrationDatasetManifest
    parameter_attempts: tuple[BreakoutParameterAttemptResult, ...]
    frozen_version: FrozenCalibrationVersion
    holdout_evaluation: PatternValidationEvaluation
    untouched_evaluation: PatternValidationEvaluation
    promotion_assessment: PromotionAssessment
    validation_report: PatternValidationReport


@dataclass(frozen=True)
class BreakoutCalibrationPilotResult:
    dataset_manifest: BreakoutCalibrationDatasetManifest
    equity: BreakoutClassCalibrationResult
    fixed_income: BreakoutClassCalibrationResult

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
    sample_kind: BreakoutSampleKind,
    label: PatternReviewLabel,
    expected_state: BreakoutExpectedState,
) -> BreakoutPilotSample:
    return BreakoutPilotSample(
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


def build_breakout_pilot_samples() -> tuple[BreakoutPilotSample, ...]:
    """Return the frozen, definition-labeled Stage 1D-1 pilot sample catalog."""

    development = CalibrationPartition.DEVELOPMENT
    holdout = CalibrationPartition.HOLDOUT
    untouched = CalibrationPartition.UNTOUCHED_VALIDATION
    positive = PatternReviewLabel.POSITIVE
    negative = PatternReviewLabel.NEGATIVE
    confirmed = BreakoutExpectedState.CONFIRMED
    not_confirmed = BreakoutExpectedState.NOT_CONFIRMED
    failed = BreakoutExpectedState.CONFIRMED_THEN_INVALIDATED
    return (
        _sample(
            "eq-dev-clean",
            "AAPL",
            1001,
            "EQUITY",
            AssetCoverage.COMMON_STOCK,
            development,
            date(2010, 1, 4),
            MarketRegime.BULL,
            (MarketEdgeCase.SPLIT,),
            BreakoutSampleKind.CLEAN_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "eq-dev-fake",
            "SPY",
            1002,
            "EQUITY",
            AssetCoverage.BROAD_MARKET_ETF,
            development,
            date(2010, 5, 3),
            MarketRegime.SIDEWAYS,
            (MarketEdgeCase.HOLIDAY,),
            BreakoutSampleKind.FAKE_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "eq-dev-low-volume",
            "XLK",
            1003,
            "EQUITY",
            AssetCoverage.SECTOR_ETF,
            development,
            date(2011, 1, 3),
            MarketRegime.LOW_VOLATILITY,
            (MarketEdgeCase.DIVIDEND,),
            BreakoutSampleKind.LOW_VOLUME_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "eq-dev-gap",
            "AAPL",
            1001,
            "EQUITY",
            AssetCoverage.COMMON_STOCK,
            development,
            date(2011, 5, 2),
            MarketRegime.HIGH_VOLATILITY,
            (MarketEdgeCase.EARNINGS_GAP, MarketEdgeCase.OVERNIGHT_GAP),
            BreakoutSampleKind.GAP_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "eq-dev-insufficient-structure",
            "SPY",
            1002,
            "EQUITY",
            AssetCoverage.BROAD_MARKET_ETF,
            development,
            date(2012, 1, 3),
            MarketRegime.BEAR,
            (MarketEdgeCase.HALF_DAY,),
            BreakoutSampleKind.INSUFFICIENT_STRUCTURE,
            negative,
            not_confirmed,
        ),
        _sample(
            "eq-dev-failed",
            "XLK",
            1003,
            "EQUITY",
            AssetCoverage.SECTOR_ETF,
            development,
            date(2012, 5, 1),
            MarketRegime.HIGH_VOLATILITY,
            (MarketEdgeCase.LOW_LIQUIDITY,),
            BreakoutSampleKind.FAILED_BREAKOUT,
            positive,
            failed,
        ),
        _sample(
            "eq-holdout-clean",
            "AAPL",
            1001,
            "EQUITY",
            AssetCoverage.COMMON_STOCK,
            holdout,
            date(2015, 1, 2),
            MarketRegime.BULL,
            (),
            BreakoutSampleKind.CLEAN_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "eq-holdout-low-volume",
            "SPY",
            1002,
            "EQUITY",
            AssetCoverage.BROAD_MARKET_ETF,
            holdout,
            date(2015, 5, 1),
            MarketRegime.LOW_VOLATILITY,
            (),
            BreakoutSampleKind.LOW_VOLUME_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "eq-holdout-failed",
            "XLK",
            1003,
            "EQUITY",
            AssetCoverage.SECTOR_ETF,
            holdout,
            date(2016, 1, 4),
            MarketRegime.HIGH_VOLATILITY,
            (),
            BreakoutSampleKind.FAILED_BREAKOUT,
            positive,
            failed,
        ),
        _sample(
            "eq-validation-gap",
            "SPY",
            1002,
            "EQUITY",
            AssetCoverage.BROAD_MARKET_ETF,
            untouched,
            date(2019, 1, 2),
            MarketRegime.HIGH_VOLATILITY,
            (),
            BreakoutSampleKind.GAP_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "eq-validation-fake",
            "AAPL",
            1001,
            "EQUITY",
            AssetCoverage.COMMON_STOCK,
            untouched,
            date(2019, 5, 1),
            MarketRegime.SIDEWAYS,
            (),
            BreakoutSampleKind.FAKE_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "eq-validation-insufficient",
            "XLK",
            1003,
            "EQUITY",
            AssetCoverage.SECTOR_ETF,
            untouched,
            date(2020, 1, 2),
            MarketRegime.BEAR,
            (),
            BreakoutSampleKind.INSUFFICIENT_STRUCTURE,
            negative,
            not_confirmed,
        ),
        _sample(
            "fi-dev-clean",
            "AGG",
            2001,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            development,
            date(2010, 1, 4),
            MarketRegime.BULL,
            (MarketEdgeCase.SPLIT, MarketEdgeCase.DIVIDEND),
            BreakoutSampleKind.CLEAN_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "fi-dev-low-volume",
            "TLT",
            2002,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            development,
            date(2010, 5, 3),
            MarketRegime.BEAR,
            (MarketEdgeCase.OVERNIGHT_GAP,),
            BreakoutSampleKind.LOW_VOLUME_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "fi-dev-failed",
            "LQD",
            2003,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            development,
            date(2011, 1, 3),
            MarketRegime.SIDEWAYS,
            (MarketEdgeCase.HOLIDAY,),
            BreakoutSampleKind.FAILED_BREAKOUT,
            positive,
            failed,
        ),
        _sample(
            "fi-dev-insufficient",
            "AGG",
            2001,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            development,
            date(2011, 5, 2),
            MarketRegime.HIGH_VOLATILITY,
            (MarketEdgeCase.HALF_DAY,),
            BreakoutSampleKind.INSUFFICIENT_STRUCTURE,
            negative,
            not_confirmed,
        ),
        _sample(
            "fi-dev-gap",
            "TLT",
            2002,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            development,
            date(2012, 1, 3),
            MarketRegime.LOW_VOLATILITY,
            (MarketEdgeCase.LOW_LIQUIDITY,),
            BreakoutSampleKind.GAP_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "fi-holdout-clean",
            "LQD",
            2003,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            holdout,
            date(2015, 1, 2),
            MarketRegime.BULL,
            (),
            BreakoutSampleKind.CLEAN_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "fi-holdout-fake",
            "AGG",
            2001,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            holdout,
            date(2016, 1, 4),
            MarketRegime.SIDEWAYS,
            (),
            BreakoutSampleKind.FAKE_BREAKOUT,
            negative,
            not_confirmed,
        ),
        _sample(
            "fi-validation-gap",
            "TLT",
            2002,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            untouched,
            date(2019, 1, 2),
            MarketRegime.HIGH_VOLATILITY,
            (),
            BreakoutSampleKind.GAP_BREAKOUT,
            positive,
            confirmed,
        ),
        _sample(
            "fi-validation-insufficient",
            "LQD",
            2003,
            "FIXED_INCOME",
            AssetCoverage.FIXED_INCOME_ETF,
            untouched,
            date(2020, 1, 2),
            MarketRegime.LOW_VOLATILITY,
            (),
            BreakoutSampleKind.INSUFFICIENT_STRUCTURE,
            negative,
            not_confirmed,
        ),
    )


def build_breakout_calibration_dataset_manifest() -> BreakoutCalibrationDatasetManifest:
    samples = build_breakout_pilot_samples()

    def exact_manifest(asset_class: str) -> CalibrationDatasetManifest:
        datasets = tuple(
            item.dataset()
            for item in samples
            if item.economic_asset_class == asset_class
        )
        return CalibrationDatasetManifest(
            pattern_family="level_break",
            pattern_type="breakout",
            manifest_version=f"{BREAKOUT_PILOT_DATASET_VERSION}-{asset_class.lower()}",
            datasets=datasets,
        )

    return BreakoutCalibrationDatasetManifest(
        dataset_version=BREAKOUT_PILOT_DATASET_VERSION,
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
        ("parameter_origin", "stage1d1_definition_fixture_pilot"),
        ("zone_atr_width_multiplier", 0.20),
        ("zone_width_pct", 0.15 if fixed_income else 0.25),
        ("volume_average_bars", 20),
        ("volume_ratio_threshold", 1.25 if fixed_income else 1.50),
    )


def build_breakout_pilot_parameters(
    economic_asset_class: str,
    *,
    attempt_number: int,
) -> DetectorParameterSet:
    asset_class = economic_asset_class.strip().upper()
    if attempt_number not in {1, 2}:
        raise ValueError("Breakout pilot defines exactly two development attempts")
    key = CalibrationKey(
        market="US",
        economic_asset_class=asset_class,
        timeframe="1d",
        pattern_family="level_break",
        pattern_type="breakout",
        calibration_version=BREAKOUT_PILOT_CALIBRATION_VERSION,
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


def _run_sample(
    sample: BreakoutPilotSample,
    parameters: DetectorParameterSet,
) -> BreakoutSampleOutcome:
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    )
    result = framework.run(
        sample.core_input(),
        evaluation_session_ordinal=sample.evaluation_ordinal,
        calibration_key=parameters.key,
        detector=BreakoutDetector(),
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
        sample.expected_state is not BreakoutExpectedState.CONFIRMED_THEN_INVALIDATED
        or invalidated
    )
    return BreakoutSampleOutcome(
        sample_id=sample.dataset().dataset_id,
        sample_name=sample.sample_name,
        partition=sample.partition,
        label=sample.label,
        sample_kind=sample.sample_kind,
        expected_state=sample.expected_state,
        candidate_count=len(result.results),
        direction_confirmed=direction_confirmed,
        invalidated=invalidated,
        definition_conforms=not false_positive and not false_negative and state_matches,
        false_positive=false_positive,
        false_negative=false_negative,
        detector_result_hash=result.result_hash,
    )


def _reviews(
    samples: tuple[BreakoutPilotSample, ...],
    outcomes: tuple[BreakoutSampleOutcome, ...],
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
                "financial outcomes were not considered."
            ),
            reviewed_on=reviewed_on,
        )
        for sample in samples
    )


def _evaluation(
    version: FrozenCalibrationVersion,
    manifest: CalibrationDatasetManifest,
    samples: tuple[BreakoutPilotSample, ...],
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
    failure_modes = tuple(
        f"{item.sample_name}:definition_mismatch"
        for item in outcomes
        if not item.definition_conforms
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
        failure_modes=failure_modes,
        completed_on=completed_on,
    )


def _execute_asset_class(
    dataset_manifest: BreakoutCalibrationDatasetManifest,
    validation: CalibrationValidationFramework,
    economic_asset_class: str,
) -> BreakoutClassCalibrationResult:
    manifest = dataset_manifest.exact_manifest(economic_asset_class)
    development_samples = dataset_manifest.partition_samples(
        economic_asset_class, CalibrationPartition.DEVELOPMENT
    )
    parameter_attempts: list[BreakoutParameterAttemptResult] = []
    attempt_records: list[CalibrationAttemptRecord] = []
    for attempt_number in (1, 2):
        parameters = build_breakout_pilot_parameters(
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
                "Evaluate the inherited one-touch boundary hypothesis"
                if attempt_number == 1
                else "Require two available resistance touches after the development "
                "insufficient-structure false positive"
            ),
            attempted_on=date(2026, 8, attempt_number),
        )
        attempt_records.append(attempt)
        parameter_attempts.append(
            BreakoutParameterAttemptResult(attempt, parameters, outcomes)
        )

    final_parameters = parameter_attempts[-1].parameters
    version = validation.register_version(
        final_parameters,
        manifest,
        tuple(attempt_records),
        frozen_on=date(2026, 8, 3),
    )
    holdout_samples = dataset_manifest.partition_samples(
        economic_asset_class, CalibrationPartition.HOLDOUT
    )
    holdout = _evaluation(
        version,
        manifest,
        holdout_samples,
        final_parameters,
        partition=CalibrationPartition.HOLDOUT,
        completed_on=date(2026, 8, 4),
    )
    validation.record_evaluation(holdout)
    untouched_samples = dataset_manifest.partition_samples(
        economic_asset_class, CalibrationPartition.UNTOUCHED_VALIDATION
    )
    untouched = _evaluation(
        version,
        manifest,
        untouched_samples,
        final_parameters,
        partition=CalibrationPartition.UNTOUCHED_VALIDATION,
        completed_on=date(2026, 8, 5),
    )
    validation.record_evaluation(untouched)
    return BreakoutClassCalibrationResult(
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


def execute_breakout_calibration_pilot() -> BreakoutCalibrationPilotResult:
    """Run the reproducible process pilot without external data or mutations."""

    dataset_manifest = build_breakout_calibration_dataset_manifest()
    validation = CalibrationValidationFramework()
    equity = _execute_asset_class(dataset_manifest, validation, "EQUITY")
    fixed_income = _execute_asset_class(
        dataset_manifest,
        validation,
        "FIXED_INCOME",
    )
    return BreakoutCalibrationPilotResult(dataset_manifest, equity, fixed_income)
