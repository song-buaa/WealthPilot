from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from backend.services.consumption.adapters.ccb_credit_card_eml import parse_ccb_credit_card_eml
from backend.services.consumption.adapters.cmb_credit_card_pdf import parse_cmb_credit_card_pdf
from backend.services.consumption.adapters.cmb_debit_card_pdf import parse_cmb_debit_card_pdf
from backend.services.consumption.contracts import (
    FieldAvailability,
    NormalizedRawTransaction,
    ParsedStatement,
    StatementMetadata,
    source_file_hash,
)
from backend.services.consumption.import_service import (
    COVERAGE_EXPLICIT,
    COVERAGE_OBSERVED_ONLY,
    DEDUP_AMBIGUOUS,
    DEDUP_CANDIDATE_DUPLICATE,
    DEDUP_UNIQUE,
    ConsumptionImportService,
)
from backend.services.consumption.models import Account, ImportBatch, PaymentInstrument, RawTransaction


FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "consumption"


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


def _account(session, account_type: str = "CREDIT_CARD") -> Account:
    account = Account(
        institution="CMB", account_type=account_type,
        display_name="脱敏测试账户", masked_account_identifier="****1234",
    )
    session.add(account)
    session.flush()
    return account


def _transaction(
    *, identity: str = "row-1", description: str = "Merchant X", amount: str = "100.00",
    transaction_date: date | None = date(2026, 7, 30), posting_date: date | None = date(2026, 7, 31),
) -> NormalizedRawTransaction:
    return NormalizedRawTransaction(
        source_row_index=1,
        source_row_identity=identity,
        transaction_date=transaction_date,
        transaction_date_availability=(
            FieldAvailability.AVAILABLE if transaction_date else FieldAvailability.SOURCE_UNAVAILABLE
        ),
        posting_date=posting_date,
        posting_date_availability=(
            FieldAvailability.AVAILABLE if posting_date else FieldAvailability.SOURCE_UNAVAILABLE
        ),
        amount=Decimal(amount),
        currency="CNY",
        raw_description=description,
        account_masked="****1234",
        instrument_masked="****1234",
        parser_provenance={"adapter": "synthetic"},
    )


def _statement(*, source: bytes, rows: tuple[NormalizedRawTransaction, ...]) -> ParsedStatement:
    return ParsedStatement(
        metadata=StatementMetadata(
            institution="CMB", statement_type="CREDIT_CARD", source_format="PDF",
            parser_version="synthetic-v1", source_file_hash=source_file_hash(source),
        ),
        transactions=rows,
    )


def test_models_create_relationships_decimal_and_nullable_fields(db_session):
    account = _account(db_session)
    instrument = PaymentInstrument(
        account_id=account.id, instrument_type="PHYSICAL_CARD", masked_identifier="****1234"
    )
    db_session.add(instrument)
    db_session.flush()

    parsed = _statement(source=b"decimal-source", rows=(
        NormalizedRawTransaction(
            source_row_index=1, source_row_identity="row-decimal",
            transaction_date=date(2026, 7, 30), transaction_date_availability=FieldAvailability.AVAILABLE,
            posting_date=None, posting_date_availability=FieldAvailability.SOURCE_UNAVAILABLE,
            amount=Decimal("123.45678901"), currency="USD", raw_description="Test merchant",
            account_masked="****1234", instrument_masked="****1234",
            settlement_amount=Decimal("888.12345678"), settlement_currency="CNY",
            balance=Decimal("1000.00000001"), parser_provenance={"adapter": "synthetic"},
        ),
    ))
    result = ConsumptionImportService().persist(
        db_session, account=account, parsed_statement=parsed, payment_instrument=instrument
    )
    db_session.commit()

    row = db_session.query(RawTransaction).one()
    assert row.import_batch.account is account
    assert row.payment_instrument is instrument
    assert row.amount == Decimal("123.45678901")
    assert row.settlement_amount == Decimal("888.12345678")
    assert row.balance == Decimal("1000.00000001")
    assert row.posting_date is None
    assert row.raw_counterparty is None
    assert '"posting_date":"SOURCE_UNAVAILABLE"' in row.source_field_availability
    assert result.import_batch.coverage_status == COVERAGE_OBSERVED_ONLY
    foreign_tables = {
        foreign_key["referred_table"]
        for foreign_key in inspect(db_session.bind).get_foreign_keys("consumption_raw_transactions")
    }
    assert {
        "consumption_import_batches", "consumption_accounts", "consumption_payment_instruments"
    }.issubset(foreign_tables)


