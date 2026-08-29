"""Pure, deterministic Consumption Eligibility and Classification design contract.

This is a Golden-Case reference only. It intentionally has no ORM, database,
network, LLM, analytics, or UI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from backend.services.consumption.contracts import canonical_json
from backend.services.consumption.economic_events import EventType


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ClassificationStatus(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PrimaryCategory(StrEnum):
    DAILY = "DAILY"
    TRAVEL = "TRAVEL"
    HOUSING = "HOUSING"


class ClassificationSource(StrEnum):
    USER_RULE = "USER_RULE"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    MERCHANT_RULE = "MERCHANT_RULE"
    ACCOUNT_PURPOSE_PRIOR = "ACCOUNT_PURPOSE_PRIOR"
    TRAVEL_CONTEXT = "TRAVEL_CONTEXT"
    BANK_EXPLICIT = "BANK_EXPLICIT"
    SYSTEM_RULE = "SYSTEM_RULE"
    UNKNOWN = "UNKNOWN"


DAILY_SECONDARY = frozenset({
    "FOOD_DINING", "TRANSPORT_AUTO", "SHOPPING", "HOME_LIVING",
    "DIGITAL_COMMUNICATION", "HEALTH_INSURANCE", "SPORTS_HOBBY", "PET", "OTHER",
})
TRAVEL_SECONDARY = frozenset({
    "LONG_DISTANCE_TRANSPORT", "ACCOMMODATION", "LOCAL_TRANSPORT", "FOOD_DINING",
    "ACTIVITIES_EXPERIENCES", "TRAVEL_SHOPPING", "OTHER",
})
HOUSING_SECONDARY = frozenset({"RENT", "PROPERTY_FEE", "OTHER"})

HARD_INELIGIBLE = frozenset({
    EventType.CREDIT_CARD_REPAYMENT, EventType.INTERNAL_TRANSFER,
    EventType.INVESTMENT_TRANSFER, EventType.LIQUIDITY_SWEEP, EventType.INCOME,
    EventType.LOAN_DISBURSEMENT, EventType.DEBT_REPAYMENT, EventType.FEE_INTEREST,
    EventType.REBATE,
})


@dataclass(frozen=True)
class EventInput:
    event_id: str
    event_type: EventType
    event_date: date | None
    account_id: str
    descriptor: str
    amount: Decimal = Decimal("0")
    original_event_id: str | None = None
    resolution_status: str = "RESOLVED"


@dataclass(frozen=True)
class AccountPurposePrior:
    account_id: str
    preferred_primary: PrimaryCategory
    effective_from: date
    effective_to: date | None = None

    def applies(self, event: EventInput) -> bool:
        return (
            self.account_id == event.account_id and event.event_date is not None
            and self.effective_from <= event.event_date
            and (self.effective_to is None or event.event_date <= self.effective_to)
        )


@dataclass(frozen=True)
class TravelContext:
    destination: str
    start_date: date
    end_date: date

    def applies(self, event: EventInput) -> bool:
        return event.event_date is not None and self.start_date <= event.event_date <= self.end_date


@dataclass(frozen=True)
class UserRule:
    rule_id: str
    match_text: str
    eligibility_status: EligibilityStatus
    primary_category: PrimaryCategory | None = None
    secondary_category: str | None = None
    account_id: str | None = None
    amount: Decimal | None = None
    amount_tolerance: Decimal = Decimal("0")
    effective_from: date | None = None
    effective_to: date | None = None

    def applies(self, event: EventInput) -> bool:
        text = "".join(event.descriptor.casefold().split())
        target = "".join(self.match_text.casefold().split())
        return (
            bool(target) and target in text
            and (self.account_id is None or self.account_id == event.account_id)
            and (self.amount is None or abs(event.amount - self.amount) <= self.amount_tolerance)
            and (self.effective_from is None or (event.event_date is not None and event.event_date >= self.effective_from))
            and (self.effective_to is None or (event.event_date is not None and event.event_date <= self.effective_to))
        )


@dataclass(frozen=True)
class UserConfirmation:
    event_id: str
    eligibility_status: EligibilityStatus
    primary_category: PrimaryCategory | None = None
    secondary_category: str | None = None
    reason: str = "USER_CONFIRMED"


@dataclass(frozen=True)
class ClassificationContext:
    account_purpose_priors: tuple[AccountPurposePrior, ...] = ()
    travel_contexts: tuple[TravelContext, ...] = ()
    user_rules: tuple[UserRule, ...] = ()
    user_confirmations: tuple[UserConfirmation, ...] = ()


@dataclass(frozen=True)
class ConsumptionInterpretation:
    event_id: str
    eligibility_status: EligibilityStatus
    eligibility_source: ClassificationSource
    eligibility_reason: str
    classification_status: ClassificationStatus
    primary_category: PrimaryCategory | None
    secondary_category: str | None
    classification_source: ClassificationSource
    classification_reason: str
    applied_rule_id: str | None = None
    inherited_from_event_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "event_id": self.event_id,
            "eligibility_status": self.eligibility_status.value,
            "eligibility_source": self.eligibility_source.value,
            "eligibility_reason": self.eligibility_reason,
            "classification_status": self.classification_status.value,
            "primary_category": self.primary_category.value if self.primary_category else None,
            "secondary_category": self.secondary_category,
            "classification_source": self.classification_source.value,
            "classification_reason": self.classification_reason,
            "applied_rule_id": self.applied_rule_id,
            "inherited_from_event_id": self.inherited_from_event_id,
        }


def canonical_classification_json(values: Iterable[ConsumptionInterpretation]) -> str:
    return canonical_json([value.to_dict() for value in values])


def _classified(event: EventInput, *, eligibility_source: ClassificationSource, eligibility_reason: str,
                primary: PrimaryCategory, secondary: str, classification_source: ClassificationSource,
                classification_reason: str, rule_id: str | None = None) -> ConsumptionInterpretation:
    return ConsumptionInterpretation(
        event_id=event.event_id, eligibility_status=EligibilityStatus.ELIGIBLE,
        eligibility_source=eligibility_source, eligibility_reason=eligibility_reason,
        classification_status=ClassificationStatus.CLASSIFIED, primary_category=primary,
        secondary_category=secondary, classification_source=classification_source,
        classification_reason=classification_reason, applied_rule_id=rule_id,
    )


def _not_applicable(event: EventInput, reason: str, source: ClassificationSource = ClassificationSource.SYSTEM_RULE,
                    inherited_from: str | None = None) -> ConsumptionInterpretation:
    return ConsumptionInterpretation(
        event_id=event.event_id, eligibility_status=EligibilityStatus.INELIGIBLE,
        eligibility_source=source, eligibility_reason=reason,
        classification_status=ClassificationStatus.NOT_APPLICABLE, primary_category=None,
        secondary_category=None, classification_source=source, classification_reason=reason,
        inherited_from_event_id=inherited_from,
    )


def _review(event: EventInput, reason: str) -> ConsumptionInterpretation:
    return ConsumptionInterpretation(
        event_id=event.event_id, eligibility_status=EligibilityStatus.NEEDS_REVIEW,
        eligibility_source=ClassificationSource.UNKNOWN, eligibility_reason=reason,
        classification_status=ClassificationStatus.NOT_APPLICABLE, primary_category=None,
        secondary_category=None, classification_source=ClassificationSource.UNKNOWN,
        classification_reason=reason,
    )


def _eligible_unknown(event: EventInput, source: ClassificationSource, reason: str) -> ConsumptionInterpretation:
    return ConsumptionInterpretation(
        event_id=event.event_id, eligibility_status=EligibilityStatus.ELIGIBLE,
        eligibility_source=source, eligibility_reason=reason,
        classification_status=ClassificationStatus.NEEDS_REVIEW, primary_category=None,
        secondary_category=None, classification_source=source, classification_reason=reason,
    )


def _semantic_category(event: EventInput) -> tuple[PrimaryCategory, str] | None:
    text = "".join(event.descriptor.casefold().split())
    if any(word in text for word in ("物业", "物业费")):
        return PrimaryCategory.HOUSING, "PROPERTY_FEE"
    if any(word in text for word in ("机票", "航空", "航班", "去哪儿网")):
        return PrimaryCategory.TRAVEL, "LONG_DISTANCE_TRANSPORT"
    if any(word in text for word in ("酒店", "hotel", "宾馆")):
        return PrimaryCategory.TRAVEL, "ACCOMMODATION"
    if any(word in text for word in ("vercel", "cursor", "cloudflare", "订阅")):
        return PrimaryCategory.DAILY, "DIGITAL_COMMUNICATION"
    if any(word in text for word in ("餐厅", "餐饮", "美团", "coffee")):
        return PrimaryCategory.DAILY, "FOOD_DINING"
    if any(word in text for word in ("滴滴", "出租车", "打车", "停车")):
        return PrimaryCategory.DAILY, "TRANSPORT_AUTO"
    if any(word in text for word in ("健身", "运动")):
        return PrimaryCategory.DAILY, "SPORTS_HOBBY"
    if "购物" in text or "merchantx" in text:
        return PrimaryCategory.DAILY, "SHOPPING"
    return None


def _travel_override(event: EventInput, contexts: tuple[TravelContext, ...]) -> tuple[PrimaryCategory, str] | None:
    if not any(context.applies(event) for context in contexts):
        return None
    text = "".join(event.descriptor.casefold().split())
    if any(word in text for word in ("餐厅", "餐饮", "美团", "coffee")):
        return PrimaryCategory.TRAVEL, "FOOD_DINING"
    if any(word in text for word in ("滴滴", "出租车", "打车")):
        return PrimaryCategory.TRAVEL, "LOCAL_TRANSPORT"
    return None


def classify(events: Iterable[EventInput], context: ClassificationContext) -> tuple[ConsumptionInterpretation, ...]:
    """Apply frozen, explainable eligibility before category determination."""
    event_list = tuple(sorted(events, key=lambda item: item.event_id))
    by_id = {event.event_id: event for event in event_list}
    results: dict[str, ConsumptionInterpretation] = {}
    confirmations = {item.event_id: item for item in context.user_confirmations}

    for event in event_list:
        if event.event_type in HARD_INELIGIBLE:
            results[event.event_id] = _not_applicable(event, "HARD_NON_CONSUMPTION_EXCLUSION")
            continue
        if event.event_type == EventType.REFUND:
            original = by_id.get(event.original_event_id or "")
            if original and original.event_type == EventType.CONSUMPTION:
                results[event.event_id] = _not_applicable(event, "REFUND_INHERITS_ORIGINAL_CLASSIFICATION", inherited_from=original.event_id)
            else:
                results[event.event_id] = _not_applicable(event, "UNMATCHED_REFUND")
            continue

        confirmation = confirmations.get(event.event_id)
        if event.event_type == EventType.OTHER:
            if confirmation is None:
                applicable_rules = [rule for rule in context.user_rules if rule.applies(event)]
                if not applicable_rules:
                    results[event.event_id] = _review(event, "OTHER_CONSUMPTION_SEMANTICS_UNCONFIRMED")
                    continue
                rule = applicable_rules[0]
                if rule.eligibility_status == EligibilityStatus.INELIGIBLE:
                    results[event.event_id] = _not_applicable(event, "USER_RULE_NON_CONSUMPTION", ClassificationSource.USER_RULE)
                    continue
                if rule.primary_category and rule.secondary_category:
                    results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.USER_RULE,
                        eligibility_reason="OTHER_PROMOTED_BY_USER_RULE", primary=rule.primary_category,
                        secondary=rule.secondary_category, classification_source=ClassificationSource.USER_RULE,
                        classification_reason="USER_RULE_SCOPE_MATCH", rule_id=rule.rule_id)
                    continue
                results[event.event_id] = _eligible_unknown(event, ClassificationSource.USER_RULE, "OTHER_PROMOTED_BY_USER_RULE")
                continue
            if confirmation.eligibility_status == EligibilityStatus.INELIGIBLE:
                results[event.event_id] = _not_applicable(event, confirmation.reason, ClassificationSource.USER_CONFIRMATION)
                continue
            if confirmation.primary_category and confirmation.secondary_category:
                results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.USER_CONFIRMATION,
                    eligibility_reason=confirmation.reason, primary=confirmation.primary_category,
                    secondary=confirmation.secondary_category, classification_source=ClassificationSource.USER_CONFIRMATION,
                    classification_reason=confirmation.reason)
                continue
            results[event.event_id] = _eligible_unknown(event, ClassificationSource.USER_CONFIRMATION, confirmation.reason)
            continue

        # CONSUMPTION is eligible. A user correction is stronger than every automatic rule.
        if confirmation and confirmation.primary_category and confirmation.secondary_category:
            results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.USER_CONFIRMATION,
                eligibility_reason=confirmation.reason, primary=confirmation.primary_category,
                secondary=confirmation.secondary_category, classification_source=ClassificationSource.USER_CONFIRMATION,
                classification_reason=confirmation.reason)
            continue
        applicable_rules = [rule for rule in context.user_rules if rule.applies(event)]
        if applicable_rules:
            rule = applicable_rules[0]
            if rule.primary_category and rule.secondary_category:
                results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.SYSTEM_RULE,
                    eligibility_reason="CONSUMPTION_EVENT", primary=rule.primary_category,
                    secondary=rule.secondary_category, classification_source=ClassificationSource.USER_RULE,
                    classification_reason="USER_RULE_SCOPE_MATCH", rule_id=rule.rule_id)
                continue

        semantic = _semantic_category(event)
        travel = _travel_override(event, context.travel_contexts)
        # Destination context overrides only generic food/local-transport semantics,
        # never a specific housing, flight, hotel or digital-service fact.
        if travel and semantic and semantic[0] == PrimaryCategory.DAILY and semantic[1] in {"FOOD_DINING", "TRANSPORT_AUTO"}:
            results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.SYSTEM_RULE,
                eligibility_reason="CONSUMPTION_EVENT", primary=travel[0], secondary=travel[1],
                classification_source=ClassificationSource.TRAVEL_CONTEXT, classification_reason="TRAVEL_DATE_CONTEXT")
            continue
        if semantic:
            results[event.event_id] = _classified(event, eligibility_source=ClassificationSource.SYSTEM_RULE,
                eligibility_reason="CONSUMPTION_EVENT", primary=semantic[0], secondary=semantic[1],
                classification_source=ClassificationSource.MERCHANT_RULE, classification_reason="HIGH_CONFIDENCE_SEMANTIC")
            continue
        if any(prior.applies(event) for prior in context.account_purpose_priors):
            results[event.event_id] = _eligible_unknown(event, ClassificationSource.ACCOUNT_PURPOSE_PRIOR, "WEAK_PRIOR_CANNOT_CLASSIFY_ALONE")
        else:
            results[event.event_id] = _eligible_unknown(event, ClassificationSource.UNKNOWN, "CONSUMPTION_CATEGORY_UNKNOWN")

    return tuple(results[event.event_id] for event in event_list)
