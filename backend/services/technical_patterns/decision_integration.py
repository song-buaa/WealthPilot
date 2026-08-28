"""Read-only Pattern Evidence sidecar for the Decision pipeline.

This module owns invocation eligibility and transport only.  Detector,
calibration, ranking, AI projection, and execution authority remain with their
existing owners.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from backend.services.instruments.classification import economic_asset_class_value
from backend.utils.symbol import parse_symbol

from .core.identity import stable_hash
from .evidence import (
    PatternEvidenceBundle,
    PatternEvidenceResultState,
    PatternInstrumentIdentity,
    select_for_presentation,
)


logger = logging.getLogger(__name__)

DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION = (
    "wp-decision-pattern-evidence-snapshot-v1"
)

_COMPARE_PATTERN = re.compile(
    r"(?:比较|对比|哪个更(?:好|合适|适合)|哪(?:一|个)个更(?:好|合适|适合)|"
    r"孰优|优劣|\bvs\.?\b|\bversus\b|compare)",
    re.IGNORECASE,
)
_SWITCH_OR_MULTI_LEG_PATTERN = re.compile(
    r"(?:换仓|调仓|轮动|卖出?.{0,24}(?:买入?|加仓)|"
    r"(?:减仓|清仓).{0,24}(?:买入?|加仓))",
    re.IGNORECASE,
)

_MARKET_CURRENCY = {
    "US": "USD",
    "HK": "HKD",
    "SH": "CNY",
    "SZ": "CNY",
    "CN": "CNY",
}


class PatternInvocationScope(str, Enum):
    NONE = "NONE"
    SINGLE = "SINGLE"
    COMPARE = "COMPARE"


@dataclass(frozen=True)
class PatternDecisionTarget:
    """Existing Decision-resolved identity hints for one bounded provider call."""

    requested_symbol: str
    symbol: str
    market: str
    currency: str
    economic_asset_class: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.requested_symbol,
                self.symbol,
                self.market,
                self.currency,
                self.economic_asset_class,
            )
        ):
            raise ValueError("Decision Pattern target identity must be complete")

    @property
    def unavailable_instrument(self) -> PatternInstrumentIdentity:
        return PatternInstrumentIdentity(
            instrument_id=f"DECISION:{self.market}:{self.symbol}",
            symbol=self.symbol,
            market=self.market,
            economic_asset_class=self.economic_asset_class,
            currency=self.currency,
        )


class DecisionPatternEvidenceProvider(Protocol):
    """Injected runtime boundary; one call is scoped to one resolved symbol."""

    def collect(
        self,
        target: PatternDecisionTarget,
    ) -> tuple[PatternEvidenceBundle, ...]: ...


class UnavailableDecisionPatternEvidenceProvider:
    """Safe default until a production calibration assembly is approved."""

    def collect(
        self,
        target: PatternDecisionTarget,
    ) -> tuple[PatternEvidenceBundle, ...]:
        return (
            PatternEvidenceBundle(
                instrument=target.unavailable_instrument,
                timeframe="1d",
                result_state=PatternEvidenceResultState.DATA_UNAVAILABLE,
                reason="runtime_pattern_provider_not_promoted",
            ),
        )


def build_runtime_pattern_evidence_provider() -> DecisionPatternEvidenceProvider:
    """Build the current-IBKR provider backed by exact promoted v2 scopes."""

    if os.getenv("AV_DEV_MOCK", "0") == "1":
        return UnavailableDecisionPatternEvidenceProvider()
    from .runtime_provider import PromotedIBKRPatternEvidenceProvider

    return PromotedIBKRPatternEvidenceProvider()


def _bundle_requested_symbol(bundle: PatternEvidenceBundle) -> str:
    return f"{bundle.instrument.symbol}:{bundle.instrument.market}"


@dataclass(frozen=True)
class DecisionPatternEvidenceSnapshot:
    snapshot_schema_version: str
    invocation_scope: PatternInvocationScope
    requested_symbols: tuple[str, ...]
    bundles: tuple[PatternEvidenceBundle, ...]
    bundle_hashes: tuple[str, ...]
    top_evidence_candidate_ids: tuple[str, ...]
    remaining_evidence_candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.snapshot_schema_version
            != DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported Decision Pattern snapshot schema")
        if self.invocation_scope is PatternInvocationScope.NONE:
            raise ValueError("NONE scope must not create an evidence snapshot")
        expected_count = 1 if self.invocation_scope is PatternInvocationScope.SINGLE else None
        if expected_count is not None and len(self.requested_symbols) != expected_count:
            raise ValueError("SINGLE scope requires exactly one requested symbol")
        if self.invocation_scope is PatternInvocationScope.COMPARE and not (
            2 <= len(self.requested_symbols) <= 3
        ):
            raise ValueError("COMPARE scope requires two or three requested symbols")
        if len(set(self.requested_symbols)) != len(self.requested_symbols):
            raise ValueError("Requested symbols must be unique")
        expected_hashes = tuple(bundle.bundle_hash for bundle in self.bundles)
        if self.bundle_hashes != expected_hashes:
            raise ValueError("Bundle hashes do not match canonical bundles")

        found_ids = {
            bundle.evidence.pattern.candidate_id
            for bundle in self.bundles
            if bundle.result_state is PatternEvidenceResultState.PATTERN_FOUND
            and bundle.evidence is not None
        }
        selected_ids = (
            self.top_evidence_candidate_ids
            + self.remaining_evidence_candidate_ids
        )
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("Presentation candidate IDs must be unique")
        if not set(selected_ids).issubset(found_ids):
            raise ValueError("Presentation IDs must reference found evidence")

    @classmethod
    def from_bundles(
        cls,
        invocation_scope: PatternInvocationScope,
        requested_symbols: tuple[str, ...],
        bundles: tuple[PatternEvidenceBundle, ...],
    ) -> "DecisionPatternEvidenceSnapshot":
        requested_order = {
            symbol: index for index, symbol in enumerate(requested_symbols)
        }
        ordered = tuple(
            sorted(
                bundles,
                key=lambda bundle: (
                    requested_order.get(_bundle_requested_symbol(bundle), 999),
                    bundle.bundle_hash,
                ),
            )
        )
        selection = select_for_presentation(ordered)
        top_ids = tuple(
            item.evidence.pattern.candidate_id
            for item in selection.top_evidence
            if item.evidence is not None
        )
        remaining_ids = tuple(
            item.evidence.pattern.candidate_id
            for item in selection.remaining_evidence
            if item.evidence is not None
        )
        return cls(
            snapshot_schema_version=(
                DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION
            ),
            invocation_scope=invocation_scope,
            requested_symbols=requested_symbols,
            bundles=ordered,
            bundle_hashes=tuple(item.bundle_hash for item in ordered),
            top_evidence_candidate_ids=top_ids,
            remaining_evidence_candidate_ids=remaining_ids,
        )

    @property
    def snapshot_hash(self) -> str:
        return stable_hash(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_schema_version": self.snapshot_schema_version,
            "invocation_scope": self.invocation_scope.value,
            "requested_symbols": list(self.requested_symbols),
            "bundles": [bundle.as_dict() for bundle in self.bundles],
            "bundle_hashes": list(self.bundle_hashes),
            "top_evidence_candidate_ids": list(
                self.top_evidence_candidate_ids
            ),
            "remaining_evidence_candidate_ids": list(
                self.remaining_evidence_candidate_ids
            ),
        }


def _engine_error_bundle(
    target: PatternDecisionTarget,
    reason: str,
) -> PatternEvidenceBundle:
    return PatternEvidenceBundle(
        instrument=target.unavailable_instrument,
        timeframe="1d",
        result_state=PatternEvidenceResultState.ENGINE_ERROR,
        reason=reason,
    )


def _data_unavailable_bundle(
    target: PatternDecisionTarget,
    reason: str,
) -> PatternEvidenceBundle:
    return PatternEvidenceBundle(
        instrument=target.unavailable_instrument,
        timeframe="1d",
        result_state=PatternEvidenceResultState.DATA_UNAVAILABLE,
        reason=reason,
    )


class DecisionPatternEvidenceCollector:
    """Bounded, fail-open collection around an injected read-only provider."""

    def __init__(
        self,
        provider_factory: Callable[[], DecisionPatternEvidenceProvider] = (
            build_runtime_pattern_evidence_provider
        ),
    ) -> None:
        self._provider_factory = provider_factory

    def collect(
        self,
        invocation_scope: PatternInvocationScope,
        targets: tuple[PatternDecisionTarget, ...],
    ) -> DecisionPatternEvidenceSnapshot | None:
        if invocation_scope is PatternInvocationScope.NONE:
            return None

        try:
            provider = self._provider_factory()
        except Exception as exc:  # noqa: BLE001 - explicit sidecar boundary
            logger.warning(
                "Pattern provider construction failed: %s", type(exc).__name__
            )
            bundles = tuple(
                _engine_error_bundle(target, "provider_construction_error")
                for target in targets
            )
            return self._snapshot_or_none(invocation_scope, targets, bundles)

        bundles: list[PatternEvidenceBundle] = []
        for target in targets:
            try:
                values = provider.collect(target)
                if not isinstance(values, tuple) or not values or not all(
                    isinstance(item, PatternEvidenceBundle) for item in values
                ):
                    raise TypeError("Provider returned a non-canonical bundle collection")
                if any(
                    _bundle_requested_symbol(item) != target.requested_symbol
                    for item in values
                ):
                    raise ValueError("Provider returned evidence for another target")
                bundles.extend(values)
            except (TimeoutError, ConnectionError) as exc:
                logger.warning(
                    "Pattern provider unavailable for %s: %s",
                    target.requested_symbol,
                    type(exc).__name__,
                )
                bundles.append(
                    _data_unavailable_bundle(target, "provider_unavailable")
                )
            except Exception as exc:  # noqa: BLE001 - per-target fail-open boundary
                logger.warning(
                    "Pattern collection failed for %s: %s",
                    target.requested_symbol,
                    type(exc).__name__,
                )
                bundles.append(_engine_error_bundle(target, "collection_error"))

        return self._snapshot_or_none(invocation_scope, targets, tuple(bundles))

    @staticmethod
    def _snapshot_or_none(
        invocation_scope: PatternInvocationScope,
        targets: tuple[PatternDecisionTarget, ...],
        bundles: tuple[PatternEvidenceBundle, ...],
    ) -> DecisionPatternEvidenceSnapshot | None:
        requested_symbols = tuple(target.requested_symbol for target in targets)
        try:
            return DecisionPatternEvidenceSnapshot.from_bundles(
                invocation_scope,
                requested_symbols,
                bundles,
            )
        except Exception as exc:  # noqa: BLE001 - Decision must continue
            logger.warning(
                "Pattern snapshot construction failed: %s", type(exc).__name__
            )
            return None


def resolve_pattern_invocation_scope(
    *,
    route: str,
    user_input: str,
    targets: tuple[PatternDecisionTarget, ...],
    trade_intent: object | None,
    aborted: bool = False,
    requested_symbol_count: int | None = None,
) -> PatternInvocationScope:
    """Return a typed, conservative scope without scanning or truncation."""

    if trade_intent is not None or aborted:
        return PatternInvocationScope.NONE
    if _SWITCH_OR_MULTI_LEG_PATTERN.search(user_input):
        return PatternInvocationScope.NONE
    unique_targets = {target.requested_symbol for target in targets}
    if len(unique_targets) != len(targets):
        return PatternInvocationScope.NONE

    if route == "position_single" and len(targets) == 1:
        return PatternInvocationScope.SINGLE

    if route != "position_multi" or not _COMPARE_PATTERN.search(user_input):
        return PatternInvocationScope.NONE
    if requested_symbol_count is not None and requested_symbol_count != len(targets):
        return PatternInvocationScope.NONE
    if 2 <= len(targets) <= 3:
        return PatternInvocationScope.COMPARE
    return PatternInvocationScope.NONE


def target_from_execution_output(exec_out: object) -> PatternDecisionTarget | None:
    """Use only identity already resolved by the existing Decision execution."""

    loaded_data = getattr(exec_out, "loaded_data", None)
    position = getattr(loaded_data, "target_position", None)
    if position is None:
        return None

    market_data = getattr(exec_out, "market_data", None)
    quote = getattr(market_data, "quote", None)
    candidates = (
        getattr(position, "symbol", ""),
        getattr(market_data, "symbol", ""),
        getattr(quote, "symbol", ""),
    )
    ticker = ""
    market = ""
    for value in candidates:
        if not value:
            continue
        try:
            ticker, market = parse_symbol(str(value))
            break
        except ValueError:
            continue
    if not ticker or not market:
        return None

    currency = str(getattr(quote, "currency", "") or "").upper()
    if not currency:
        currency = _MARKET_CURRENCY.get(market, "UNKNOWN")
    return PatternDecisionTarget(
        requested_symbol=f"{ticker}:{market}",
        symbol=ticker,
        market=market,
        currency=currency,
        economic_asset_class=economic_asset_class_value(position),
    )


def serialize_pattern_evidence_snapshot(
    snapshot: DecisionPatternEvidenceSnapshot | None,
) -> dict[str, Any] | None:
    """Pre-validate canonical JSON; omit only Pattern metadata on failure."""

    if snapshot is None:
        return None
    try:
        value = snapshot.as_dict()
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        restored = json.loads(encoded)
        if tuple(restored["bundle_hashes"]) != snapshot.bundle_hashes:
            raise ValueError("Pattern bundle hashes changed during serialization")
        for bundle_value, expected_hash in zip(
            restored["bundles"], snapshot.bundle_hashes
        ):
            if stable_hash(bundle_value) != expected_hash:
                raise ValueError("Pattern bundle content changed during serialization")
        return restored
    except Exception as exc:  # noqa: BLE001 - Pattern metadata is optional
        logger.warning(
            "Pattern metadata serialization failed: %s", type(exc).__name__
        )
        return None


__all__ = [
    "DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION",
    "DecisionPatternEvidenceCollector",
    "DecisionPatternEvidenceProvider",
    "DecisionPatternEvidenceSnapshot",
    "PatternDecisionTarget",
    "PatternInvocationScope",
    "UnavailableDecisionPatternEvidenceProvider",
    "build_runtime_pattern_evidence_provider",
    "resolve_pattern_invocation_scope",
    "serialize_pattern_evidence_snapshot",
    "target_from_execution_output",
]