def test_adapter_source_coverage_semantics_are_preserved(db_session):
    account = _account(db_session)
    service = ConsumptionImportService()
    cmb_credit = parse_cmb_credit_card_pdf(
        (FIXTURES / "cmb_credit_card" / "input_redacted.txt").read_bytes(),
        text_extractor=lambda value: value.decode("utf-8"),
    )
    ccb_credit = parse_ccb_credit_card_eml(
        (FIXTURES / "ccb_credit_card" / "input_redacted.eml").read_bytes()
    )
    cmb_debit = parse_cmb_debit_card_pdf(
        (FIXTURES / "cmb_debit_card" / "input_redacted.txt").read_bytes(),
        text_extractor=lambda value: value.decode("utf-8"),
    )

    batches = [
        service.persist(db_session, account=account, parsed_statement=parsed).import_batch
        for parsed in (cmb_credit, ccb_credit, cmb_debit)
    ]

    assert batches[0].statement_period_start is None
    assert batches[0].statement_period_end is None
    assert batches[0].statement_period_availability == FieldAvailability.SOURCE_UNAVAILABLE.value
    assert batches[0].coverage_status == COVERAGE_OBSERVED_ONLY
    assert batches[0].observed_transaction_start is not None
    assert batches[0].observed_transaction_end is not None
    assert batches[1].coverage_status == COVERAGE_EXPLICIT
    assert batches[1].statement_period_start is not None
    assert batches[1].statement_period_end is not None
    assert batches[1].statement_period_availability == FieldAvailability.AVAILABLE.value
    assert batches[2].coverage_status == COVERAGE_EXPLICIT
    debit_row = next(row for row in batches[2].raw_transactions if row.posting_date is None)
    assert debit_row.posting_date_availability == FieldAvailability.SOURCE_UNAVAILABLE.value


def test_same_file_is_idempotently_reused_without_duplicate_rows(db_session):
    account = _account(db_session)
    parsed = _statement(source=b"same-source", rows=(_transaction(),))
    service = ConsumptionImportService()

    first = service.persist(db_session, account=account, parsed_statement=parsed)
    second = service.persist(db_session, account=account, parsed_statement=parsed)

    assert first.reused_existing_batch is False
    assert second.reused_existing_batch is True
    assert second.import_batch.id == first.import_batch.id
    assert db_session.query(ImportBatch).count() == 1
    assert db_session.query(RawTransaction).count() == 1


def test_duplicate_source_identity_in_one_batch_is_rejected_before_writing_rows(db_session):
    account = _account(db_session)
    parsed = _statement(source=b"duplicate-row-id", rows=(
        _transaction(identity="same-row"),
        _transaction(identity="same-row", description="Different source text"),
    ))

    with pytest.raises(ValueError, match="duplicate source_row_identity"):
        ConsumptionImportService().persist(db_session, account=account, parsed_statement=parsed)
    db_session.rollback()
    assert db_session.query(ImportBatch).count() == 0
    assert db_session.query(RawTransaction).count() == 0


def test_cross_batch_overlap_is_candidate_only_and_keeps_both_raw_rows(db_session):
    account = _account(db_session)
    service = ConsumptionImportService()
    first = service.persist(
        db_session, account=account,
        parsed_statement=_statement(source=b"statement-a", rows=(_transaction(identity="a-row"),)),
    )
    second = service.persist(
        db_session, account=account,
        parsed_statement=_statement(source=b"statement-b", rows=(_transaction(identity="b-row"),)),
    )

    rows = db_session.query(RawTransaction).order_by(RawTransaction.source_row_identity).all()
    assert first.import_batch.id != second.import_batch.id
    assert len(rows) == 2
    assert {row.dedup_status for row in rows} == {DEDUP_CANDIDATE_DUPLICATE}


def test_identical_same_batch_purchases_remain_ambiguous_and_are_not_dropped(db_session):
    account = _account(db_session)
    parsed = _statement(source=b"two-real-purchases", rows=(
        _transaction(identity="lunch"),
        _transaction(identity="dinner"),
    ))

    ConsumptionImportService().persist(db_session, account=account, parsed_statement=parsed)
    rows = db_session.query(RawTransaction).order_by(RawTransaction.source_row_identity).all()
    assert len(rows) == 2
    assert {row.dedup_status for row in rows} == {DEDUP_AMBIGUOUS}


def test_small_raw_description_difference_is_not_semantically_deduplicated(db_session):
    account = _account(db_session)
    service = ConsumptionImportService()
    service.persist(
        db_session, account=account,
        parsed_statement=_statement(
            source=b"description-a", rows=(_transaction(identity="a", description="支付宝-去哪儿网"),)
        ),
    )
    service.persist(
        db_session, account=account,
        parsed_statement=_statement(
            source=b"description-b", rows=(_transaction(identity="b", description="去哪儿网（天津）国际旅行社有限公司"),)
        ),
    )

    rows = db_session.query(RawTransaction).all()
    assert len(rows) == 2
    assert len({row.match_fingerprint for row in rows}) == 2
    assert {row.dedup_status for row in rows} == {DEDUP_UNIQUE}


def test_database_init_creates_raw_tables_on_empty_existing_and_repeated_sqlite(tmp_path, monkeypatch):
    from app import database

    db_path = tmp_path / "schema-freeze.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    database.init_db()
    database.init_db()
    engine = database.get_engine()
    assert {
        "consumption_accounts", "consumption_payment_instruments",
        "consumption_import_batches", "consumption_raw_transactions",
    }.issubset(set(inspect(engine).get_table_names()))
    engine.dispose()

    legacy_path = tmp_path / "existing-wealthpilot.db"
    legacy_engine = create_engine(f"sqlite:///{legacy_path}")
    with legacy_engine.begin() as connection:
        connection.execute(text("CREATE TABLE portfolios (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
    legacy_engine.dispose()

    monkeypatch.setattr(database, "DB_PATH", str(legacy_path))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    database.init_db()
    database.init_db()
    engine = database.get_engine()
    assert "portfolios" in inspect(engine).get_table_names()
    assert "consumption_raw_transactions" in inspect(engine).get_table_names()
    engine.dispose()
