from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from backend.scripts.import_consumption_statements import (
    _archive_sources,
    _backup_database,
    main as cli_main,
)
from backend.services.consumption.adapters.ccb_credit_card_eml import parse_ccb_credit_card_eml
from backend.services.consumption.adapters.cmb_credit_card_pdf import parse_cmb_credit_card_pdf
from backend.services.consumption.adapters.cmb_debit_card_pdf import parse_cmb_debit_card_pdf
from backend.services.consumption.import_runner import (
    BootstrapError,
    BootstrapSource,
    SourceKind,
    bootstrap_sources,
)
from backend.services.consumption.models import (
    Account,
    ConsumptionInterpretation,
    EconomicEvent,
    ImportBatch,
    PaymentInstrument,
    RawTransaction,
)


FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "consumption"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE portfolios (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        connection.execute(text("INSERT INTO portfolios (id, name) VALUES (1, 'existing')"))
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _fixture_sources() -> tuple[BootstrapSource, ...]:
    cmb_credit = (FIXTURES / "cmb_credit_card" / "input_redacted.txt").read_bytes()
    ccb_credit = (FIXTURES / "ccb_credit_card" / "input_redacted.eml").read_bytes()
    cmb_debit = (FIXTURES / "cmb_debit_card" / "input_redacted.txt").read_bytes()
    return (
        BootstrapSource(
            SourceKind.CMB_CREDIT,
            cmb_credit,
            "cmb-credit-redacted.pdf",
            parser=lambda value: parse_cmb_credit_card_pdf(
                value, text_extractor=lambda text: text.decode("utf-8")
            ),
        ),
        BootstrapSource(SourceKind.CCB_CREDIT, ccb_credit, "ccb-credit-redacted.eml"),
        BootstrapSource(
            SourceKind.CMB_DEBIT,
            cmb_debit,
            "cmb-debit-redacted.pdf",
            parser=lambda value: parse_cmb_debit_card_pdf(
                value, text_extractor=lambda text: text.decode("utf-8")
            ),
        ),
    )


def test_three_source_runner_reuses_production_pipeline_and_is_idempotent(db_session):
    first = bootstrap_sources(db_session, _fixture_sources(), as_of=date(2026, 7, 31))
    assert (first.source_statement_count, first.new_batch_count, first.reused_batch_count) == (3, 3, 0)
    assert first.inserted_raw_row_count == first.parsed_row_count
    assert first.analytics_non_empty is True
    assert first.invariant_passed is True
    assert db_session.query(Account).count() == 3
    assert db_session.query(PaymentInstrument).count() == 2
    assert db_session.query(RawTransaction).filter_by(payment_instrument_id=None).count() > 0
    assert db_session.execute(text("SELECT name FROM portfolios WHERE id = 1")).scalar_one() == "existing"

    stable_counts = (
        db_session.query(ImportBatch).count(),
        db_session.query(RawTransaction).count(),
        db_session.query(EconomicEvent).filter_by(is_active=True).count(),
        db_session.query(ConsumptionInterpretation).filter_by(is_active=True).count(),
    )
    second = bootstrap_sources(db_session, _fixture_sources(), as_of=date(2026, 7, 31))
    assert (second.new_batch_count, second.reused_batch_count) == (0, 3)
    assert second.inserted_raw_row_count == 0
    assert second.reused_raw_row_count == first.parsed_row_count
    assert stable_counts == (
        db_session.query(ImportBatch).count(),
        db_session.query(RawTransaction).count(),
        db_session.query(EconomicEvent).filter_by(is_active=True).count(),
        db_session.query(ConsumptionInterpretation).filter_by(is_active=True).count(),
    )


def test_overlapping_statement_import_collapses_cross_batch_duplicates_before_analytics(db_session):
    source = (FIXTURES / "cmb_credit_card" / "input_redacted.txt").read_bytes()

    def parser(value: bytes):
        return parse_cmb_credit_card_pdf(value, text_extractor=lambda text: text.decode("utf-8"))

    first = BootstrapSource(SourceKind.CMB_CREDIT, source, "first.pdf", parser=parser)
    second = BootstrapSource(SourceKind.CMB_CREDIT, source + b"\n", "overlap.pdf", parser=parser)
    bootstrap_sources(db_session, (first,), as_of=date(2026, 7, 31))
    events_before = db_session.query(EconomicEvent).filter_by(is_active=True).count()

    result = bootstrap_sources(db_session, (second,), as_of=date(2026, 7, 31))

    assert result.new_batch_count == 1
    assert db_session.query(RawTransaction).count() == 4
    assert db_session.query(RawTransaction).filter_by(is_active=True).count() == 2
    assert db_session.query(EconomicEvent).filter_by(is_active=True).count() == events_before


def test_parse_failure_happens_before_any_consumption_write(db_session):
    broken = BootstrapSource(
        SourceKind.CMB_CREDIT,
        b"not-a-statement",
        "broken.pdf",
        parser=lambda _: (_ for _ in ()).throw(RuntimeError("sensitive source detail")),
    )
    with pytest.raises(BootstrapError, match="CMB Credit parser failed"):
        bootstrap_sources(db_session, (*_fixture_sources()[:1], broken), as_of=date(2026, 7, 31))
    assert db_session.query(ImportBatch).count() == 0
    assert db_session.query(RawTransaction).count() == 0


def test_backup_uses_consistent_sqlite_copy_without_mutating_source(tmp_path):
    source = tmp_path / "wealthpilot.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker (value) VALUES ('before-import')")
        connection.commit()
    finally:
        connection.close()
    backup = _backup_database(source)
    assert backup is not None and backup.is_file()
    with sqlite3.connect(source) as original, sqlite3.connect(backup) as copied:
        assert original.execute("SELECT value FROM marker").fetchone() == ("before-import",)
        assert copied.execute("SELECT value FROM marker").fetchone() == ("before-import",)


def test_cli_dry_run_prints_safe_summary_without_fixture_transaction_text(capsys):
    fixture = FIXTURES / "ccb_credit_card" / "input_redacted.eml"
    assert cli_main(["--ccb-credit", str(fixture), "--dry-run"]) == 0
    captured = capsys.readouterr()
    raw_fixture_text = fixture.read_text(encoding="utf-8")
    assert "Target DB:" in captured.out
    assert "Dry run:" in captured.out
    assert fixture.name in captured.out
    assert "<table" not in captured.out
    assert raw_fixture_text not in captured.out


def test_explicit_archive_or_nested_directory_routes_eml_to_the_existing_ccb_adapter(tmp_path):
    archive = tmp_path / "statements.zip"
    fixture = FIXTURES / "ccb_credit_card" / "input_redacted.eml"
    with ZipFile(archive, "w", ZIP_DEFLATED) as value:
        value.writestr("source.eml", fixture.read_bytes())
    sources = _archive_sources([archive])
    assert len(sources) == 1
    assert sources[0].kind == SourceKind.CCB_CREDIT
    assert sources[0].source_name == archive.name

    nested = tmp_path / "source-directory" / "nested"
    nested.mkdir(parents=True)
    (nested / fixture.name).write_bytes(fixture.read_bytes())
    sources = _archive_sources([nested.parent])
    assert len(sources) == 1
    assert sources[0].kind == SourceKind.CCB_CREDIT
