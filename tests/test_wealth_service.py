from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Portfolio, WealthItemSnapshot, WealthSnapshot
from backend.api import wealth as wealth_api
from backend.services import wealth_service


@pytest.fixture()
def wealth_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(Portfolio(id=1, name="测试组合"))
    session.commit()
    session.close()
    monkeypatch.setattr(wealth_service, "get_session", factory)
    monkeypatch.setattr(
        wealth_service.portfolio_service,
        "get_summary",
        lambda _: {
            "total_assets": 1000.0,
            "total_profit_loss": 10.0,
            "allocation": {"equity": {"value": 700.0, "pct": 70.0}, "monetary": {"value": 300.0, "pct": 30.0}},
        },
    )
    return factory


def _create(kind, item_type, value, **extra):
    return wealth_service.create_item(1, {
        "kind": kind, "name": item_type, "item_type": item_type,
        "current_value": value, "value_as_of": date.today().isoformat(), **extra,
    })


def test_wealth_totals_reuse_investment_once_and_exclude_duplicate(wealth_db):
    _create("asset", "housing_fund", 200)
    _create("asset", "personal_pension", 300, already_investment_accounted=True)
    _create("liability", "credit_card", 40)

    summary = wealth_service.get_summary(1)

    assert summary["investment"]["total_assets"] == 700
    assert summary["total_assets"] == 1200
    assert summary["total_liabilities"] == 40
    assert summary["net_worth"] == 1160
    assert {item["label"]: item["value"] for item in summary["asset_breakdown"]} == {
        "投资资产": 700.0, "养老与长期权益": 500.0,
    }


def test_personal_pension_reclassification_preserves_total_assets(wealth_db):
    pension = _create("asset", "personal_pension", 300, already_investment_accounted=True)

    summary = wealth_service.get_summary(1)

    assert summary["investment"]["total_assets"] == 700
    assert summary["total_assets"] == 1000
    assert summary["net_worth"] == 1000
    assert pension["included_in_net_worth"] is True
    assert pension["effective_included_in_net_worth"] is True
    assert {item["label"]: item["value"] for item in summary["asset_breakdown"]} == {
        "投资资产": 700.0, "养老与长期权益": 300.0,
    }


def test_enterprise_annuity_is_supplementary_pension_benefit(wealth_db):
    annuity = _create("asset", "enterprise_annuity", 200)
    _create("asset", "housing_fund", 300)

    summary = wealth_service.get_summary(1)

    assert annuity["effective_included_in_net_worth"] is False
    assert summary["total_assets"] == 1300
    assert summary["net_worth"] == 1300
    assert summary["pension_benefit"] == 200
    assert summary["total_wealth_including_pension_benefit"] == 1500
    assert {item["label"]: item["value"] for item in summary["asset_breakdown"]} == {
        "投资资产": 1000.0, "养老与长期权益": 300.0,
    }


def test_foreign_currency_asset_keeps_source_amount_and_uses_shared_fx(wealth_db, monkeypatch):
    monkeypatch.setattr(wealth_service.fx_service, "convert", lambda amount, *_: (amount * 2, 2.0, "2026-09-01"))
    item = _create("asset", "bank_cash", 0, currency="HKD", original_value=10)

    assert item["currency"] == "HKD"
    assert item["original_value"] == 10
    assert item["current_value"] == 20
    assert item["fx_rate_to_cny"] == 2


def test_manual_update_keeps_item_history_and_old_value_remains_effective(wealth_db):
    item = _create("asset", "bank_cash", 100)
    wealth_service.update_item(1, item["id"], {"current_value": 180, "value_as_of": date.today().isoformat()})
    session = wealth_db()
    try:
        assert session.query(WealthItemSnapshot).filter_by(item_id=item["id"]).count() == 2
        row = session.query(wealth_service.WealthItem).filter_by(id=item["id"]).one()
        row.updated_at = datetime(2025, 1, 1)
        session.commit()
    finally:
        session.close()

    listed = wealth_service.list_items(1, "asset")
    assert listed[0]["current_value"] == 180
    assert listed[0]["freshness"] == "long_unupdated"
    assert wealth_service.get_summary(1)["total_assets"] == 1180


def test_monthly_attribution_is_self_consistent_when_baseline_exists(wealth_db):
    _create("asset", "bank_cash", 100)
    session = wealth_db()
    try:
        previous_month = date.today().replace(day=1).fromordinal(date.today().replace(day=1).toordinal() - 1)
        session.add(WealthSnapshot(
            portfolio_id=1, recorded_at=datetime.combine(previous_month, datetime.min.time()),
            total_assets=1100, total_liabilities=0, net_worth=1100,
            investment_assets=1000, investment_profit_loss=5,
            non_investment_assets=100, pension_benefit_value=0,
        ))
        session.commit()
    finally:
        session.close()

    summary = wealth_service.get_summary(1)
    attribution = summary["attribution"]
    assert summary["monthly_net_worth_change"] == 0
    assert attribution["investment_return"] == 5
    assert attribution["other_adjustment"] == -5
    assert attribution["cash_surplus"] is None
    assert attribution["investment_return"] + attribution["other_adjustment"] == summary["monthly_net_worth_change"]


def test_preview_never_creates_an_asset_fact(wealth_db):
    preview = wealth_service.preview_import("公积金截图.png", b"not-an-image", "image/png")
    assert preview["requires_confirmation"] is True
    assert preview["source_type"] == "SCREENSHOT"
    assert wealth_service.list_items(1) == []


def test_wealth_api_create_and_update(wealth_db, monkeypatch):
    monkeypatch.setattr(wealth_api, "_pid", lambda: 1)
    app = FastAPI()
    app.include_router(wealth_api.router, prefix="/api/wealth")
    client = TestClient(app)

    created = client.post("/api/wealth/items", json={
        "kind": "liability", "name": "信用卡", "item_type": "credit_card", "current_value": 88,
    })
    assert created.status_code == 201
    item_id = created.json()["id"]
    updated = client.patch(f"/api/wealth/items/{item_id}", json={"current_value": 60})
    assert updated.status_code == 200
    assert updated.json()["current_value"] == 60
    summary = client.get("/api/wealth/summary")
    assert summary.status_code == 200
    assert summary.json()["total_liabilities"] == 60
