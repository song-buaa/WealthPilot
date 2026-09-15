from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.consumption.adapters.ccb_credit_card_eml import parse_ccb_credit_card_eml
from backend.services.consumption.adapters.cmb_credit_card_pdf import parse_cmb_credit_card_pdf
from backend.services.consumption.adapters.cmb_credit_card_eml import parse_cmb_credit_card_eml
from backend.services.consumption.adapters.cmb_debit_card_pdf import parse_cmb_debit_card_pdf
from backend.services.consumption.adapters.common import parse_month_day_in_period, parse_month_day_with_statement_anchor
from backend.services.consumption.contracts import raw_row_fingerprint, source_file_hash
from backend.services.consumption.source_adapter_golden import assert_source_adapter_golden
from datetime import date
from decimal import Decimal


FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "consumption"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


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

    assert_source_adapter_golden(
        parsed,
        source_bytes=source,
        expected_statement=_json(folder / "expected_statement.json"),
        expected_transactions=_json(folder / "expected_transactions.json"),
    )


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


def test_cmb_credit_parser_uses_rmb_amount_and_preserves_statement_section():
    source = """账单日期：2026-06-12
卡号：****1234
退款
05/23 05/24 支付宝-测试商户 -3684.10 4964 -3684.10(CN)
消费
05/23 05/24 支付宝-测试商户 3684.10 4964 3684.10(CN)
""".encode("utf-8")
    parsed = parse_cmb_credit_card_pdf(source, text_extractor=lambda value: value.decode("utf-8"))

    assert [(row.amount, row.parser_provenance.get("statement_section")) for row in parsed.transactions] == [
        (Decimal("-3684.10"), "REFUND"),
        (Decimal("3684.10"), "CONSUMPTION"),
    ]


def test_cmb_credit_parser_anchors_values_at_the_tail_and_keeps_single_date_repayment():
    source = """账单日
2026年07月12日
到期还款日
2026年08月01日
还款
06/01 银联在线网络还款 -11,111.59 4964 -11,111.59
消费
06/27 06/28 财付通-1006195湖南常德牛肉粉 26.50 4964 26.50(CN)
07/01 07/02 财付通-7号饺子 35.00 4964 35.00(CN)
分期
07/02 07/02 分期还款 本金 第1/12期 1,011.12 4964 1,011.12
""".encode("utf-8")
    parsed = parse_cmb_credit_card_pdf(source, text_extractor=lambda value: value.decode("utf-8"))

    assert [(row.raw_description, row.amount, row.instrument_masked) for row in parsed.transactions] == [
        ("银联在线网络还款", Decimal("-11111.59"), "****4964"),
        ("财付通-1006195湖南常德牛肉粉", Decimal("26.50"), "****4964"),
        ("财付通-7号饺子", Decimal("35.00"), "****4964"),
        ("分期还款 本金 第1/12期", Decimal("1011.12"), "****4964"),
    ]
    assert parsed.transactions[0].posting_date is None
    assert parsed.metadata.account_masked == "****4964"
    assert (parsed.metadata.statement_date, parsed.metadata.payment_due_date) == (date(2026, 7, 12), date(2026, 8, 1))


