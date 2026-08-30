"""Read-only adapter, service, and API regression coverage."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from backend.api import consumption as consumption_api
from backend.services.consumption.analytics import ConsumptionAnalyticsService, ConsumptionAnalyticsQueryAdapter
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.models import (
    Account, ConsumptionInterpretation, ConsumptionInterpretationAudit, EconomicEvent, EconomicEventProjectionRevision,
    EventRawLink, ImportBatch, RawTransaction, UserClassificationRule,
)

FIXTURES=Path(__file__).parents[4] / "tests" / "fixtures" / "consumption" / "analytics"


@pytest.fixture
def db_session():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool); Base.metadata.create_all(engine); session=sessionmaker(bind=engine)()
    try: yield session
    finally: session.close(); engine.dispose()


def _account(session, account_id="card"):
    existing=session.get(Account,account_id)
    if existing: return existing
    item=Account(id=account_id,institution="TEST",account_type="CREDIT_CARD",display_name="Masked test account")
    session.add(item); session.flush(); return item


def _event(session, event_id, *, account, event_type="CONSUMPTION", when=date(2026,7,3), amount="100", net="100", currency="CNY", eligibility="ELIGIBLE", classification="CLASSIFIED", primary="DAILY", secondary="FOOD_DINING", active=True, description="synthetic"):
    period_start=when.replace(day=1); period_end=(date(period_start.year + (period_start.month == 12),1 if period_start.month == 12 else period_start.month+1,1)-timedelta(days=1))
    batch=ImportBatch(id=f"batch-{event_id}",account_id=account.id,source_format="TEST",institution="TEST",statement_type=account.account_type,source_file_hash=(event_id*64)[:64],parser_version="test",statement_period_start=period_start,statement_period_end=period_end,statement_period_availability="AVAILABLE",coverage_status="EXPLICIT",observed_transaction_start=period_start,observed_transaction_end=period_end,row_count=1)
    raw=RawTransaction(id=f"raw-{event_id}",import_batch_id=batch.id,account_id=account.id,source_row_index=1,source_row_identity=f"row-{event_id}",source_row_fingerprint_candidate=f"source-{event_id}",match_fingerprint=f"match-{event_id}",dedup_status="UNIQUE",transaction_date=when,transaction_date_availability="AVAILABLE",posting_date=None,posting_date_availability="SOURCE_UNAVAILABLE",amount=Decimal(amount),currency=currency,raw_description=description,parser_provenance="{}",source_field_availability="{}")
    event=EconomicEvent(id=event_id,semantic_key=f"key-{event_id}",event_type=event_type,event_date=when,analytics_effective_date=when,amount=Decimal(amount),currency=currency,economic_direction="OUTFLOW",base_currency="CNY",base_amount=Decimal(amount),fx_rate=Decimal("1"),fx_source="NATIVE_CNY",resolution_status="RESOLVED",normalizer_version="test",rule_sources="[]",provenance="{}",is_active=active)
    session.add_all((batch,raw,event)); session.flush(); session.add(EventRawLink(event_id=event.id,raw_transaction_id=raw.id,link_role="PRIMARY",rule_source="TEST",evidence="{}"))
    if net is not None:
        session.add(EconomicEventProjectionRevision(event_id=event.id,revision_number=1,gross_amount=Decimal(amount),refund_amount=Decimal(amount)-Decimal(net),net_amount=Decimal(net),base_currency="CNY",base_net_amount=Decimal(net),reason="TEST",rule_source="TEST"))
    session.add(ConsumptionInterpretation(event_id=event.id,eligibility_status=eligibility,eligibility_source="SYSTEM_RULE",eligibility_reason="TEST",classification_status=classification,primary_category=primary,secondary_category=secondary,classification_source="SYSTEM_RULE",classification_reason="TEST",revision_number=1,resolver_version="test"))
    session.flush(); return event


def test_adapter_reads_only_active_rows_and_service_keeps_review_boundaries(db_session):
    account=_account(db_session)
    classified=_event(db_session,"classified",account=account,amount="100",net="80")
    _event(db_session,"unclassified",account=account,amount="20",net="20",classification="NEEDS_REVIEW",primary=None,secondary=None)
    _event(db_session,"eligibility-review",account=account,event_type="OTHER",amount="6500",net=None,eligibility="NEEDS_REVIEW",classification="NOT_APPLICABLE",primary=None,secondary=None)
    _event(db_session,"repayment",account=account,event_type="CREDIT_CARD_REPAYMENT",amount="500",net="500")
    inactive=_event(db_session,"inactive",account=account,amount="999",net="999",active=False)
    current=db_session.query(EconomicEventProjectionRevision).filter_by(event_id=classified.id,is_active=True).one(); current.is_active=False
    db_session.add(EconomicEventProjectionRevision(event_id=classified.id,revision_number=2,gross_amount=Decimal("100"),refund_amount=Decimal("10"),net_amount=Decimal("90"),base_currency="CNY",base_net_amount=Decimal("90"),reason="REFUND",rule_source="TEST",supersedes_revision_id=current.id))
    db_session.flush()
    pairs=ConsumptionAnalyticsQueryAdapter(db_session).active_events((account.id,),date(2026,7,1),date(2026,7,31))
    assert {item[0].event_id for item in pairs} == {"classified","unclassified","eligibility-review","repayment"}
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(account.id,))
    point=result.months[0]
    assert (point.total_spending_cny,point.daily_cny,point.unclassified_eligible_cny,point.eligibility_review_count)==(Decimal("110"),Decimal("90"),Decimal("20"),1)
    assert point.classification_coverage_rate == Decimal("90") / Decimal("110")


def test_scope_coverage_partial_and_bounded_months(db_session):
    card=_account(db_session,"card"); _event(db_session,"one",account=card,when=date(2026,8,3))
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,8,10),months=1,account_ids=(card.id,))
    assert result.months[0].is_partial_month is True
    assert result.months[0].comparison_available is False
    with pytest.raises(ValueError): ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,8,10),months=25)


def test_production_case_v_inactive_interpretation_is_ignored(db_session):
    card=_account(db_session); event=_event(db_session,"interpretation",account=card)
    old=db_session.query(ConsumptionInterpretation).filter_by(event_id=event.id,is_active=True).one(); old.is_active=False
    db_session.add(ConsumptionInterpretation(event_id=event.id,eligibility_status="ELIGIBLE",eligibility_source="TEST",eligibility_reason="TEST",classification_status="CLASSIFIED",primary_category="TRAVEL",secondary_category="ACCOMMODATION",classification_source="TEST",classification_reason="TEST",revision_number=2,resolver_version="test",supersedes_revision_id=old.id))
    db_session.flush()
    point=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(card.id,)).months[0]
    assert (point.daily_cny,point.travel_cny)==(Decimal("0"),Decimal("100"))


def test_production_case_x_complete_month_comparison_is_enabled(db_session):
    card=_account(db_session); _event(db_session,"july",account=card,when=date(2026,7,3)); _event(db_session,"august",account=card,when=date(2026,8,3))
    points=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,8,31),months=2,account_ids=(card.id,)).months
    assert (points[0].comparison_available,points[1].comparison_available)==(False,True)


def test_production_case_y_averages_skip_incomplete_months(db_session):
    card=_account(db_session); _event(db_session,"june",account=card,when=date(2026,6,3),amount="3",net="3"); _event(db_session,"july",account=card,when=date(2026,7,3),amount="6",net="6"); _event(db_session,"august",account=card,when=date(2026,8,3),amount="9",net="9")
    db_session.query(ImportBatch).filter_by(id="batch-june").update({ImportBatch.coverage_status:"OBSERVED_ONLY",ImportBatch.statement_period_start:None,ImportBatch.statement_period_end:None,ImportBatch.observed_transaction_start:date(2026,6,1),ImportBatch.observed_transaction_end:date(2026,6,30)})
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,8,31),months=3,account_ids=(card.id,))
    assert (result.three_month_average.amount_cny,result.three_month_average.months_used)==(Decimal("7.5"),2)
    assert (result.twelve_month_average.amount_cny,result.twelve_month_average.months_used)==(Decimal("7.5"),2)


def test_production_case_z_missing_expected_account_is_unknown(db_session):
    card=_account(db_session); _event(db_session,"one",account=card)
    point=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(card.id,"not-imported")).months[0]
    assert point.data_coverage_status == "UNKNOWN"


def test_production_case_aa_observed_only_source_is_limited(db_session):
    card=_account(db_session); _event(db_session,"one",account=card)
    db_session.query(ImportBatch).filter_by(id="batch-one").update({ImportBatch.coverage_status:"OBSERVED_ONLY",ImportBatch.statement_period_start:None,ImportBatch.statement_period_end:None,ImportBatch.observed_transaction_start:date(2026,7,1),ImportBatch.observed_transaction_end:date(2026,7,31)})
    point=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(card.id,)).months[0]
    assert point.data_coverage_status == "SOURCE_LIMITED"


@pytest.mark.parametrize("case_dir",sorted(path for path in FIXTURES.iterdir() if path.is_dir()),ids=lambda path:path.name.upper())
def test_design_a_through_t_execute_through_orm_adapter_and_service(db_session,case_dir):
    events=json.loads((case_dir/"input_events.json").read_text()); interpretations={item["event_id"]:item for item in json.loads((case_dir/"input_interpretations.json").read_text())}
    coverage=json.loads((case_dir/"input_coverage.json").read_text()); expected=json.loads((case_dir/"expected_analytics.json").read_text())
    for item in events:
        account=_account(db_session,item["account_id"])
        value=interpretations[item["event_id"]]
        _event(db_session,item["event_id"],account=account,event_type=item["event_type"],when=date.fromisoformat(item["analytics_effective_date"]),amount=item["original_amount"],net=item["base_net_amount"],eligibility=value["eligibility_status"],classification=value["classification_status"],primary=value.get("primary_category"),secondary=value.get("secondary_category"))
    for item in coverage["coverage"]:
        db_session.query(ImportBatch).filter_by(account_id=item["account_id"]).update({ImportBatch.coverage_status:item["status"]})
    scope=tuple(sorted(set(coverage["expected_account_ids"]) | {item["account_id"] for item in events}))
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date.fromisoformat(coverage["as_of_date"]),months=12,account_ids=scope)
    point=next(item for item in result.months if item.month == date.fromisoformat(expected["month"]))
    for field,value in expected.items():
        if field == "month": continue
        actual=getattr(point,field)
        if isinstance(actual,Decimal): assert actual == Decimal(value)
        elif hasattr(actual,"value"): assert actual.value == value
        else: assert actual == value


def test_production_cases_ab_and_ac_fx_unresolved_and_calendar_ordering(db_session):
    card=_account(db_session,"card"); _event(db_session,"july",account=card,when=date(2026,7,3),amount="10",net="10")
    _event(db_session,"aug-fx",account=card,when=date(2026,8,3),amount="100",net=None,currency="USD",classification="NEEDS_REVIEW",primary=None,secondary=None)
    db_session.query(ImportBatch).filter_by(id="batch-july").update({ImportBatch.coverage_status:"OBSERVED_ONLY",ImportBatch.statement_period_start:None,ImportBatch.statement_period_end:None,ImportBatch.observed_transaction_start:date(2026,7,1),ImportBatch.observed_transaction_end:date(2026,7,31)})
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,8,10),months=2,account_ids=(card.id,"missing"))
    assert [item.month for item in result.months] == [date(2026,7,1),date(2026,8,1)]
    assert result.months[0].data_coverage_status == "UNKNOWN"
    assert (result.months[1].amount_complete,result.months[1].amount_unresolved_count,result.months[1].total_spending_cny) == (False,1,Decimal("0"))
    assert [(item.currency,item.amount,item.event_count) for item in result.months[1].amount_unresolved_by_currency] == [("USD",Decimal("100"),1)]


def test_api_is_get_only_serializes_decimals_and_honors_account_filter(db_session, monkeypatch):
    card=_account(db_session,"card"); other=_account(db_session,"other")
    _event(db_session,"card-event",account=card,amount="12.50",net="12.50")
    _event(db_session,"other-event",account=other,amount="99",net="99")
    monkeypatch.setattr(consumption_api,"get_session",lambda: db_session)
    from fastapi import FastAPI
    app=FastAPI(); app.include_router(consumption_api.router,prefix="/api/consumption")
    response=TestClient(app).get("/api/consumption/analytics?as_of=2026-07-31&months=1&account_ids=card")
    assert response.status_code == 200
    body=response.json(); assert Decimal(body["months"][0]["total_spending_cny"]) == Decimal("12.50")
    assert body["months"][0]["secondary_breakdowns"] == [{
        "primary_category": "DAILY", "secondary_category": "FOOD_DINING", "amount_cny": "12.50000000",
        "event_count": 1, "share_of_total": "1", "share_within_primary": "1",
    }]
    assert body["three_month_average"]["months_used"] == 1
    assert {next(iter(route.methods)) for route in app.routes if getattr(route,"path","") == "/api/consumption/analytics"} == {"GET"}


def test_monthly_detail_api_is_bounded_sorted_exports_current_fields_and_reads_raw_description(db_session, monkeypatch):
    card=_account(db_session,"card")
    card_id=card.id
    card.display_name="CMB Debit ****1234"
    _event(db_session,"largest",account=card,when=date(2026,7,10),amount="6500",net="6500",event_type="OTHER",primary="HOUSING",secondary="RENT",description="private landlord 123456789")
    _event(db_session,"same-amount-later",account=card,when=date(2026,7,9),amount="90",net="90",description="private food source")
    _event(db_session,"same-amount-earlier",account=card,when=date(2026,7,8),amount="90",net="90",description="private food source")
    _event(db_session,"unclassified",account=card,when=date(2026,7,7),amount="30",net="30",classification="NEEDS_REVIEW",primary=None,secondary=None,description="private unknown")
    _event(db_session,"ineligible",account=card,when=date(2026,7,6),amount="999",net="999",eligibility="INELIGIBLE",classification="NOT_APPLICABLE",primary=None,secondary=None)
    _event(db_session,"review",account=card,when=date(2026,7,5),amount="888",net="888",event_type="OTHER",eligibility="NEEDS_REVIEW",classification="NOT_APPLICABLE",primary=None,secondary=None)
    _event(db_session,"refund",account=card,when=date(2026,7,4),amount="777",net="777",event_type="REFUND",eligibility="ELIGIBLE",classification="CLASSIFIED",primary="DAILY",secondary="FOOD_DINING")
    db_session.commit()
    monkeypatch.setattr(consumption_api,"get_session",lambda: db_session)
    from fastapi import FastAPI
    app=FastAPI(); app.include_router(consumption_api.router,prefix="/api/consumption")

    response=TestClient(app).get("/api/consumption/events?month=2026-07&limit=2")
    assert response.status_code == 200
    body=response.json()
    assert (body["total"],body["limit"],body["offset"]) == (4,2,0)
    assert [item["amount_cny"] for item in body["items"]] == ["6500.00000000","90.00000000"]
    assert [item["analytics_effective_date"] for item in body["items"]] == ["2026-07-10","2026-07-09"]
    assert body["items"][0]["event_id"] == "largest"
    assert body["items"][0]["raw_description"] == "private landlord 123456789"
    assert body["items"][0]["account_display_name"] == "CMB Debit ****"
    assert TestClient(app).get("/api/consumption/events?month=2026-07&limit=201").status_code == 422
    export=TestClient(app).get("/api/consumption/events/export.csv?month=2026-07")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "消费名称" in export.content.decode("utf-8-sig") and "private landlord 123456789" in export.content.decode("utf-8-sig")
    second=ConsumptionAnalyticsService(db_session).monthly_detail(month=date(2026,7,1),limit=2,offset=2,account_ids=(card.id,))
    assert [item.classification_status for item in second.items] == ["CLASSIFIED","NEEDS_REVIEW"]


def test_local_rent_rule_replay_promotes_other_into_housing_analytics_and_detail(db_session):
    debit=_account(db_session,"debit")
    rent=_event(db_session,"rent",account=debit,event_type="OTHER",amount="6500",net="6500",description="synthetic fixed rent transfer")
    property_fee=_event(db_session,"property",account=debit,amount="20",net="20",primary="HOUSING",secondary="PROPERTY_FEE",description="物业费")
    rule=UserClassificationRule(eligibility_action="ELIGIBLE",primary_category="HOUSING",secondary_category="RENT",
        account_id=debit.id,match_text="fixed rent",amount=Decimal("6500"),amount_tolerance=Decimal("0"),effective_from=date(2026,7,1))
    db_session.add(rule); db_session.flush()
    ClassificationResolver().replay(db_session,(rent.id,property_fee.id))
    result=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(debit.id,))
    point=result.months[0]
    assert (point.total_spending_cny,point.housing_cny,point.eligibility_review_count) == (Decimal("6520"),Decimal("6520"),0)
    assert [(item.secondary_category,item.amount_cny) for item in point.secondary_breakdowns] == [("RENT",Decimal("6500")),("PROPERTY_FEE",Decimal("20"))]
    detail=ConsumptionAnalyticsService(db_session).monthly_detail(month=date(2026,7,1),account_ids=(debit.id,))
    assert [(item.raw_description,item.amount_cny) for item in detail.items] == [("synthetic fixed rent transfer",Decimal("6500")),("物业费",Decimal("20"))]


def test_detail_classification_update_uses_user_confirmation_revision_and_updates_analytics(db_session, monkeypatch):
    card=_account(db_session,"card")
    card_id=card.id
    event=_event(db_session,"editable",account=card,amount="100",net="100",primary="DAILY",secondary="FOOD_DINING")
    event_id=event.id
    excluded=_event(db_session,"excluded",account=card,event_type="CREDIT_CARD_REPAYMENT",amount="50",net="50")
    monkeypatch.setattr(consumption_api,"get_session",lambda: db_session)
    from fastapi import FastAPI
    app=FastAPI(); app.include_router(consumption_api.router,prefix="/api/consumption")

    response=TestClient(app).put("/api/consumption/events/editable/classification",json={"primary_category":"TRAVEL","secondary_category":"ACCOMMODATION"})
    assert response.status_code == 200
    assert response.json()["revision_number"] == 2
    current=db_session.query(ConsumptionInterpretation).filter_by(event_id=event_id,is_active=True).one()
    assert (current.user_confirmed,current.primary_category,current.secondary_category,current.revision_number) == (True,"TRAVEL","ACCOMMODATION",2)
    assert db_session.query(ConsumptionInterpretationAudit).filter_by(event_id=event_id).count() == 1
    point=ConsumptionAnalyticsService(db_session).summary(as_of=date(2026,7,31),months=1,account_ids=(card_id,)).months[0]
    assert (point.daily_cny,point.travel_cny) == (Decimal("0"),Decimal("100"))
    assert TestClient(app).put("/api/consumption/events/excluded/classification",json={"primary_category":"DAILY","secondary_category":"FOOD_DINING"}).status_code == 422
