from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.consumption.adapters.ccb_credit_card_eml import parse_ccb_credit_card_eml
from backend.services.consumption.adapters.cmb_credit_card_pdf import parse_cmb_credit_card_pdf
from backend.services.consumption.adapters.cmb_debit_card_pdf import parse_cmb_debit_card_pdf
from backend.services.consumption.adapters.common import parse_month_day_in_period, parse_month_day_with_statement_anchor
from backend.services.consumption.contracts import raw_row_fingerprint, source_file_hash
from datetime import date


FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "consumption"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _statement_expected(source: bytes, folder: Path) -> dict:
    expected = _json(folder / "expected_statement.json")
    expected["source_file_hash"] = source_file_hash(source)
    return expected


@pytest.mark.parametrize(
    ("folder_name", "parser", "input_name", "kwargs"),
    [
        ("cmb_credit_card", parse_cmb_credit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
        ("ccb_credit_card", parse_ccb_credit_card_eml, "input_redacted.eml", {}),
        ("cmb_debit_card", parse_cmb_debit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
    ],
)
def test_redacted_fixture_matches_golden_contract(folder_name, parser, input_name, kwargs):
    folder = FIXTURES / folder_name
    source = (folder / input_name).read_bytes()
    parsed = parser(source, **kwargs)

    assert parsed.metadata.to_dict() == _statement_expected(source, folder)
    assert [item.to_dict() for item in parsed.transactions] == _json(folder / "expected_transactions.json")
    assert parsed.transactions


@pytest.mark.parametrize(
    ("folder_name", "parser", "input_name", "kwargs"),
    [
        ("cmb_credit_card", parse_cmb_credit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
        ("ccb_credit_card", parse_ccb_credit_card_eml, "input_redacted.eml", {}),
        ("cmb_debit_card", parse_cmb_debit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
    ],
)
def test_adapter_output_is_canonical_and_deterministic(folder_name, parser, input_name, kwargs):
    source = (FIXTURES / folder_name / input_name).read_bytes()
    first = parser(source, **kwargs)
    second = parser(source, **kwargs)

    assert first.canonical_json() == second.canonical_json()
    assert source_file_hash(source) == source_file_hash(source)


@pytest.mark.parametrize(
    ("folder_name", "parser", "input_name", "kwargs"),
    [
        ("cmb_credit_card", parse_cmb_credit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
        ("ccb_credit_card", parse_ccb_credit_card_eml, "input_redacted.eml", {}),
        ("cmb_debit_card", parse_cmb_debit_card_pdf, "input_redacted.txt", {"text_extractor": lambda value: value.decode("utf-8")}),
    ],
)
def test_field_availability_fixture_is_observed_not_inferred(folder_name, parser, input_name, kwargs):
    folder = FIXTURES / folder_name
    parsed = parser((folder / input_name).read_bytes(), **kwargs)
    expected = _json(folder / "field_availability.json")
    first = parsed.transactions[0]
    observed = {
        "transaction_date": first.transaction_date_availability.value,
        "posting_date": first.posting_date_availability.value,
        "amount": "AVAILABLE",
        "currency": "AVAILABLE",
        "raw_description": "AVAILABLE",
        **{key: value.value for key, value in first.field_availability.items()},
    }
    assert observed == expected


def test_row_fingerprint_is_stable_and_distinguishes_representative_rows():
    source = (FIXTURES / "cmb_credit_card" / "input_redacted.txt").read_bytes()
    parsed = parse_cmb_credit_card_pdf(source, text_extractor=lambda value: value.decode("utf-8"))
    first, second = parsed.transactions
    assert raw_row_fingerprint(institution="CMB", transaction=first) == raw_row_fingerprint(institution="CMB", transaction=first)
    assert raw_row_fingerprint(institution="CMB", transaction=first) != raw_row_fingerprint(institution="CMB", transaction=second)


def test_credit_card_month_day_uses_the_proven_statement_period_year():
    assert parse_month_day_in_period("12/20", period_start=date(2025, 12, 5), period_end=date(2026, 1, 4)) == date(2025, 12, 20)
    assert parse_month_day_in_period("01/02", period_start=date(2025, 12, 5), period_end=date(2026, 1, 4)) == date(2026, 1, 2)


def test_credit_card_month_day_falls_back_to_statement_anchor_without_claiming_a_period():
    assert parse_month_day_with_statement_anchor("12/20", anchor=date(2026, 1, 12)) == date(2025, 12, 20)
    assert parse_month_day_with_statement_anchor("01/02", anchor=date(2026, 1, 12)) == date(2026, 1, 2)


def test_committed_consumption_fixtures_do_not_contain_sensitive_source_extensions():
    fixture_paths = list(FIXTURES.rglob("*"))
    assert not any(path.suffix.lower() == ".pdf" for path in fixture_paths)
    assert all("真实" not in path.name for path in fixture_paths)
