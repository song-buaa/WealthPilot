"""Regression coverage for explicit user-provided consumption facts."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from backend.scripts.backfill_historical_rent import backfill_historical_rent
from backend.services.consumption.analytics import ConsumptionAnalyticsService
from backend.services.consumption.classification_design import PrimaryCategory
from backend.services.consumption.manual_events import ManualConsumptionSpec, upsert_manual_consumption
from backend.services.consumption.models import (
    ConsumptionInterpretation, EconomicEvent, EconomicEventProjectionRevision,
    ImportBatch, ManualConsumptionEntry, RawTransaction,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def test_manual_fact_is_idempotent_and_uses_no_bank_raw_or_import_batch():
    session, engine = _session()
    try:
        spec = ManualConsumptionSpec(
            id="manual-rent-2025-09",
            occurred_on=date(2025, 9, 15),
            amount=Decimal("4500"),
            description="支付宝房租",
            primary_category=PrimaryCategory.HOUSING,
            secondary_category="RENT",
        )
        assert upsert_manual_consumption(session, spec) is True
        assert upsert_manual_consumption(session, spec) is False
        session.commit()

        entry = session.get(ManualConsumptionEntry, spec.id)
        event = session.get(EconomicEvent, spec.id)
        interpretation = session.query(ConsumptionInterpretation).filter_by(event_id=spec.id, is_active=True).one()
        projection = session.query(EconomicEventProjectionRevision).filter_by(event_id=spec.id, is_active=True).one()
        assert entry and event
        assert (entry.source, entry.description, entry.occurred_on, Decimal(entry.amount)) == ("USER_PROVIDED", "支付宝房租", date(2025, 9, 15), Decimal("4500"))
        assert (event.event_type, event.analytics_effective_date, Decimal(event.amount)) == ("CONSUMPTION", date(2025, 9, 15), Decimal("4500"))
        assert (interpretation.user_confirmed, interpretation.primary_category, interpretation.secondary_category) == (True, "HOUSING", "RENT")
        assert Decimal(projection.base_net_amount) == Decimal("4500")
        assert session.query(RawTransaction).count() == session.query(ImportBatch).count() == 0

        summary = ConsumptionAnalyticsService(session).summary(as_of=date(2025, 9, 30), months=1)
        assert (summary.months[0].total_spending_cny, summary.months[0].housing_cny) == (Decimal("4500"), Decimal("4500"))
        detail = ConsumptionAnalyticsService(session).monthly_detail(month=date(2025, 9, 1))
        assert [(item.raw_description, item.account_display_name, item.amount_cny) for item in detail.items] == [("支付宝房租", "人工补录", Decimal("4500"))]
    finally:
        session.close()
        engine.dispose()


def test_historical_rent_backfill_creates_eight_entries_once():
    session, engine = _session()
    try:
        first = backfill_historical_rent(session)
        session.commit()
        assert (first.created_count, first.reused_count) == (8, 0)
        second = backfill_historical_rent(session)
        assert (second.created_count, second.reused_count) == (0, 8)
        assert session.query(ManualConsumptionEntry).count() == 8
        assert session.query(RawTransaction).count() == session.query(ImportBatch).count() == 0
        assert sum(Decimal(item.amount) for item in session.query(ManualConsumptionEntry).all()) == Decimal("36000")
    finally:
        session.close()
        engine.dispose()
