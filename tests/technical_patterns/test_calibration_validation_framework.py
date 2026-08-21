from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import (
    SIX_PATTERN_BINDINGS,
    AssetCoverage,
    CalibrationAttemptRecord,
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationKey,
    CalibrationPartition,
    CalibrationValidationFramework,
    CalibrationWorkflowError,
    DatasetReviewStatus,
    DetectorParameterSet,
    MarketEdgeCase,
    MarketRegime,
    PatternReviewLabel,
    PatternSampleReview,
    PatternValidationEvaluation,
    PatternValidationReport,
    PromotionRecommendation,
    build_us_ascending_triangle_development_parameter_sets,
    build_us_double_reversal_development_parameter_sets,
    build_us_level_break_development_parameter_sets,
    build_us_rectangle_development_parameter_sets,
)


def _key(*, version: str = "us-breakout-calibration-v1") -> CalibrationKey:
    return CalibrationKey(
        "US", "EQUITY", "1d", "level_break", "breakout", version
    )


def _parameters(*, version: str = "us-breakout-calibration-v1", threshold: float = 1.1):
    return DetectorParameterSet(
        _key(version=version),
        (("definition_threshold", threshold), ("parameter_origin", "development_review")),
        minimum_history_bars=40,
    )


def _dataset(
    instrument: str,
    partition: CalibrationPartition,
    date_range: tuple[date, date],
    asset: AssetCoverage,
    regimes: tuple[MarketRegime, ...],
    edges: tuple[MarketEdgeCase, ...] = (),
    *,
    source_hash: str | None = None,
    review_status: DatasetReviewStatus | None = None,
    label: PatternReviewLabel | None = None,
    economic_asset_class: str = "equity",
) -> CalibrationDataset:
    if review_status is None:
        review_status = (
            DatasetReviewStatus.COMPLETED
            if partition is CalibrationPartition.DEVELOPMENT
            else DatasetReviewStatus.SEALED
        )
    if label is None and review_status is DatasetReviewStatus.COMPLETED:
        label = PatternReviewLabel.POSITIVE
    return CalibrationDataset(
        instrument=instrument,
        market="us",
        economic_asset_class=economic_asset_class,
        timeframe="1D",
        date_range=date_range,
        source_provider="IBKR Historical Data",
        source_bar_hash=source_hash or f"sha256:{instrument}:{partition.value}",
        adjustment_policy="IBKR_TRADES_SPLIT_ADJUSTED_NOT_DIVIDEND_ADJUSTED",
        calendar_version="XNYS-v2026.08",
        label=label,
        partition=partition,
        review_status=review_status,
        asset_coverage=asset,
        market_regimes=regimes,
        edge_cases=edges,
    )


def _manifest(*, complete_coverage: bool = True, version: str = "manifest-v1"):
    development = (
        _dataset(
            "AAPL",
            CalibrationPartition.DEVELOPMENT,
            (date(2010, 1, 4), date(2013, 12, 31)),
            AssetCoverage.COMMON_STOCK,
            (MarketRegime.BULL, MarketRegime.HIGH_VOLATILITY),
            (
                MarketEdgeCase.EARNINGS_GAP,
                MarketEdgeCase.OVERNIGHT_GAP,
                MarketEdgeCase.SPLIT,
            ),
        ),
        _dataset(
            "SPY",
            CalibrationPartition.DEVELOPMENT,
            (date(2010, 1, 4), date(2013, 12, 31)),
            AssetCoverage.BROAD_MARKET_ETF,
            (MarketRegime.BEAR, MarketRegime.LOW_VOLATILITY),
            (MarketEdgeCase.DIVIDEND, MarketEdgeCase.HOLIDAY),
        ),
    )
    holdout_asset = (
        AssetCoverage.SECTOR_ETF
        if complete_coverage
        else AssetCoverage.COMMON_STOCK
    )
    holdout = _dataset(
        "XLK",
        CalibrationPartition.HOLDOUT,
        (date(2014, 1, 2), date(2016, 12, 30)),
        holdout_asset,
        (MarketRegime.SIDEWAYS,),
        (MarketEdgeCase.HALF_DAY,),
    )
    untouched = _dataset(
        "XBI",
        CalibrationPartition.UNTOUCHED_VALIDATION,
        (date(2017, 1, 3), date(2020, 12, 31)),
        AssetCoverage.SECTOR_ETF,
        (MarketRegime.HIGH_VOLATILITY, MarketRegime.LOW_VOLATILITY),
        (MarketEdgeCase.LOW_LIQUIDITY,),
    )
    return CalibrationDatasetManifest(
        "level_break", "breakout", version, development + (holdout, untouched)
    )


