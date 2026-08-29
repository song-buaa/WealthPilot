"""Persistence Golden Cases Q–V for deterministic EconomicEvent normalization."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from backend.services.consumption.economic_events import EventType, FxSource, ResolutionStatus
from backend.services.consumption.models import (
    Account,
    EconomicEvent,
    EconomicEventProjectionRevision,
    EventRawLink,
    ImportBatch,
    RawTransaction,
)
from backend.services.consumption.normalization import EconomicEventNormalizer
from backend.services.consumption.normalization.rules import classify_source


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _account(session, account_id: str, account_type: str = "CREDIT_CARD", ownership: str = "UNCONFIRMED"):
    account = Account(
        id=account_id, institution="SYNTHETIC", account_type=account_type,
        masked_account_identifier=f"****{account_id[-4:]}", ownership_status=ownership,
    )
    session.add(account)
    session.flush()
    return account


def _raw(session, account: Account, raw_id: str, description: str, amount: str, *,
         day: date = date(2026, 7, 1), currency: str = "CNY", settlement: str | None = None,
         dedup_status: str = "UNIQUE", match_fingerprint: str | None = None) -> RawTransaction:
    batch = ImportBatch(
        id=f"batch-{raw_id}", account_id=account.id, source_format="TEST", institution="SYNTHETIC",
        statement_type=account.account_type, source_file_hash=(raw_id * 64)[:64], parser_version="test",
        statement_period_availability="SOURCE_UNAVAILABLE", coverage_status="OBSERVED_ONLY", row_count=1,
    )
    session.add(batch)
    raw = RawTransaction(
        id=raw_id, import_batch_id=batch.id, account_id=account.id, source_row_index=1,
        source_row_identity=f"row-{raw_id}", source_row_fingerprint_candidate=f"source-{raw_id}",
        match_fingerprint=match_fingerprint or f"match-{raw_id}", dedup_status=dedup_status,
        transaction_date=day, transaction_date_availability="AVAILABLE", posting_date=None,
        posting_date_availability="SOURCE_UNAVAILABLE", amount=Decimal(amount), currency=currency,
        settlement_amount=Decimal(settlement) if settlement is not None else None,
        settlement_currency="CNY" if settlement is not None else None, raw_description=description,
        parser_provenance="{}", source_field_availability="{}",
    )
    session.add(raw)
    session.flush()
    return raw


def _event(session, event_type: EventType, raw_id: str) -> EconomicEvent:
    return session.query(EconomicEvent).join(EventRawLink).filter(
        EconomicEvent.event_type == event_type.value, EventRawLink.raw_transaction_id == raw_id,
    ).one()


def test_q_multiple_partial_refunds_create_append_only_projection_revisions(db_session):
    account = _account(db_session, "card", ownership="CONFIRMED_OWNED")
    _raw(db_session, account, "purchase", "[CONSUMPTION] purchase", "-5000")
    _raw(db_session, account, "refund-one", "[REFUND] REF:purchase first", "1000", day=date(2026, 8, 1))
    _raw(db_session, account, "refund-two", "[REFUND] REF:purchase second", "1500", day=date(2026, 8, 2))

    result = EconomicEventNormalizer().normalize(db_session)
    purchase = _event(db_session, EventType.CONSUMPTION, "purchase")
    refunds = db_session.query(EconomicEvent).filter_by(original_event_id=purchase.id).all()
    revisions = db_session.query(EconomicEventProjectionRevision).filter_by(event_id=purchase.id).order_by(EconomicEventProjectionRevision.revision_number).all()

    assert result.created_events == 3
    assert len(refunds) == 2
    assert [(item.revision_number, item.is_active, Decimal(item.net_amount)) for item in revisions] == [
        (1, False, Decimal("5000")), (2, False, Decimal("4000")), (3, True, Decimal("2500")),
    ]
    assert revisions[-1].supersedes_revision_id == revisions[-2].id
    assert _event(db_session, EventType.REFUND, "refund-one").analytics_effective_date == date(2026, 7, 1)


def test_r_normalizer_rerun_is_idempotent(db_session):
    account = _account(db_session, "card")
    _raw(db_session, account, "one", "[CONSUMPTION] repeat-safe", "-100")
    normalizer = EconomicEventNormalizer()
    normalizer.normalize(db_session)
    second = normalizer.normalize(db_session)

    assert second.created_events == second.created_links == second.created_revisions == 0
    assert db_session.query(EconomicEvent).count() == 1
    assert db_session.query(EventRawLink).count() == 1


def test_s_and_t_ownership_blocks_then_allows_exact_internal_pair(db_session):
    left = _account(db_session, "left", account_type="DEBIT_CARD")
    right = _account(db_session, "right", account_type="DEBIT_CARD")
    _raw(db_session, left, "left-raw", "[INTERNAL] own transfer", "-50")
    _raw(db_session, right, "right-raw", "[INTERNAL] own transfer", "50", day=date(2026, 7, 2))
    EconomicEventNormalizer().normalize(db_session)
    assert {item.event_type for item in db_session.query(EconomicEvent).all()} == {EventType.OTHER.value}

    db_session.query(EventRawLink).update({EventRawLink.is_active: False})
    db_session.query(EconomicEvent).delete()
    left.ownership_status = right.ownership_status = "CONFIRMED_OWNED"
    EconomicEventNormalizer().normalize(db_session)
    transfer = db_session.query(EconomicEvent).one()
    assert transfer.event_type == EventType.INTERNAL_TRANSFER.value
    assert len(transfer.raw_links) == 2


def test_u_multiple_internal_candidates_are_ambiguous_not_auto_paired(db_session):
    left = _account(db_session, "left", account_type="DEBIT_CARD", ownership="CONFIRMED_OWNED")
    right = _account(db_session, "right", account_type="DEBIT_CARD", ownership="CONFIRMED_OWNED")
    third = _account(db_session, "third", account_type="DEBIT_CARD", ownership="CONFIRMED_OWNED")
    _raw(db_session, left, "out", "[INTERNAL] own transfer", "-50")
    _raw(db_session, right, "in-one", "[INTERNAL] own transfer", "50")
    _raw(db_session, third, "in-two", "[INTERNAL] own transfer", "50")
    EconomicEventNormalizer().normalize(db_session)
    events = db_session.query(EconomicEvent).all()
    assert {event.event_type for event in events} == {EventType.OTHER.value}
    assert {event.resolution_reason for event in events} == {"INTERNAL_TRANSFER_AMBIGUOUS"}


@pytest.mark.parametrize(("description", "account_type", "expected"), [
    ("信用卡自动还款", "DEBIT_CARD", EventType.CREDIT_CARD_REPAYMENT),
    ("朝朝宝转出", "DEBIT_CARD", EventType.LIQUIDITY_SWEEP),
    ("基金快速赎回", "DEBIT_CARD", EventType.INVESTMENT_TRANSFER),
    ("住房公积金管理中心代发", "DEBIT_CARD", EventType.INCOME),
    ("个贷放款", "DEBIT_CARD", EventType.LOAN_DISBURSEMENT),
    ("贷款本金偿还", "DEBIT_CARD", EventType.DEBT_REPAYMENT),
    ("贷款利息", "DEBIT_CARD", EventType.FEE_INTEREST),
    ("活动现金红包", "DEBIT_CARD", EventType.REBATE),
    ("普通个人转账", "DEBIT_CARD", EventType.OTHER),
])
def test_v_high_confidence_production_rules(description, account_type, expected):
    assert classify_source(raw_description=description, account_type=account_type).event_type == expected


def test_event_orm_decimal_fx_nullable_and_schema_relationships(db_session):
    account = _account(db_session, "card")
    _raw(db_session, account, "usd", "[CONSUMPTION] foreign", "-100", currency="USD", settlement="-720")
    _raw(db_session, account, "eur", "[CONSUMPTION] no settlement", "-10", currency="EUR")
    EconomicEventNormalizer().normalize(db_session)
    usd, eur = (_event(db_session, EventType.CONSUMPTION, raw_id) for raw_id in ("usd", "eur"))
    assert (Decimal(usd.base_amount), Decimal(usd.fx_rate), usd.fx_source) == (Decimal("720"), Decimal("7.2"), FxSource.BANK_SETTLEMENT.value)
    assert (eur.base_amount, eur.fx_rate, eur.fx_source, eur.resolution_status) == (None, None, FxSource.FX_REQUIRED.value, ResolutionStatus.NEEDS_REVIEW.value)
    assert {"consumption_economic_events", "consumption_event_raw_links", "consumption_event_projection_revisions"}.issubset(inspect(db_session.bind).get_table_names())


def test_existing_consumption_account_gets_idempotent_ownership_evolution(tmp_path, monkeypatch):
    from app import database

    path = tmp_path / "existing-consumption.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE consumption_accounts (
                id VARCHAR(36) PRIMARY KEY, institution VARCHAR(30) NOT NULL,
                account_type VARCHAR(30) NOT NULL, display_name VARCHAR(120),
                masked_account_identifier VARCHAR(64), base_currency VARCHAR(3) NOT NULL,
                status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            )
        """))
    engine.dispose()
    monkeypatch.setattr(database, "DB_PATH", str(path))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    database.init_db()
    database.init_db()
    columns = {item["name"] for item in inspect(database.get_engine()).get_columns("consumption_accounts")}
    assert "ownership_status" in columns
    database.get_engine().dispose()
