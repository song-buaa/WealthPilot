"""Deterministic, append-only resolver for consumption interpretations.

This module deliberately reads EconomicEvent and its linked Raw evidence but never
changes either fact layer.  It contains no aggregation, network, UI, or AI code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.services.consumption.classification_design import (
    ClassificationSource, ClassificationStatus, EligibilityStatus, PrimaryCategory,
    HARD_INELIGIBLE,
)
from backend.services.consumption.economic_events import EventType
from backend.services.consumption.models import (
    AccountPurposePreference, ConsumptionInterpretation, ConsumptionInterpretationAudit,
    EconomicEvent, EventRawLink, RawTransaction, TravelContext, UserClassificationRule,
)


RESOLVER_VERSION = "consumption-classification-v1"


@dataclass(frozen=True)
class Resolution:
    eligibility_status: EligibilityStatus
    eligibility_source: ClassificationSource
    eligibility_reason: str
    classification_status: ClassificationStatus
    primary_category: PrimaryCategory | None
    secondary_category: str | None
    classification_source: ClassificationSource
    classification_reason: str
    rule_id: str | None = None
    user_confirmed: bool = False
    inherited_from_event_id: str | None = None


def _compact(value: str | None) -> str:
    return "".join((value or "").casefold().split())


def _semantic(text: str) -> tuple[PrimaryCategory, str] | None:
    value = _compact(text)
    if any(word in value for word in ("物业", "物业费")):
        return PrimaryCategory.HOUSING, "PROPERTY_FEE"
    if any(word in value for word in ("机票", "航空", "航班", "去哪儿网")):
        return PrimaryCategory.TRAVEL, "LONG_DISTANCE_TRANSPORT"
    if any(word in value for word in ("酒店", "hotel", "宾馆")):
        return PrimaryCategory.TRAVEL, "ACCOMMODATION"
    if any(word in value for word in ("vercel", "cursor", "cloudflare", "googleone")):
        return PrimaryCategory.DAILY, "DIGITAL_COMMUNICATION"
    if any(word in value for word in ("餐厅", "餐饮", "美团", "coffee")):
        return PrimaryCategory.DAILY, "FOOD_DINING"
    if any(word in value for word in ("滴滴", "出租车", "打车", "停车")):
        return PrimaryCategory.DAILY, "TRANSPORT_AUTO"
    if any(word in value for word in ("健身", "运动")):
        return PrimaryCategory.DAILY, "SPORTS_HOBBY"
    if "购物" in value or "merchantx" in value:
        return PrimaryCategory.DAILY, "SHOPPING"
    return None


class ClassificationResolver:
    """Apply frozen priority and append only semantic changes."""

    def resolve_event(self, session: Session, event: EconomicEvent | str) -> ConsumptionInterpretation:
        target = session.get(EconomicEvent, event) if isinstance(event, str) else event
        if target is None:
            raise ValueError("EconomicEvent not found")
        current = self._current(session, target.id)
        # A local decision has precedence over every automatic replay.
        if current and current.user_confirmed:
            return current
        resolution = self._resolve(session, target)
        return self._append_if_changed(session, target.id, resolution, current=current)

    def replay(self, session: Session, event_ids: tuple[str, ...] | None = None) -> tuple[ConsumptionInterpretation, ...]:
        query = session.query(EconomicEvent).order_by(EconomicEvent.id)
        if event_ids is not None:
            query = query.filter(EconomicEvent.id.in_(event_ids))
        return tuple(self.resolve_event(session, event) for event in query.all())

    def confirm_event(self, session: Session, event_id: str, *, eligibility_status: EligibilityStatus,
                      primary_category: PrimaryCategory | None = None, secondary_category: str | None = None,
                      reason: str = "USER_CONFIRMED", actor_id: str | None = None) -> ConsumptionInterpretation:
        event = session.get(EconomicEvent, event_id)
        if event is None:
            raise ValueError("EconomicEvent not found")
        if EventType(event.event_type) in HARD_INELIGIBLE or EventType(event.event_type) == EventType.REFUND:
            # Record no invalid override: the frozen hard boundary remains authoritative.
            return self.resolve_event(session, event)
        if eligibility_status == EligibilityStatus.ELIGIBLE and (primary_category is None) != (secondary_category is None):
            raise ValueError("primary and secondary categories must be supplied together")
        if eligibility_status == EligibilityStatus.INELIGIBLE:
            status, primary_category, secondary_category = ClassificationStatus.NOT_APPLICABLE, None, None
        elif primary_category:
            status = ClassificationStatus.CLASSIFIED
        else:
            status = ClassificationStatus.NEEDS_REVIEW
        resolution = Resolution(
            eligibility_status, ClassificationSource.USER_CONFIRMATION, reason, status,
            primary_category, secondary_category, ClassificationSource.USER_CONFIRMATION, reason,
            user_confirmed=True,
        )
        old = self._current(session, event_id)
        created = self._append_if_changed(session, event_id, resolution, current=old, actor_id=actor_id)
        if created is not old:
            session.add(ConsumptionInterpretationAudit(
                event_id=event_id, old_interpretation_id=old.id if old else None,
                new_interpretation_id=created.id, actor_id=actor_id, reason=reason,
            ))
            session.flush()
        return created

    def get_effective_classification(self, session: Session, event: EconomicEvent | str) -> ConsumptionInterpretation | None:
        target = session.get(EconomicEvent, event) if isinstance(event, str) else event
        if target is None:
            return None
        if EventType(target.event_type) == EventType.REFUND:
            return self._current(session, target.original_event_id) if target.original_event_id else None
        return self._current(session, target.id) or self.resolve_event(session, target)

    def _resolve(self, session: Session, event: EconomicEvent) -> Resolution:
        event_type = EventType(event.event_type)
        if event_type in HARD_INELIGIBLE:
            return self._not_applicable("HARD_NON_CONSUMPTION_EXCLUSION")
        if event_type == EventType.REFUND:
            return self._not_applicable("REFUND_INHERITS_ORIGINAL_CLASSIFICATION" if event.original_event_id else "UNMATCHED_REFUND", event.original_event_id)
        raw, account_id, descriptor = self._evidence(session, event)
        if event_type == EventType.OTHER:
            rule = self._matching_rule(session, account_id, descriptor, Decimal(event.amount), event.event_date)
            if rule is None:
                return Resolution(EligibilityStatus.NEEDS_REVIEW, ClassificationSource.UNKNOWN,
                    "OTHER_CONSUMPTION_SEMANTICS_UNCONFIRMED", ClassificationStatus.NOT_APPLICABLE,
                    None, None, ClassificationSource.UNKNOWN, "OTHER_CONSUMPTION_SEMANTICS_UNCONFIRMED")
            return self._from_rule(rule, other=True)
        # Remaining event type is CONSUMPTION. Rules only influence category, never hard eligibility.
        rule = self._matching_rule(session, account_id, descriptor, Decimal(event.amount), event.event_date)
        if rule and rule.primary_category and rule.secondary_category:
            return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.SYSTEM_RULE, "CONSUMPTION_EVENT",
                ClassificationStatus.CLASSIFIED, PrimaryCategory(rule.primary_category), rule.secondary_category,
                ClassificationSource.USER_RULE, "USER_RULE_SCOPE_MATCH", rule.id)
        semantic = _semantic(descriptor)
        travel = self._travel_applies(session, event.event_date)
        if semantic and travel and semantic in {(PrimaryCategory.DAILY, "FOOD_DINING"), (PrimaryCategory.DAILY, "TRANSPORT_AUTO")}:
            return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.SYSTEM_RULE, "CONSUMPTION_EVENT",
                ClassificationStatus.CLASSIFIED, PrimaryCategory.TRAVEL,
                "FOOD_DINING" if semantic[1] == "FOOD_DINING" else "LOCAL_TRANSPORT",
                ClassificationSource.TRAVEL_CONTEXT, "TRAVEL_DATE_CONTEXT")
        if semantic:
            return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.SYSTEM_RULE, "CONSUMPTION_EVENT",
                ClassificationStatus.CLASSIFIED, semantic[0], semantic[1],
                ClassificationSource.MERCHANT_RULE, "HIGH_CONFIDENCE_SEMANTIC")
        source = ClassificationSource.ACCOUNT_PURPOSE_PRIOR if self._prior_applies(session, account_id, event.event_date) else ClassificationSource.UNKNOWN
        reason = "WEAK_PRIOR_CANNOT_CLASSIFY_ALONE" if source == ClassificationSource.ACCOUNT_PURPOSE_PRIOR else "CONSUMPTION_CATEGORY_UNKNOWN"
        return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.SYSTEM_RULE, "CONSUMPTION_EVENT",
            ClassificationStatus.NEEDS_REVIEW, None, None, source, reason)

    @staticmethod
    def _not_applicable(reason: str, inherited_from_event_id: str | None = None) -> Resolution:
        return Resolution(EligibilityStatus.INELIGIBLE, ClassificationSource.SYSTEM_RULE, reason,
            ClassificationStatus.NOT_APPLICABLE, None, None, ClassificationSource.SYSTEM_RULE, reason,
            inherited_from_event_id=inherited_from_event_id)

    @staticmethod
    def _evidence(session: Session, event: EconomicEvent) -> tuple[RawTransaction | None, str, str]:
        link = session.query(EventRawLink).filter_by(event_id=event.id, is_active=True).order_by(EventRawLink.id).first()
        if link is None:
            return None, "", ""
        raw = session.get(RawTransaction, link.raw_transaction_id)
        return raw, raw.account_id, " ".join(item for item in (raw.raw_description, raw.raw_counterparty) if item)

    @staticmethod
    def _current(session: Session, event_id: str | None) -> ConsumptionInterpretation | None:
        return session.query(ConsumptionInterpretation).filter_by(event_id=event_id, is_active=True).one_or_none() if event_id else None

    @staticmethod
    def _matching_rule(session: Session, account_id: str, text: str, amount: Decimal, when: date | None) -> UserClassificationRule | None:
        for rule in session.query(UserClassificationRule).filter_by(status="ACTIVE").order_by(UserClassificationRule.created_at, UserClassificationRule.id):
            if rule.account_id and rule.account_id != account_id:
                continue
            target = _compact(rule.match_text)
            if target and target not in _compact(text):
                continue
            if not target and not rule.account_id and rule.amount is None:
                continue  # reject unbounded rules even if an old DB row exists
            if rule.amount is not None and abs(amount - Decimal(rule.amount)) > Decimal(rule.amount_tolerance or 0):
                continue
            if rule.effective_from and (when is None or when < rule.effective_from):
                continue
            if rule.effective_to and (when is None or when > rule.effective_to):
                continue
            return rule
        return None

    @staticmethod
    def _from_rule(rule: UserClassificationRule, *, other: bool) -> Resolution:
        eligibility = EligibilityStatus(rule.eligibility_action)
        if eligibility == EligibilityStatus.INELIGIBLE:
            return Resolution(eligibility, ClassificationSource.USER_RULE, "USER_RULE_NON_CONSUMPTION",
                ClassificationStatus.NOT_APPLICABLE, None, None, ClassificationSource.USER_RULE, "USER_RULE_NON_CONSUMPTION", rule.id)
        if rule.primary_category and rule.secondary_category:
            return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.USER_RULE,
                "OTHER_PROMOTED_BY_USER_RULE" if other else "CONSUMPTION_EVENT", ClassificationStatus.CLASSIFIED,
                PrimaryCategory(rule.primary_category), rule.secondary_category, ClassificationSource.USER_RULE,
                "USER_RULE_SCOPE_MATCH", rule.id)
        return Resolution(EligibilityStatus.ELIGIBLE, ClassificationSource.USER_RULE, "OTHER_PROMOTED_BY_USER_RULE",
            ClassificationStatus.NEEDS_REVIEW, None, None, ClassificationSource.USER_RULE, "USER_RULE_SCOPE_MATCH", rule.id)

    @staticmethod
    def _travel_applies(session: Session, when: date | None) -> bool:
        return bool(when and session.query(TravelContext).filter(
            TravelContext.status == "ACTIVE", TravelContext.start_date <= when, TravelContext.end_date >= when
        ).first())

    @staticmethod
    def _prior_applies(session: Session, account_id: str, when: date | None) -> bool:
        return bool(when and session.query(AccountPurposePreference).filter(
            AccountPurposePreference.account_id == account_id, AccountPurposePreference.status == "ACTIVE",
            AccountPurposePreference.effective_from <= when,
            (AccountPurposePreference.effective_to.is_(None)) | (AccountPurposePreference.effective_to >= when),
        ).first())

    @staticmethod
    def _same(current: ConsumptionInterpretation, result: Resolution) -> bool:
        return all((
            current.eligibility_status == result.eligibility_status.value,
            current.eligibility_source == result.eligibility_source.value,
            current.eligibility_reason == result.eligibility_reason,
            current.classification_status == result.classification_status.value,
            current.primary_category == (result.primary_category.value if result.primary_category else None),
            current.secondary_category == result.secondary_category,
            current.classification_source == result.classification_source.value,
            current.classification_reason == result.classification_reason,
            current.rule_id == result.rule_id,
            current.user_confirmed == result.user_confirmed,
            current.inherited_from_event_id == result.inherited_from_event_id,
            current.resolver_version == RESOLVER_VERSION,
        ))

    def _append_if_changed(self, session: Session, event_id: str, result: Resolution, *,
                           current: ConsumptionInterpretation | None, actor_id: str | None = None) -> ConsumptionInterpretation:
        if current and self._same(current, result):
            return current
        if current:
            current.is_active = False
        created = ConsumptionInterpretation(
            event_id=event_id, eligibility_status=result.eligibility_status.value,
            eligibility_source=result.eligibility_source.value, eligibility_reason=result.eligibility_reason,
            classification_status=result.classification_status.value,
            primary_category=result.primary_category.value if result.primary_category else None,
            secondary_category=result.secondary_category,
            classification_source=result.classification_source.value, classification_reason=result.classification_reason,
            rule_id=result.rule_id, user_confirmed=result.user_confirmed,
            inherited_from_event_id=result.inherited_from_event_id,
            revision_number=(current.revision_number + 1 if current else 1),
            supersedes_revision_id=current.id if current else None, resolver_version=RESOLVER_VERSION,
            actor_type="LOCAL_USER" if result.user_confirmed else None, actor_id=actor_id,
        )
        session.add(created)
        session.flush()
        return created