def _attempt(parameters, manifest, *, number: int = 1, attempted_on=date(2026, 1, 10)):
    return CalibrationAttemptRecord(
        calibration_key=parameters.key,
        attempt_number=number,
        parameters_hash=parameters.parameters_hash,
        development_partition_hash=manifest.partition_hash(CalibrationPartition.DEVELOPMENT),
        based_on_partitions=(CalibrationPartition.DEVELOPMENT,),
        development_review_ids=(f"development-review-{number}",),
        change_reason="Align detector definition with reviewed US structure evidence",
        attempted_on=attempted_on,
    )


def _register(*, complete_coverage: bool = True):
    parameters = _parameters()
    manifest = _manifest(complete_coverage=complete_coverage)
    framework = CalibrationValidationFramework()
    version = framework.register_version(
        parameters,
        manifest,
        (_attempt(parameters, manifest),),
        frozen_on=date(2026, 1, 15),
    )
    return framework, parameters, manifest, version


def _reviews(
    manifest,
    partition,
    *,
    label=PatternReviewLabel.POSITIVE,
    reviewed_on=date(2026, 2, 1),
):
    return tuple(
        PatternSampleReview(
            dataset_id=item.dataset_id,
            partition=partition,
            reviewer_ids=("reviewer-a",),
            label=label,
            definition_conforms=True if label is PatternReviewLabel.POSITIVE else False,
            false_positive=False,
            false_negative=False,
            boundary_ambiguous=label is PatternReviewLabel.AMBIGUOUS,
            notes="Definition-focused review; no return metric used.",
            reviewed_on=reviewed_on,
        )
        for item in manifest.partition(partition)
    )


def _evaluation(
    version,
    manifest,
    partition,
    *,
    label=PatternReviewLabel.POSITIVE,
    completed_on=date(2026, 2, 1),
    passed=True,
):
    return PatternValidationEvaluation(
        calibration_version_id=version.version_id,
        partition=partition,
        parameters_hash=version.parameters_hash,
        dataset_manifest_hash=version.dataset_manifest_hash,
        partition_hash=manifest.partition_hash(partition),
        reviews=_reviews(
            manifest, partition, label=label, reviewed_on=completed_on
        ),
        definition_review_pass=passed,
        false_positive_review_pass=passed,
        false_negative_review_pass=passed,
        boundary_review_pass=passed,
        human_review_pass=passed,
        failure_modes=() if passed else ("boundary_definition_mismatch",),
        completed_on=completed_on,
    )


def test_dataset_manifest_contains_required_authority_review_and_coverage_fields():
    manifest = _manifest()
    sample = manifest.datasets[0]
    assert sample.instrument == "AAPL"
    assert sample.market == "US"
    assert sample.economic_asset_class == "EQUITY"
    assert sample.timeframe == "1d"
    assert sample.source_provider == "IBKR Historical Data"
    assert sample.source_bar_hash.startswith("sha256:")
    assert sample.adjustment_policy
    assert sample.calendar_version
    assert sample.label is PatternReviewLabel.POSITIVE
    assert sample.review_status is DatasetReviewStatus.COMPLETED
    assert sample.dataset_id.startswith("calitem_")
    assert manifest.manifest_id.startswith("caldata_")
    assert len(manifest.manifest_hash) == 64
    assert manifest.coverage_gaps() == ()


