"""Read-only current-IBKR runtime provider for promoted Pattern scopes only."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Callable

from backend.services.pattern_data import CanonicalPatternSeries, InstrumentQuery
from backend.services.pattern_data.contracts import build_source_bar_hash
from backend.services.pattern_data.ibkr_adapter import (
    IBKRPatternAdapterConfig,
    IBKRPatternDataAdapter,
)
from backend.services.pattern_data.ibkr_source import IBKRHistoricalDataSource

from .calibration import build_approved_runtime_calibration_registry
from .core.input_mapper import PatternInputMapper
from .core.lifecycle import LifecycleState
from .decision_integration import PatternDecisionTarget
from .detectors.framework import DetectorFramework
from .evidence import PatternEvidenceAdapter, PatternEvidenceBundle
from .indicators import TalibIndicatorLayer
from .real_review import _bindings


RUNTIME_BAR_WINDOW = 300


class PromotedIBKRPatternEvidenceProvider:
    """Evaluation uses Dataset v2; this provider always uses current IBKR data."""

    def __init__(
        self,
        source_factory: Callable[[], IBKRHistoricalDataSource] | None = None,
    ) -> None:
        self._registry = build_approved_runtime_calibration_registry()
        self._source_factory = source_factory or _default_source

    def collect(
        self,
        target: PatternDecisionTarget,
    ) -> tuple[PatternEvidenceBundle, ...]:
        candidates = tuple(
            item
            for item in self._registry.snapshot()
            if item.scope.market == target.market
            and item.scope.economic_asset_class == target.economic_asset_class
            and item.scope.timeframe == "1d"
        )
        if not candidates:
            return (
                PatternEvidenceAdapter.no_pattern(
                    target.unavailable_instrument,
                    reason="exact_runtime_pattern_scope_not_promoted",
                ),
            )

        source = self._source_factory()
        try:
            result = IBKRPatternDataAdapter(
                source,
                config=IBKRPatternAdapterConfig(
                    target_bar_count=RUNTIME_BAR_WINDOW,
                    durations=("2 Y",),
                    schedule_calendar_days=500,
                    schedule_page_sessions=365,
                ),
            ).get_series(
                InstrumentQuery(
                    symbol=target.symbol,
                    exchange="SMART",
                    currency=target.currency,
                ),
                refresh=True,
            )
        finally:
            source.shutdown()
        if not result.is_ready or result.series is None:
            return (
                PatternEvidenceAdapter.from_data_status(
                    target.unavailable_instrument,
                    result.status,
                    reason=result.reason or "current_ibkr_pattern_data_unavailable",
                ),
            )

        runtime_series = _bounded_runtime_series(result.series)
        core_input = PatternInputMapper().map_series(runtime_series)
        core_input = replace(core_input, market=target.market)
        framework = DetectorFramework(
            calibrations=_ApprovedCalibrationProvider(self._registry),
            indicators=TalibIndicatorLayer(),
        )
        bindings = {item[0]: item for item in _bindings()}
        bundles: list[PatternEvidenceBundle] = []
        for candidate in candidates:
            _, _, detector, structure, invalidation, direction = bindings[
                candidate.scope.pattern_type
            ]
            output = framework.run(
                core_input,
                evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
                calibration_key=candidate.parameters.key,
                detector=detector,
                structure_confirmation=structure,
                invalidation=invalidation,
                direction_confirmation=direction,
            )
            visible = tuple(
                item
                for item in output.results
                if item.lifecycle.state is not LifecycleState.CANDIDATE
            )
            if not visible:
                continue
            latest = max(
                visible,
                key=lambda item: (
                    item.candidate.available_from_session_ordinal,
                    item.candidate.candidate_id,
                ),
            )
            bundles.append(
                PatternEvidenceAdapter.from_pattern_result(
                    core_input,
                    latest,
                    economic_asset_class=target.economic_asset_class,
                    parameter_hash=candidate.final_parameter_hash,
                )
            )
        if bundles:
            return tuple(bundles)
        return (
            PatternEvidenceAdapter.no_pattern(
                PatternEvidenceAdapter.instrument(
                    core_input,
                    economic_asset_class=target.economic_asset_class,
                )
            ),
        )


class _ApprovedCalibrationProvider:
    def __init__(self, registry) -> None:
        self._registry = registry

    def resolve(self, key):
        from .calibration import RuntimeCalibrationScope

        return self._registry.resolve_parameters(RuntimeCalibrationScope.from_key(key))


def _default_source() -> IBKRHistoricalDataSource:
    return IBKRHistoricalDataSource(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", "4001")),
        client_id=int(os.getenv("IBKR_PATTERN_CLIENT_ID", "41")),
        timeout=float(os.getenv("IBKR_PATTERN_TIMEOUT", "15")),
    )


def _bounded_runtime_series(
    series: CanonicalPatternSeries,
) -> CanonicalPatternSeries:
    """Keep runtime below the Decision sidecar deadline without changing Core."""

    bars = series.bars[-RUNTIME_BAR_WINDOW:]
    return replace(
        series,
        bars=bars,
        source_bar_hash=build_source_bar_hash(bars),
    )