def test_cmb_credit_email_parser_reads_html_statement_rows_and_sections():
    source = b"""From: statement@example.test
Content-Type: text/html; charset=utf-8

<html><body>2026/08/13-2026/09/12<br/>
\xe8\xbf\x98\xe6\xac\xbe<br/>0901<br/>\xe6\x89\x8b\xe6\x9c\xba\xe9\x93\xb6\xe8\xa1\x8c\xe8\xbf\x98\xe6\xac\xbe<br/>\xc2\xa5 -100.00<br/>4964<br/>-100.00<br/>
\xe9\x80\x80\xe6\xac\xbe<br/>0825<br/>0826<br/>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x95\x86\xe6\x88\xb7<br/>\xc2\xa5 -20.00<br/>4964<br/>CN<br/>-20.00<br/>
\xe6\xb6\x88\xe8\xb4\xb9<br/>0826<br/>0827<br/>\xe6\xb5\x8b\xe8\xaf\x95\xe6\xb6\x88\xe8\xb4\xb9<br/>\xc2\xa5 20.00<br/>4964<br/>CN<br/>20.00</body></html>"""
    parsed = parse_cmb_credit_card_eml(source)

    assert [(row.transaction_date, row.posting_date, row.amount, row.parser_provenance["statement_section"]) for row in parsed.transactions] == [
        (date(2026, 9, 1), None, Decimal("-100.00"), "CREDIT_CARD_REPAYMENT"),
        (date(2026, 8, 25), date(2026, 8, 26), Decimal("-20.00"), "REFUND"),
        (date(2026, 8, 26), date(2026, 8, 27), Decimal("20.00"), "CONSUMPTION"),
    ]
    assert parsed.metadata.source_format == "EML"
    assert parsed.metadata.account_masked == "****4964"


def test_cmb_debit_parser_merges_continuation_and_preserves_source_type():
    source = """2026-08-20 CNY 100.00 100.00 朝朝宝转出 待清算电子汇差-代销理财快赎
投资
2026-08-20 CNY -100.00 0.00 快捷支付 测试商户
""".encode("utf-8")
    parsed = parse_cmb_debit_card_pdf(source, text_extractor=lambda value: value.decode("utf-8"))

    first = parsed.transactions[0]
    assert (first.raw_description, first.counterparty, first.parser_provenance["source_transaction_type"]) == (
        "朝朝宝转出 待清算电子汇差-代销理财快赎 投资", "待清算电子汇差-代销理财快赎 投资", "朝朝宝转出",
    )


def test_ccb_credit_parser_discards_only_an_exact_mirrored_transaction_table():
    source = b"""From: statement@example.test
Content-Type: text/html; charset=utf-8

<html><body>
<table><tr><td>\xe3\x80\x90\xe4\xba\xa4\xe6\x98\x93\xe6\x98\x8e\xe7\xbb\x86\xe3\x80\x91</td></tr>
<tr><td>2026-05-16</td><td>2026-05-17</td><td>1234</td><td>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x95\x86\xe6\x88\xb7</td><td>CNY</td><td>2.80</td><td>CNY</td><td>2.80</td></tr>
<tr><td>2026-05-16</td><td>2026-05-17</td><td>5678</td><td>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x95\x86\xe6\x88\xb7</td><td>CNY</td><td>2.80</td><td>CNY</td><td>2.80</td></tr></table>
<table><tr><td>\xe3\x80\x90\xe4\xba\xa4\xe6\x98\x93\xe6\x98\x8e\xe7\xbb\x86\xe3\x80\x91</td></tr>
<tr><td>2026-05-16</td><td>2026-05-17</td><td>1234</td><td>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x95\x86\xe6\x88\xb7</td><td>CNY</td><td>2.80</td><td>CNY</td><td>2.80</td></tr>
<tr><td>2026-05-16</td><td>2026-05-17</td><td>5678</td><td>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x95\x86\xe6\x88\xb7</td><td>CNY</td><td>2.80</td><td>CNY</td><td>2.80</td></tr></table>
</body></html>"""
    parsed = parse_ccb_credit_card_eml(source)

    assert len(parsed.transactions) == 2
    assert [row.source_row_identity for row in parsed.transactions] == [
        "html-table-1-row-1", "html-table-1-row-2",
    ]
    assert [row.instrument_masked for row in parsed.transactions] == ["****1234", "****5678"]


def test_committed_consumption_fixtures_do_not_contain_sensitive_source_extensions():
    fixture_paths = list(FIXTURES.rglob("*"))
    assert not any(path.suffix.lower() == ".pdf" for path in fixture_paths)
    assert all("真实" not in path.name for path in fixture_paths)
