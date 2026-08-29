"""Pure reference contract for Consumption Analytics Design v1.

The evaluator deliberately consumes only already-active Event projections and
already-active interpretations supplied by a caller. It has no ORM, API, raw
statement, UI, network, or LLM dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from backend.services.consumption.classification_design import (
    ClassificationStatus, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.economic_events import EventType


class DataCoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    SOURCE_LIMITED = "SOURCE_LIMITED"
    UNKNOWN = "UNKNOWN"


class SourceCoverageStatus(StrEnum):
    EXPLICIT = "EXPLICIT"
    OBSERVED_ONLY = "OBSERVED_ONLY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ActiveEventProjection:
    """Read model of a current EconomicEvent projection, not an ORM entity."""

    event_id: str
    event_type: EventType
    analytics_effective_date: date | None
    account_id: str
    original_amount: Decimal
    base_net_amount: Decimal | None
    fx_source: str
    currency: str = "CNY"


@dataclass(frozen=True)
class ActiveInterpretation:
    """Read model of the one active ConsumptionInterpretation revision."""

    event_id: str
    eligibility_status: EligibilityStatus
    classification_status: ClassificationStatus
    primary_category: PrimaryCategory | None = None
    secondary_category: str | None = None


@dataclass(frozen=True)
class SourceCoverageInput:
    account_id: str
    month: date
    status: SourceCoverageStatus
    observed_through: date | None = None
    connected: bool = True


@dataclass(frozen=True)
class SecondaryBreakdown:
    primary_category: PrimaryCategory
    secondary_category: str
    amount_cny: Decimal
    event_count: int
    share_of_total: Decimal | None
    share_within_primary: Decimal | None


@dataclass(frozen=True)
class AverageMetric:
    """Average over complete, amount-resolved calendar months only."""

    amount_cny: Decimal | None
    months_used: int


@dataclass(frozen=True)
class UnresolvedAmount:
    """Known source-currency amount that cannot yet be included in CNY totals."""

    currency: str
    amount: Decimal
    event_count: int


@dataclass(frozen=True)
class MonthlySpendingPoint:
    month: date
    total_spending_cny: Decimal
    daily_cny: Decimal
    travel_cny: Decimal
    housing_cny: Decimal
    unclassified_eligible_cny: Decimal
    classified_eligible_cny: Decimal
    classification_coverage_rate: Decimal | None
    eligible_event_count: int
    eligibility_review_count: int
    classification_review_count: int
    amount_unresolved_count: int
    amount_unresolved_original_amount: Decimal
    amount_complete: bool
    data_coverage_status: DataCoverageStatus
    is_partial_month: bool
    as_of_date: date | None
    comparison_available: bool
    comparison_reason: str | None
    amount_unresolved_by_currency: tuple[UnresolvedAmount, ...] = ()


@dataclass(frozen=True)
class SpendingSummary:
    months: tuple[MonthlySpendingPoint, ...]
    secondary_breakdowns: tuple[SecondaryBreakdown, ...]
    complete_month_average_cny: Decimal | None
    complete_month_count: int
    three_month_average: AverageMetric
    twelve_month_average: AverageMetric


def month_start(value: date) -> date:
    return value.replace(day=1)


def _month_end(value: date) -> date:
    next_month = date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)
    return next_month - timedelta(days=1)


def _months_ending(as_of_date: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    cursor = month_start(as_of_date)
    for _ in range(count):
        values.append(cursor)
        cursor = date(cursor.year - (cursor.month == 1), 12 if cursor.month == 1 else cursor.month - 1, 1)
    return tuple(reversed(values))


def _coverage_status(month: date, as_of_date: date, inputs: Iterable[SourceCoverageInput], expected_accounts: frozenset[str]) -> DataCoverageStatus:
    rows = [item for item in inputs if item.month == month and item.connected]
    present = {item.account_id for item in rows}
    if expected_accounts and not expected_accounts.issubset(present):
        return DataCoverageStatus.UNKNOWN
    if any(item.status == SourceCoverageStatus.UNKNOWN for item in rows):
        return DataCoverageStatus.UNKNOWN
    if any(item.status == SourceCoverageStatus.OBSERVED_ONLY for item in rows):
        return DataCoverageStatus.SOURCE_LIMITED
    if month == month_start(as_of_date) and as_of_date < _month_end(month):
        return DataCoverageStatus.PARTIAL
    if any(item.observed_through and item.observed_through < _month_end(month) for item in rows):
        return DataCoverageStatus.PARTIAL
    return DataCoverageStatus.COMPLETE if rows or not expected_accounts else DataCoverageStatus.UNKNOWN


def evaluate_spending(
    events: Iterable[ActiveEventProjection],
    interpretations: Iterable[ActiveInterpretation],
    coverage: Iterable[SourceCoverageInput],
    *,
    as_of_date: date,
    expected_account_ids: Iterable[str] = (),
    month_count: int = 12,
) -> SpendingSummary:
    """Evaluate deterministic monthly spending from active upstream read models.

    ``total_spending_cny`` is a known partial total when ``amount_complete`` is
    false; unresolved eligible foreign amounts are never silently represented as
    zero. Eligibility-review events are governance counts, not spending amounts.
    """
    if month_count < 1:
        raise ValueError("month_count must be positive")
    by_interpretation = {item.event_id: item for item in interpretations}
    windows = _months_ending(as_of_date, month_count)
    month_events: dict[date, list[tuple[ActiveEventProjection, ActiveInterpretation]]] = {item: [] for item in windows}
    for event in events:
        interpretation = by_interpretation.get(event.event_id)
        if interpretation is None or event.analytics_effective_date is None:
            continue
        bucket = month_start(event.analytics_effective_date)
        if bucket in month_events:
            month_events[bucket].append((event, interpretation))
    coverage_values = tuple(coverage)
    expected = frozenset(expected_account_ids)
    points: list[MonthlySpendingPoint] = []
    secondary: dict[tuple[PrimaryCategory, str], tuple[Decimal, int]] = {}
    for month in windows:
        daily = travel = housing = unclassified = known_total = Decimal("0")
        unresolved_original = Decimal("0")
        unresolved_by_currency: dict[str, tuple[Decimal, int]] = {}
        eligible_count = eligibility_review = classification_review = unresolved_count = 0
        for event, interpretation in month_events[month]:
            if interpretation.eligibility_status == EligibilityStatus.NEEDS_REVIEW:
                eligibility_review += 1
                continue
            if interpretation.eligibility_status != EligibilityStatus.ELIGIBLE:
                continue
            # A stale interpretation can never turn a hard Event type into spend.
            if event.event_type != EventType.CONSUMPTION and event.event_type != EventType.OTHER:
                continue
            eligible_count += 1
            if event.base_net_amount is None:
                unresolved_count += 1
                unresolved_original += abs(event.original_amount)
                current_amount, current_count = unresolved_by_currency.get(
                    event.currency, (Decimal("0"), 0)
                )
                unresolved_by_currency[event.currency] = (
                    current_amount + abs(event.original_amount), current_count + 1
                )
                continue
            amount = Decimal(event.base_net_amount)
            known_total += amount
            if interpretation.classification_status == ClassificationStatus.NEEDS_REVIEW:
                unclassified += amount
                classification_review += 1
                continue
            if interpretation.classification_status != ClassificationStatus.CLASSIFIED or interpretation.primary_category is None:
                continue
            if interpretation.primary_category == PrimaryCategory.DAILY:
                daily += amount
            elif interpretation.primary_category == PrimaryCategory.TRAVEL:
                travel += amount
            elif interpretation.primary_category == PrimaryCategory.HOUSING:
                housing += amount
            if interpretation.secondary_category:
                key = (interpretation.primary_category, interpretation.secondary_category)
                previous_amount, previous_count = secondary.get(key, (Decimal("0"), 0))
                secondary[key] = (previous_amount + amount, previous_count + 1)
        classified = daily + travel + housing
        coverage_rate = (classified / known_total) if known_total else None
        status = _coverage_status(month, as_of_date, coverage_values, expected)
        partial = status != DataCoverageStatus.COMPLETE
        previous = points[-1] if points else None
        comparison_available = bool(previous and not partial and previous.data_coverage_status == DataCoverageStatus.COMPLETE)
        reason = None if comparison_available else ("NO_PREVIOUS_COMPLETE_MONTH" if previous is None else "MONTH_NOT_COMPARABLE")
        points.append(MonthlySpendingPoint(
            month, known_total, daily, travel, housing, unclassified, classified, coverage_rate,
            eligible_count, eligibility_review, classification_review, unresolved_count,
            unresolved_original, unresolved_count == 0, status, partial,
            as_of_date if month == month_start(as_of_date) else None, comparison_available, reason,
            tuple(
                UnresolvedAmount(currency, amount, count)
                for currency, (amount, count) in sorted(unresolved_by_currency.items())
            ),
        ))
    window_total = sum((point.total_spending_cny for point in points), Decimal("0"))
    primary_totals = {
        PrimaryCategory.DAILY: sum((point.daily_cny for point in points), Decimal("0")),
        PrimaryCategory.TRAVEL: sum((point.travel_cny for point in points), Decimal("0")),
        PrimaryCategory.HOUSING: sum((point.housing_cny for point in points), Decimal("0")),
    }
    breakdowns: list[SecondaryBreakdown] = []
    for (primary, secondary_name), (amount, count) in sorted(secondary.items(), key=lambda item: (-item[1][0], item[0])):
        primary_amount = primary_totals[primary]
        breakdowns.append(SecondaryBreakdown(primary, secondary_name, amount, count,
            amount / window_total if window_total else None,
            amount / primary_amount if primary_amount else None))
    def average(values: Iterable[MonthlySpendingPoint]) -> AverageMetric:
        complete = [
            item.total_spending_cny for item in values
            if item.data_coverage_status == DataCoverageStatus.COMPLETE and item.amount_complete
        ]
        return AverageMetric(
            sum(complete, Decimal("0")) / len(complete) if complete else None,
            len(complete),
        )

    all_complete = average(points)
    return SpendingSummary(
        tuple(points), tuple(breakdowns), all_complete.amount_cny, all_complete.months_used,
        average(points[-3:]), average(points[-12:]),
    )
