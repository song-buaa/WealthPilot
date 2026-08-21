"""Pattern technical lifecycle without publishing or teacher-copy semantics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from .identity import stable_hash


LIFECYCLE_VERSION = "wp-pattern-technical-lifecycle-v1"


class LifecycleState(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass(frozen=True)
class LifecycleTransition:
    from_state: LifecycleState
    to_state: LifecycleState
    session_date: date
    session_ordinal: int
    reason: str


@dataclass(frozen=True)
class LifecycleSnapshot:
    pattern_id: str
    state: LifecycleState
    formed_on: date
    formed_session_ordinal: int
    evaluation_session: date
    evaluation_session_ordinal: int
    confirmed_on: date | None = None
    confirmed_session_ordinal: int | None = None
    invalidated_on: date | None = None
    invalidated_session_ordinal: int | None = None
    expired_on: date | None = None
    expired_session_ordinal: int | None = None
    transitions: tuple[LifecycleTransition, ...] = ()
    lifecycle_version: str = LIFECYCLE_VERSION

    @property
    def result_hash(self) -> str:
        return stable_hash(self)


class LifecycleCore:
    @staticmethod
    def candidate(pattern_id: str, *, formed_on: date, formed_session_ordinal: int) -> LifecycleSnapshot:
        if not pattern_id or formed_session_ordinal < 0:
            raise ValueError("candidate lifecycle requires stable identity and session")
        return LifecycleSnapshot(
            pattern_id=pattern_id,
            state=LifecycleState.CANDIDATE,
            formed_on=formed_on,
            formed_session_ordinal=formed_session_ordinal,
            evaluation_session=formed_on,
            evaluation_session_ordinal=formed_session_ordinal,
        )

    @staticmethod
    def evaluate(
        snapshot: LifecycleSnapshot,
        *,
        session_date: date,
        session_ordinal: int,
        confirmation: bool = False,
        invalidation_reason: str | None = None,
        expires_at_session_ordinal: int | None = None,
    ) -> LifecycleSnapshot:
        if session_ordinal < snapshot.evaluation_session_ordinal:
            raise ValueError("lifecycle replay cannot move backward")
        if snapshot.state in {LifecycleState.INVALIDATED, LifecycleState.EXPIRED}:
            return replace(snapshot, evaluation_session=session_date, evaluation_session_ordinal=session_ordinal)

        current = replace(snapshot, evaluation_session=session_date, evaluation_session_ordinal=session_ordinal)
        if invalidation_reason:
            return LifecycleCore._transition(current, LifecycleState.INVALIDATED, session_date, session_ordinal, invalidation_reason)
        if confirmation and current.state is LifecycleState.CANDIDATE:
            current = LifecycleCore._transition(current, LifecycleState.CONFIRMED, session_date, session_ordinal, "confirmation_fact_available")
        if expires_at_session_ordinal is not None and session_ordinal >= expires_at_session_ordinal:
            return LifecycleCore._transition(current, LifecycleState.EXPIRED, session_date, session_ordinal, "session_expiry_reached")
        return current

    @staticmethod
    def _transition(
        snapshot: LifecycleSnapshot,
        target: LifecycleState,
        session_date: date,
        session_ordinal: int,
        reason: str,
    ) -> LifecycleSnapshot:
        allowed = {
            LifecycleState.CANDIDATE: {LifecycleState.CONFIRMED, LifecycleState.INVALIDATED, LifecycleState.EXPIRED},
            LifecycleState.CONFIRMED: {LifecycleState.INVALIDATED, LifecycleState.EXPIRED},
        }
        if target not in allowed.get(snapshot.state, set()):
            raise ValueError(f"illegal lifecycle transition: {snapshot.state.value} -> {target.value}")
        transition = LifecycleTransition(snapshot.state, target, session_date, session_ordinal, reason)
        updates: dict[str, object] = {
            "state": target,
            "evaluation_session": session_date,
            "evaluation_session_ordinal": session_ordinal,
            "transitions": snapshot.transitions + (transition,),
        }
        if target is LifecycleState.CONFIRMED:
            updates.update(confirmed_on=session_date, confirmed_session_ordinal=session_ordinal)
        elif target is LifecycleState.INVALIDATED:
            updates.update(invalidated_on=session_date, invalidated_session_ordinal=session_ordinal)
        elif target is LifecycleState.EXPIRED:
            updates.update(expired_on=session_date, expired_session_ordinal=session_ordinal)
        return replace(snapshot, **updates)