def test_manifest_requires_sealed_labels_and_chronological_partition_separation():
    manifest = _manifest()
    assert manifest.partition(CalibrationPartition.HOLDOUT)[0].label is None
    assert manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION)[0].label is None
    exposed_holdout = replace(
        manifest.partition(CalibrationPartition.HOLDOUT)[0],
        review_status=DatasetReviewStatus.COMPLETED,
        label=PatternReviewLabel.POSITIVE,
        dataset_id="",
    )
    with pytest.raises(ValueError, match="remain sealed"):
        CalibrationDatasetManifest(
            manifest.pattern_family,
            manifest.pattern_type,
            manifest.manifest_version,
            manifest.partition(CalibrationPartition.DEVELOPMENT)
            + (exposed_holdout,)
            + manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION),
        )

    early_holdout = replace(
        manifest.partition(CalibrationPartition.HOLDOUT)[0],
        date_range=(date(2013, 1, 2), date(2015, 12, 31)),
        dataset_id="",
    )
    with pytest.raises(ValueError, match="development < holdout"):
        CalibrationDatasetManifest(
            manifest.pattern_family,
            manifest.pattern_type,
            manifest.manifest_version,
            manifest.partition(CalibrationPartition.DEVELOPMENT)
            + (early_holdout,)
            + manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION),
        )


def test_manifest_rejects_duplicate_source_hash_and_reports_coverage_gaps():
    manifest = _manifest(complete_coverage=False)
    assert "asset:sector_etf" not in manifest.coverage_gaps()  # untouched still covers it
    reduced = replace(
        manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION)[0],
        asset_coverage=AssetCoverage.COMMON_STOCK,
        edge_cases=(),
        dataset_id="",
    )
    incomplete = CalibrationDatasetManifest(
        manifest.pattern_family,
        manifest.pattern_type,
        manifest.manifest_version,
        manifest.partition(CalibrationPartition.DEVELOPMENT)
        + manifest.partition(CalibrationPartition.HOLDOUT)
        + (reduced,),
    )
    assert "asset:sector_etf" in incomplete.coverage_gaps()
    assert "edge_case:low_liquidity" in incomplete.coverage_gaps()

    duplicate = replace(
        manifest.partition(CalibrationPartition.HOLDOUT)[0],
        source_bar_hash=manifest.partition(CalibrationPartition.DEVELOPMENT)[0].source_bar_hash,
        dataset_id="",
    )
    with pytest.raises(ValueError, match="source bar hashes must be disjoint"):
        CalibrationDatasetManifest(
            manifest.pattern_family,
            manifest.pattern_type,
            manifest.manifest_version,
            manifest.partition(CalibrationPartition.DEVELOPMENT)
            + (duplicate,)
            + manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION),
        )


def test_fixed_income_manifest_uses_asset_appropriate_edge_coverage():
    development = _dataset(
        "CBU3",
        CalibrationPartition.DEVELOPMENT,
        (date(2010, 1, 4), date(2013, 12, 31)),
        AssetCoverage.FIXED_INCOME_ETF,
        (MarketRegime.BULL, MarketRegime.BEAR),
        (
            MarketEdgeCase.OVERNIGHT_GAP,
            MarketEdgeCase.SPLIT,
            MarketEdgeCase.DIVIDEND,
        ),
        economic_asset_class="fixed_income",
    )
    holdout = _dataset(
        "IB01",
        CalibrationPartition.HOLDOUT,
        (date(2014, 1, 2), date(2016, 12, 30)),
        AssetCoverage.FIXED_INCOME_ETF,
        (MarketRegime.SIDEWAYS, MarketRegime.HIGH_VOLATILITY),
        (MarketEdgeCase.HOLIDAY, MarketEdgeCase.HALF_DAY),
        economic_asset_class="fixed_income",
    )
    untouched = _dataset(
        "AGG",
        CalibrationPartition.UNTOUCHED_VALIDATION,
        (date(2017, 1, 3), date(2020, 12, 31)),
        AssetCoverage.FIXED_INCOME_ETF,
        (MarketRegime.LOW_VOLATILITY,),
        (MarketEdgeCase.LOW_LIQUIDITY,),
        economic_asset_class="fixed_income",
    )
    manifest = CalibrationDatasetManifest(
        "level_break", "breakout", "fixed-income-v1", (development, holdout, untouched)
    )
    assert manifest.economic_asset_class == "FIXED_INCOME"
    assert manifest.coverage_gaps() == ()


