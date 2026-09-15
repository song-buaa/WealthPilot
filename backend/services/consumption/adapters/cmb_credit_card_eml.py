"""招商银行信用卡 HTML 邮件 → normalized raw-statement contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
import re

from backend.services.consumption.adapters.common import (
    mask_identity,
    normalized_text,
    parse_month_day_with_statement_anchor,
    unavailable_fields,
)
from backend.services.consumption.contracts import (
    FieldAvailability,
    NormalizedRawTransaction,
    ParsedStatement,
    StatementMetadata,
    source_file_hash,
)


PARSER_VERSION = "cmb-credit-card-eml-v1"
_MONTH_DAY = re.compile(r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_MONEY = re.compile(r"^¥\s*([+-]?\d[\d,]*\.\d{2})$")
_SETTLEMENT = re.compile(r"^[+-]?\d[\d,]*\.\d{2}$")
_CARD_TAIL = re.compile(r"^\d{4}$")
_SECTIONS = {
    "还款": "CREDIT_CARD_REPAYMENT",
    "退款": "REFUND",
    "消费": "CONSUMPTION",
    "分期": "INSTALLMENT",
}


def _message_lines(source_bytes: bytes) -> list[str]:
    message = BytesParser(policy=policy.default).parsebytes(source_bytes)
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_type() != "text/html":
            continue
        try:
            html_parts.append(part.get_content())
        except Exception:
            html_parts.append(
                (part.get_payload(decode=True) or b"").decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
            )
    if not html_parts:
        return []
    from bs4 import BeautifulSoup

    return [
        normalized_text(value)
        for value in BeautifulSoup("\n".join(html_parts), "html.parser").get_text("\n", strip=True).splitlines()
        if normalized_text(value)
    ]


def _statement_anchor(lines: list[str]) -> date | None:
    for value in lines:
        match = re.search(r"(20\d{2})/(\d{2})/(\d{2})-(20\d{2})/(\d{2})/(\d{2})", value)
        if match:
            return date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    return None


def _decimal(value: str) -> Decimal:
    match = _MONEY.fullmatch(value)
    if match:
        value = match.group(1)
    return Decimal(value.replace(",", "")).quantize(Decimal("0.01"))


def parse_cmb_credit_card_eml(
    source_bytes: bytes,
    source_metadata: dict[str, str] | None = None,
) -> ParsedStatement:
    """Parse the bank's sequential HTML-detail cells without semantic inference."""
    del source_metadata
    lines = _message_lines(source_bytes)
    anchor = _statement_anchor(lines)
    transactions: list[NormalizedRawTransaction] = []
    section: str | None = None
    source_section: str | None = None
    index = 0
    while index < len(lines):
        value = lines[index]
        if value in _SECTIONS:
            section = _SECTIONS[value]
            source_section = value
            index += 1
            continue
        if section is None or not _MONTH_DAY.fullmatch(value):
            index += 1
            continue

        transaction_token = value
        next_index = index + 1
        posting_token: str | None = None
        if next_index < len(lines) and _MONTH_DAY.fullmatch(lines[next_index]):
            posting_token = lines[next_index]
            next_index += 1
        if next_index + 2 >= len(lines):
            index += 1
            continue
        description, amount_token, card_tail = lines[next_index:next_index + 3]
        if not description or not _MONEY.fullmatch(amount_token) or not _CARD_TAIL.fullmatch(card_tail):
            index += 1
            continue

        next_index += 3
        currency = "CNY"
        if next_index < len(lines) and re.fullmatch(r"[A-Z]{2,3}", lines[next_index]):
            currency = lines[next_index]
            next_index += 1
        amount = _decimal(amount_token)
        settlement_amount = amount
        if next_index < len(lines) and _SETTLEMENT.fullmatch(lines[next_index]):
            settlement_amount = _decimal(lines[next_index])
            next_index += 1
        transaction_date = parse_month_day_with_statement_anchor(
            f"{transaction_token[:2]}/{transaction_token[2:]}", anchor=anchor
        )
        posting_date = parse_month_day_with_statement_anchor(
            f"{posting_token[:2]}/{posting_token[2:]}", anchor=anchor
        ) if posting_token else None
        row_index = len(transactions) + 1
        transactions.append(NormalizedRawTransaction(
            source_row_index=row_index,
            source_row_identity=f"html-sequence-row-{row_index}",
            transaction_date=transaction_date,
            transaction_date_availability=FieldAvailability.AVAILABLE if transaction_date else FieldAvailability.AMBIGUOUS,
            posting_date=posting_date,
            posting_date_availability=FieldAvailability.AVAILABLE if posting_date else FieldAvailability.SOURCE_UNAVAILABLE,
            amount=amount,
            currency=currency,
            raw_description=description,
            account_masked=mask_identity(card_tail),
            instrument_masked=mask_identity(card_tail),
            settlement_amount=settlement_amount,
            settlement_currency="CNY",
            parser_provenance={
                "adapter": "cmb_credit_card_eml",
                "source_row": str(row_index),
                "date_year_resolution": "statement_period_end_year_anchor",
                "statement_section": section,
                "source_section": source_section,
            },
            field_availability={
                **unavailable_fields("balance", "counterparty", "mcc"),
                "settlement_amount": FieldAvailability.AVAILABLE,
                "settlement_currency": FieldAvailability.AVAILABLE,
            },
        ))
        index = next_index

    identity = next((row.instrument_masked for row in transactions if row.instrument_masked), None)
    return ParsedStatement(
        metadata=StatementMetadata(
            institution="CMB",
            statement_type="CREDIT_CARD",
            source_format="EML",
            parser_version=PARSER_VERSION,
            statement_date=anchor,
            account_masked=identity,
            instrument_masked=identity,
            source_file_hash=source_file_hash(source_bytes),
            field_availability={
                "statement_date": FieldAvailability.AVAILABLE if anchor else FieldAvailability.SOURCE_UNAVAILABLE,
                "payment_due_date": FieldAvailability.SOURCE_UNAVAILABLE,
                "statement_period": FieldAvailability.SOURCE_UNAVAILABLE,
                "account_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
                "instrument_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
            },
        ),
        transactions=tuple(transactions),
    )
