"""Read-only ORM adapter and service for the frozen spending analytics contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services.consumption.analytics_design import (
    ActiveEventProjection, ActiveInterpretation, SourceCoverageInput,
    SourceCoverageStatus, SpendingSummary, evaluate_spending, month_start,
)


_DETAIL_DESCRIPTION = {
    "FOOD_DINING": "餐饮消费",
    "TRANSPORT_AUTO": "交通用车",
    "SHOPPING": "购物消费",
    "HOME_LIVING": "居家生活",
    "DIGITAL_COMMUNICATION": "数字与通讯",
    "HEALTH_INSURANCE": "健康保障",
    "SPORTS_HOBBY": "运动兴趣",
    "PET": "宠物消费",
    "LONG_DISTANCE_TRANSPORT": "大交通",
    "ACCOMMODATION": "住宿",
    "LOCAL_TRANSPORT": "当地交通",
    "ACTIVITIES_EXPERIENCES": "活动体验",
    "TRAVEL_SHOPPING": "旅行购物",
    "RENT": "房租",
    "PROPERTY_FEE": "物业费",
}


@dataclass(frozen=True)
class MonthlySpendingDetailItem:
    analytics_effective_date: date
    display_description: str
    account_display_name: str
    primary_category: str | None
    secondary_category: str | None
    classification_status: str
    amount_cny: Decimal


@dataclass(frozen=True)
class MonthlySpendingDetailPage:
    month: date
    items: tuple[MonthlySpendingDetailItem, ...]
    total: int
    limit: int
    offset: int


def _safe_detail_description(secondary_category: str | None) -> str:
    """Return a bounded semantic label, never source-statement text."""
    return _DETAIL_DESCRIPTION.get(secondary_category or "", "未识别消费")


def _safe_account_display_name(value: str | None, institution: str) -> str:
    label = " ".join((value or f"{institution}账户").split())[:40]
    return re.sub(r"(?:\*{2,})?\d{2,}", "****", label) or f"{institution}账户"
from backend.services.consumption.classification_design import ClassificationStatus, EligibilityStatus, PrimaryCategory
from backend.services.consumption.economic_events import EventType
from backend.services.consumption.models import (
    Account, ConsumptionInterpretation, EconomicEvent, EconomicEventProjectionRevision,
    EventRawLink, ImportBatch, RawTransaction,
)


class ConsumptionAnalyticsQueryAdapter:
    """Maps active ORM rows to DTOs; it never aggregates Raw amounts or writes."""
    def __init__(self, session: Session): self.session = session

    def expected_account_ids(self, selected: tuple[str, ...] | None = None) -> tuple[str, ...]:
        if selected is not None:
            return tuple(sorted(set(selected)))
        return tuple(row.id for row in self.session.query(Account).filter_by(status="ACTIVE").order_by(Account.id))

    def active_events(self, account_ids: tuple[str, ...], start: date, end: date):
        if not account_ids:
            return ()
        rows = (self.session.query(EconomicEvent, EconomicEventProjectionRevision, ConsumptionInterpretation, RawTransaction.account_id)
            .outerjoin(EconomicEventProjectionRevision, (EconomicEventProjectionRevision.event_id == EconomicEvent.id) & EconomicEventProjectionRevision.is_active.is_(True))
            .join(ConsumptionInterpretation, (ConsumptionInterpretation.event_id == EconomicEvent.id) & ConsumptionInterpretation.is_active.is_(True))
            .join(EventRawLink, (EventRawLink.event_id == EconomicEvent.id) & EventRawLink.is_active.is_(True))
            .join(RawTransaction, RawTransaction.id == EventRawLink.raw_transaction_id)
            .filter(EconomicEvent.is_active.is_(True), EconomicEvent.analytics_effective_date >= start, EconomicEvent.analytics_effective_date <= end)
            .order_by(EconomicEvent.id, RawTransaction.id).all())
        output=[]; seen=set()
        for event, projection, interpretation, account_id in rows:
            if event.id in seen or account_id not in account_ids: continue
            seen.add(event.id)
            output.append((
                ActiveEventProjection(event.id, EventType(event.event_type), event.analytics_effective_date, account_id, event.amount, projection.base_net_amount if projection else None, event.fx_source, event.currency),
                ActiveInterpretation(event.id, EligibilityStatus(interpretation.eligibility_status), ClassificationStatus(interpretation.classification_status), PrimaryCategory(interpretation.primary_category) if interpretation.primary_category else None, interpretation.secondary_category),
            ))
        return tuple(output)

    def coverage(self, account_ids: tuple[str, ...], start: date, end: date) -> tuple[SourceCoverageInput, ...]:
        batches = self.session.query(ImportBatch).filter(ImportBatch.account_id.in_(account_ids), ImportBatch.status == "COMPLETED").all() if account_ids else []
        values=[]; cursor=month_start(start)
        while cursor <= month_start(end):
            for account_id in account_ids:
                month_end=(date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1) - timedelta(days=1))
                relevant=[row for row in batches if row.account_id == account_id and ((row.statement_period_start and row.statement_period_start <= cursor and row.statement_period_end and row.statement_period_end >= cursor) or (not row.statement_period_start and row.observed_transaction_start and row.observed_transaction_end and row.observed_transaction_start <= month_end and row.observed_transaction_end >= cursor))]
                if not relevant: values.append(SourceCoverageInput(account_id,cursor,SourceCoverageStatus.UNKNOWN)); continue
                status = "OBSERVED_ONLY" if any(row.coverage_status == "OBSERVED_ONLY" for row in relevant) else "EXPLICIT"
                observed=max((row.observed_transaction_end for row in relevant if row.observed_transaction_end), default=None)
                values.append(SourceCoverageInput(account_id,cursor,SourceCoverageStatus(status),observed))
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        return tuple(values)

    def monthly_detail(
        self,
        month: date,
        account_ids: tuple[str, ...],
        *,
        limit: int,
        offset: int,
    ) -> MonthlySpendingDetailPage:
        """Read a bounded selected-month view from active event projections only."""
        end = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
        primary_link_id = (
            self.session.query(func.min(EventRawLink.id))
            .filter(EventRawLink.event_id == EconomicEvent.id, EventRawLink.is_active.is_(True))
            .correlate(EconomicEvent)
            .scalar_subquery()
        )
        query = (
            self.session.query(
                EconomicEvent,
                EconomicEventProjectionRevision,
                ConsumptionInterpretation,
                RawTransaction,
                Account,
            )
            .join(
                EconomicEventProjectionRevision,
                (EconomicEventProjectionRevision.event_id == EconomicEvent.id)
                & EconomicEventProjectionRevision.is_active.is_(True),
            )
            .join(
                ConsumptionInterpretation,
                (ConsumptionInterpretation.event_id == EconomicEvent.id)
                & ConsumptionInterpretation.is_active.is_(True),
            )
            .join(EventRawLink, EventRawLink.id == primary_link_id)
            .join(RawTransaction, RawTransaction.id == EventRawLink.raw_transaction_id)
            .join(Account, Account.id == RawTransaction.account_id)
            .filter(
                EconomicEvent.is_active.is_(True),
                EconomicEvent.event_type.in_((EventType.CONSUMPTION.value, EventType.OTHER.value)),
                ConsumptionInterpretation.eligibility_status == EligibilityStatus.ELIGIBLE.value,
                EconomicEventProjectionRevision.base_net_amount.is_not(None),
                EconomicEvent.analytics_effective_date >= month,
                EconomicEvent.analytics_effective_date < end,
                RawTransaction.account_id.in_(account_ids),
            )
        )
        total = query.count()
        rows = (
            query.order_by(
                EconomicEventProjectionRevision.base_net_amount.desc(),
                EconomicEvent.analytics_effective_date.desc(),
                EconomicEvent.id.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return MonthlySpendingDetailPage(
            month=month,
            items=tuple(
                MonthlySpendingDetailItem(
                    analytics_effective_date=event.analytics_effective_date,
                    display_description=_safe_detail_description(interpretation.secondary_category),
                    account_display_name=_safe_account_display_name(account.display_name, account.institution),
                    primary_category=interpretation.primary_category,
                    secondary_category=interpretation.secondary_category,
                    classification_status=interpretation.classification_status,
                    amount_cny=Decimal(projection.base_net_amount),
                )
                for event, projection, interpretation, _raw, account in rows
            ),
            total=total,
            limit=limit,
            offset=offset,
        )


class ConsumptionAnalyticsService:
    """Read-only deterministic service. No LLM, network, Raw aggregation, or mutation."""
    def __init__(self, session: Session): self.adapter=ConsumptionAnalyticsQueryAdapter(session)

    def summary(self, *, as_of: date, months: int = 12, account_ids: tuple[str, ...] | None = None) -> SpendingSummary:
        if not 1 <= months <= 24: raise ValueError("months must be between 1 and 24")
        accounts=self.adapter.expected_account_ids(account_ids)
        start=date(as_of.year, as_of.month, 1)
        for _ in range(months-1): start=date(start.year - (start.month == 1), 12 if start.month == 1 else start.month-1, 1)
        pairs=self.adapter.active_events(accounts,start,as_of)
        return evaluate_spending((item[0] for item in pairs),(item[1] for item in pairs),self.adapter.coverage(accounts,start,as_of),as_of_date=as_of,expected_account_ids=accounts,month_count=months)

    def monthly_detail(
        self,
        *,
        month: date,
        limit: int = 100,
        offset: int = 0,
        account_ids: tuple[str, ...] | None = None,
    ) -> MonthlySpendingDetailPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must not be negative")
        return self.adapter.monthly_detail(month, self.adapter.expected_account_ids(account_ids), limit=limit, offset=offset)