def test_parameter_attempts_are_development_only_and_record_review_history():
    parameters = _parameters()
    manifest = _manifest()
    attempt = _attempt(parameters, manifest)
    assert attempt.attempt_number == 1
    assert attempt.development_review_ids == ("development-review-1",)
    with pytest.raises(ValueError, match="development evidence only"):
        replace(
            attempt,
            based_on_partitions=(
                CalibrationPartition.DEVELOPMENT,
                CalibrationPartition.HOLDOUT,
            ),
            attempt_id="",
        )


def test_version_registry_freezes_parameter_and_manifest_hashes_immutably():
    framework, parameters, manifest, version = _register()
    repeated = framework.register_version(
        parameters,
        manifest,
        (_attempt(parameters, manifest),),
        frozen_on=date(2026, 1, 15),
    )
    assert repeated == version
    assert version.parameters_hash == parameters.parameters_hash
    assert version.dataset_manifest_hash == manifest.manifest_hash
    assert version.attempt_count == 1

    changed = _parameters(threshold=1.2)
    with pytest.raises(CalibrationWorkflowError, match="immutable"):
        framework.register_version(
            changed,
            manifest,
            (_attempt(changed, manifest),),
            frozen_on=date(2026, 1, 16),
        )


def test_holdout_and_untouched_validation_require_frozen_hashes_and_order():
    framework, _, manifest, version = _register()
    untouched = _evaluation(
        version,
        manifest,
        CalibrationPartition.UNTOUCHED_VALIDATION,
        completed_on=date(2026, 3, 1),
    )
    with pytest.raises(CalibrationWorkflowError, match="before frozen holdout passes"):
        framework.record_evaluation(untouched)

    drifted = replace(
        _evaluation(version, manifest, CalibrationPartition.HOLDOUT),
        parameters_hash="different-parameters",
        evaluation_id="",
    )
    with pytest.raises(CalibrationWorkflowError, match="hash drifted"):
        framework.record_evaluation(drifted)

    holdout = _evaluation(version, manifest, CalibrationPartition.HOLDOUT)
    framework.record_evaluation(holdout)
    framework.record_evaluation(untouched)
    assessment = framework.promotion_assessment(version.version_id, detector_pass=True)
    assert assessment.holdout_pass is True
    assert assessment.untouched_validation_pass is True


def test_failed_or_incomplete_review_blocks_untouched_and_promotion():
    framework, _, manifest, version = _register()
    failed = _evaluation(
        version, manifest, CalibrationPartition.HOLDOUT, passed=False
    )
    framework.record_evaluation(failed)
    with pytest.raises(CalibrationWorkflowError, match="before frozen holdout passes"):
        framework.record_evaluation(
            _evaluation(
                version,
                manifest,
                CalibrationPartition.UNTOUCHED_VALIDATION,
                completed_on=date(2026, 3, 1),
            )
        )
    assessment = framework.promotion_assessment(version.version_id, detector_pass=True)
    assert assessment.eligible_for_governance_review is False
    assert "holdout_not_passed" in assessment.blocking_reasons
    assert "untouched_validation_not_passed" in assessment.blocking_reasons


