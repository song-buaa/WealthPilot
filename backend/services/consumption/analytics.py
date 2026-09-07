"""Read-only ORM adapter and service for the frozen spending analytics contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import csv
from io import StringIO
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services.consumption.analytics_design import (
    ActiveEventProjection, ActiveInterpretation, SourceCoverageInput,
    SourceCoverageStatus, SpendingSummary, evaluate_spending, month_start,
)


@dataclass(frozen=True)
class MonthlySpendingDetailItem:
    event_id: str
    analytics_effective_date: date
    raw_description: str
    account_display_name: str
    primary_category: str | None
    secondary_category: str | None
    classification_status: str
    amount_cny: Decimal
    user_note: str | None


@dataclass(frozen=True)
class MonthlySpendingDetailPage:
    month: date
    items: tuple[MonthlySpendingDetailItem, ...]
    total: int
    limit: int
    offset: int


from backend.services.consumption.classification_design import ClassificationStatus, EligibilityStatus, PrimaryCategory
from backend.services.consumption.economic_events import EventType
from backend.services.consumption.models import (
    Account, ConsumptionInterpretation, EconomicEvent, EconomicEventProjectionRevision,
    ConsumptionEventNote, EventRawLink, ImportBatch, ManualConsumptionEntry, RawTransaction,
)
from backend.services.consumption.presentation import account_display_label


class ConsumptionAnalyticsQueryAdapter:
    """Maps active ORM rows to DTOs; it never aggregates Raw amounts or writes."""
    def __init__(self, session: Session): self.session = session

    def expected_account_ids(self, selected: tuple[str, ...] | None = None) -> tuple[str, ...]:
        if selected is not None:
            return tuple(sorted(set(selected)))
        return tuple(row.id for row in self.session.query(Account).filter_by(status="ACTIVE").order_by(Account.id))

    def latest_consumption_date(self, account_ids: tuple[str, ...]) -> date | None:
        """Return the latest actual eligible consumption fact, never a calendar placeholder."""
        events = self.active_events(account_ids, date.min, date.max)
        dates = [
            projection.analytics_effective_date
            for projection, interpretation in events
            if projection.event_type in {EventType.CONSUMPTION, EventType.OTHER}
            and interpretation.eligibility_status == EligibilityStatus.ELIGIBLE
            and projection.base_net_amount is not None
        ]
        return max(dates, default=None)

    def active_events(self, account_ids: tuple[str, ...], start: date, end: date):
        output=[]; seen=set()
        if account_ids:
            rows = (self.session.query(EconomicEvent, EconomicEventProjectionRevision, ConsumptionInterpretation, RawTransaction.account_id)
                .outerjoin(EconomicEventProjectionRevision, (EconomicEventProjectionRevision.event_id == EconomicEvent.id) & EconomicEventProjectionRevision.is_active.is_(True))
                .join(ConsumptionInterpretation, (ConsumptionInterpretation.event_id == EconomicEvent.id) & ConsumptionInterpretation.is_active.is_(True))
                .join(EventRawLink, (EventRawLink.event_id == EconomicEvent.id) & EventRawLink.is_active.is_(True))
                .join(RawTransaction, RawTransaction.id == EventRawLink.raw_transaction_id)
                .filter(EconomicEvent.is_active.is_(True), EconomicEvent.analytics_effective_date >= start, EconomicEvent.analytics_effective_date <= end)
                .order_by(EconomicEvent.id, RawTransaction.id).all())
            for event, projection, interpretation, account_id in rows:
                if event.id in seen or account_id not in account_ids: continue
                seen.add(event.id)
                output.append((
                    ActiveEventProjection(event.id, EventType(event.event_type), event.analytics_effective_date, account_id, event.amount, projection.base_net_amount if projection else None, event.fx_source, event.currency),
                    ActiveInterpretation(event.id, EligibilityStatus(interpretation.eligibility_status), ClassificationStatus(interpretation.classification_status), PrimaryCategory(interpretation.primary_category) if interpretation.primary_category else None, interpretation.secondary_category),
                ))
        manual_rows = (self.session.query(EconomicEvent, EconomicEventProjectionRevision, ConsumptionInterpretation, ManualConsumptionEntry)
            .join(EconomicEventProjectionRevision, (EconomicEventProjectionRevision.event_id == EconomicEvent.id) & EconomicEventProjectionRevision.is_active.is_(True))
            .join(ConsumptionInterpretation, (ConsumptionInterpretation.event_id == EconomicEvent.id) & ConsumptionInterpretation.is_active.is_(True))
            .join(ManualConsumptionEntry, (ManualConsumptionEntry.event_id == EconomicEvent.id) & ManualConsumptionEntry.is_active.is_(True))
            .filter(EconomicEvent.is_active.is_(True), EconomicEvent.analytics_effective_date >= start, EconomicEvent.analytics_effective_date <= end)
            .order_by(EconomicEvent.id).all())
        for event, projection, interpretation, entry in manual_rows:
            if event.id in seen: continue
            seen.add(event.id)
            output.append((
                ActiveEventProjection(event.id, EventType(event.event_type), event.analytics_effective_date, f"manual:{entry.id}", event.amount, projection.base_net_amount, event.fx_source, event.currency),
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
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> MonthlySpendingDetailPage:
        """Read a bounded selected-month view from active event projections only."""
        end = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
        return self.detail_in_period(
            month, end, account_ids, limit=limit, offset=offset,
            classification_status=classification_status, primary_category=primary_category,
            secondary_category=secondary_category,
        )

    def detail_in_period(
        self,
        start: date,
        end: date,
        account_ids: tuple[str, ...],
        *,
        limit: int,
        offset: int,
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> MonthlySpendingDetailPage:
        """Read a closed-start, open-end month range from active projections."""
        if start >= end:
            raise ValueError("detail range end must follow start")
        primary_link_id = (
            self.session.query(func.min(EventRawLink.id))
            .filter(EventRawLink.event_id == EconomicEvent.id, EventRawLink.is_active.is_(True))
            .correlate(EconomicEvent)
            .scalar_subquery()
        )
        raw_query = (
            self.session.query(
                EconomicEvent,
                EconomicEventProjectionRevision,
                ConsumptionInterpretation,
                RawTransaction,
                Account,
                ConsumptionEventNote,
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
            .outerjoin(ConsumptionEventNote, ConsumptionEventNote.event_id == EconomicEvent.id)
            .filter(
                EconomicEvent.is_active.is_(True),
                EconomicEvent.event_type.in_((EventType.CONSUMPTION.value, EventType.OTHER.value)),
                ConsumptionInterpretation.eligibility_status == EligibilityStatus.ELIGIBLE.value,
                EconomicEventProjectionRevision.base_net_amount.is_not(None),
                EconomicEvent.analytics_effective_date >= start,
                EconomicEvent.analytics_effective_date < end,
                RawTransaction.account_id.in_(account_ids),
            )
        )
        manual_query = (
            self.session.query(
                EconomicEvent,
                EconomicEventProjectionRevision,
                ConsumptionInterpretation,
                ManualConsumptionEntry,
                ConsumptionEventNote,
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
            .join(
                ManualConsumptionEntry,
                (ManualConsumptionEntry.event_id == EconomicEvent.id)
                & ManualConsumptionEntry.is_active.is_(True),
            )
            .outerjoin(ConsumptionEventNote, ConsumptionEventNote.event_id == EconomicEvent.id)
            .filter(
                EconomicEvent.is_active.is_(True),
                EconomicEvent.event_type.in_((EventType.CONSUMPTION.value, EventType.OTHER.value)),
                ConsumptionInterpretation.eligibility_status == EligibilityStatus.ELIGIBLE.value,
                EconomicEventProjectionRevision.base_net_amount.is_not(None),
                EconomicEvent.analytics_effective_date >= start,
                EconomicEvent.analytics_effective_date < end,
            )
        )
        def apply_detail_filters(query):
            if classification_status is not None:
                query = query.filter(ConsumptionInterpretation.classification_status == classification_status.value)
            if primary_category is not None:
                query = query.filter(ConsumptionInterpretation.primary_category == primary_category.value)
            if secondary_category is not None:
                query = query.filter(ConsumptionInterpretation.secondary_category == secondary_category)
            return query

        raw_query = apply_detail_filters(raw_query)
        manual_query = apply_detail_filters(manual_query)
        items = [
            MonthlySpendingDetailItem(
                event_id=event.id,
                analytics_effective_date=event.analytics_effective_date,
                raw_description=raw.raw_description,
                account_display_name=account_display_label(
                    account.display_name, account.institution, account.account_type,
                ),
                primary_category=interpretation.primary_category,
                secondary_category=interpretation.secondary_category,
                classification_status=interpretation.classification_status,
                amount_cny=Decimal(projection.base_net_amount),
                user_note=note.note if note else None,
            )
            for event, projection, interpretation, raw, account, note in raw_query.all()
        ]
        items.extend(
            MonthlySpendingDetailItem(
                event_id=event.id,
                analytics_effective_date=event.analytics_effective_date,
                raw_description=entry.description,
                account_display_name="人工补录",
                primary_category=interpretation.primary_category,
                secondary_category=interpretation.secondary_category,
                classification_status=interpretation.classification_status,
                amount_cny=Decimal(projection.base_net_amount),
                user_note=note.note if note else None,
            )
            for event, projection, interpretation, entry, note in manual_query.all()
        )
        items.sort(key=lambda item: (-item.amount_cny, -item.analytics_effective_date.toordinal(), item.event_id))
        total = len(items)
        return MonthlySpendingDetailPage(
            month=start,
            items=tuple(items[offset:offset + limit]),
            total=total,
            limit=limit,
            offset=offset,
        )


class ConsumptionAnalyticsService:
    """Read-only deterministic service. No LLM, network, Raw aggregation, or mutation."""
    def __init__(self, session: Session): self.adapter=ConsumptionAnalyticsQueryAdapter(session)

    def summary(self, *, as_of: date | None = None, months: int = 12, account_ids: tuple[str, ...] | None = None) -> SpendingSummary:
        if not 1 <= months <= 24: raise ValueError("months must be between 1 and 24")
        accounts=self.adapter.expected_account_ids(account_ids)
        effective_as_of = as_of or self.adapter.latest_consumption_date(accounts) or date.today()
        start=date(effective_as_of.year, effective_as_of.month, 1)
        for _ in range(months-1): start=date(start.year - (start.month == 1), 12 if start.month == 1 else start.month-1, 1)
        pairs=self.adapter.active_events(accounts,start,effective_as_of)
        return evaluate_spending((item[0] for item in pairs),(item[1] for item in pairs),self.adapter.coverage(accounts,start,effective_as_of),as_of_date=effective_as_of,expected_account_ids=accounts,month_count=months)

    def monthly_detail(
        self,
        *,
        month: date,
        limit: int = 100,
        offset: int = 0,
        account_ids: tuple[str, ...] | None = None,
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> MonthlySpendingDetailPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must not be negative")
        return self.adapter.monthly_detail(
            month, self.adapter.expected_account_ids(account_ids), limit=limit, offset=offset,
            classification_status=classification_status, primary_category=primary_category,
            secondary_category=secondary_category,
        )

    def detail_in_month_range(
        self,
        *,
        start_month: date,
        end_month: date,
        limit: int = 100,
        offset: int = 0,
        account_ids: tuple[str, ...] | None = None,
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> MonthlySpendingDetailPage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must not be negative")
        if end_month < start_month:
            raise ValueError("end_month must not precede start_month")
        end = date(end_month.year + (end_month.month == 12), 1 if end_month.month == 12 else end_month.month + 1, 1)
        return self.adapter.detail_in_period(
            start_month, end, self.adapter.expected_account_ids(account_ids), limit=limit, offset=offset,
            classification_status=classification_status, primary_category=primary_category,
            secondary_category=secondary_category,
        )

    def export_monthly_detail_csv(
        self,
        *,
        month: date,
        account_ids: tuple[str, ...] | None = None,
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> str:
        """Export the selected month's current detail projection as CSV."""
        return self.export_detail_range_csv(
            start_month=month, end_month=month, account_ids=account_ids,
            classification_status=classification_status, primary_category=primary_category,
            secondary_category=secondary_category,
        )

    def export_detail_range_csv(
        self,
        *,
        start_month: date,
        end_month: date,
        account_ids: tuple[str, ...] | None = None,
        classification_status: ClassificationStatus | None = None,
        primary_category: PrimaryCategory | None = None,
        secondary_category: str | None = None,
    ) -> str:
        """Export every current detail row in an inclusive natural-month range."""
        if end_month < start_month:
            raise ValueError("end_month must not precede start_month")
        end = date(end_month.year + (end_month.month == 12), 1 if end_month.month == 12 else end_month.month + 1, 1)
        page = self.adapter.detail_in_period(
            start_month, end, self.adapter.expected_account_ids(account_ids), limit=100_000, offset=0,
            classification_status=classification_status, primary_category=primary_category,
            secondary_category=secondary_category,
        )
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["日期", "消费名称", "一级分类", "二级分类", "账户", "金额（CNY）", "备注", "分类状态"])
        writer.writerows(
            (
                item.analytics_effective_date.isoformat(), item.raw_description,
                item.primary_category or "", item.secondary_category or "",
                item.account_display_name, format(item.amount_cny, "f"), item.user_note or "", item.classification_status,
            )
            for item in page.items
        )
        return output.getvalue()