def test_review_disagreement_is_preserved_and_cannot_be_marked_passed():
    framework, _, manifest, version = _register()
    partition = CalibrationPartition.HOLDOUT
    dataset = manifest.partition(partition)[0]
    disagreement = PatternSampleReview(
        dataset.dataset_id,
        partition,
        ("reviewer-a", "reviewer-b"),
        PatternReviewLabel.REVIEW_DISAGREEMENT,
        None,
        False,
        False,
        True,
        "Reviewers disagree on the boundary definition.",
        date(2026, 2, 1),
    )
    evaluation = PatternValidationEvaluation(
        version.version_id,
        partition,
        version.parameters_hash,
        version.dataset_manifest_hash,
        manifest.partition_hash(partition),
        (disagreement,),
        True,
        True,
        True,
        True,
        True,
        ("boundary_review_disagreement",),
        date(2026, 2, 1),
    )
    assert evaluation.passed is False
    framework.record_evaluation(evaluation)
    assert framework.promotion_assessment(
        version.version_id, detector_pass=True
    ).eligible_for_governance_review is False


def test_previously_opened_holdout_cannot_be_reused_as_unseen_after_tuning():
    framework, _, manifest, version = _register()
    framework.record_evaluation(
        _evaluation(version, manifest, CalibrationPartition.HOLDOUT)
    )
    tuned = _parameters(version="us-breakout-calibration-v2", threshold=1.2)
    tuned_manifest = replace(manifest, manifest_version="manifest-v2", manifest_id="")
    with pytest.raises(CalibrationWorkflowError, match="cannot be reused as unseen"):
        framework.register_version(
            tuned,
            tuned_manifest,
            (_attempt(tuned, tuned_manifest),),
            frozen_on=date(2026, 2, 15),
        )


def test_full_definition_validation_yields_review_eligibility_not_auto_promotion():
    framework, _, manifest, version = _register()
    framework.record_evaluation(
        _evaluation(version, manifest, CalibrationPartition.HOLDOUT)
    )
    framework.record_evaluation(
        _evaluation(
            version,
            manifest,
            CalibrationPartition.UNTOUCHED_VALIDATION,
            label=PatternReviewLabel.NEGATIVE,
            completed_on=date(2026, 3, 1),
        )
    )
    assessment = framework.promotion_assessment(version.version_id, detector_pass=True)
    report = framework.build_report(version.version_id, detector_pass=True)

    assert assessment.eligible_for_governance_review is True
    assert assessment.blocking_reasons == ()
    assert report.positive_cases == 1
    assert report.negative_cases == 1
    assert report.ambiguous_cases == 0
    assert report.failure_modes == ()
    assert report.promotion_recommendation is (
        PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
    )
    assert report.report_id.startswith("calreport_")


def test_code_and_golden_parity_alone_never_pass_production_gate():
    framework, _, _, version = _register()
    assessment = framework.promotion_assessment(version.version_id, detector_pass=True)
    assert assessment.eligible_for_governance_review is False
    assert assessment.blocking_reasons == (
        "holdout_not_passed",
        "untouched_validation_not_passed",
        "human_review_not_passed",
    )


def test_six_launch_patterns_have_independent_exact_development_calibrations():
    parameter_sets = (
        build_us_level_break_development_parameter_sets()
        + build_us_rectangle_development_parameter_sets()
        + build_us_ascending_triangle_development_parameter_sets()
        + build_us_double_reversal_development_parameter_sets()
    )
    bindings = {
        (item.key.pattern_family, item.key.pattern_type) for item in parameter_sets
    }
    assert tuple(sorted(bindings)) == tuple(sorted(SIX_PATTERN_BINDINGS))
    assert len(parameter_sets) == 12
    assert all(
        item.require("calibration_stage") == "development_only"
        for item in parameter_sets
    )


def test_validation_contract_has_no_profit_ranking_probability_or_execution_semantics():
    forbidden_fields = {"profit", "return", "win_rate", "ranking", "probability"}
    report_fields = {item.name for item in fields(PatternValidationReport)}
    assert not forbidden_fields & report_fields
    package = (
        Path(__file__).parents[2]
        / "backend/services/technical_patterns/calibration"
    )
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in package.glob("*.py")
    ).lower()
    forbidden_imports = (
        "backend.services.action",
        "backend.services.portfolio",
        "backend.services.decision",
        "ib_async",
        "placeorder",
    )
    assert all(item not in sources for item in forbidden_imports)
