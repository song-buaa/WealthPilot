"""招商银行信用卡 PDF → pure normalized raw-statement contract."""

from __future__ import annotations

import re
from typing import Callable

from backend.services.consumption.adapters.common import (
    mask_identity,
    normalized_text,
    parse_month_day_with_statement_anchor,
    parse_full_date,
    unavailable_fields,
)
from backend.services.consumption.contracts import (
    FieldAvailability,
    NormalizedRawTransaction,
    ParsedStatement,
    StatementMetadata,
    source_file_hash,
)

PARSER_VERSION = "cmb-credit-card-pdf-v2"
# All value columns are anchored from the right.  Merchant descriptions routinely
# contain order numbers, installment counters, and digits such as "7号".
_ROW_RE = re.compile(
    r"^(\d{1,2}/\d{1,2})(?:\s+(\d{1,2}/\d{1,2}))?\s+(.+)\s+"
    r"([+-]?\d[\d,]*\.\d{2})\s+(\d{4}|[A-Z]{3})\s+([+-]?\d[\d,]*\.\d{2})(?:\(([^)]*)\))?$"
)
_SECTION_MAP = {
    "还款": "CREDIT_CARD_REPAYMENT",
    "退款": "REFUND",
    "消费": "CONSUMPTION",
    "分期": "INSTALLMENT",
    "费用": "FEE_INTEREST",
    "利息": "FEE_INTEREST",
}


def _extract_pdf_text(source_bytes: bytes) -> str:
    from io import BytesIO
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(source_bytes)).pages)


def parse_cmb_credit_card_pdf(
    source_bytes: bytes,
    source_metadata: dict[str, str] | None = None,
    *,
    text_extractor: Callable[[bytes], str] = _extract_pdf_text,
) -> ParsedStatement:
    """Parse source facts only; no classification or event inference occurs here."""
    del source_metadata
    text = text_extractor(source_bytes)
    lines = [normalized_text(item) for item in text.splitlines()]
    statement_date = _value_after_label(lines, "账单日") or _inline_date_after_label(lines, "账单日期")
    payment_due_date = _value_after_label(lines, "到期还款日")
    statement_anchor = parse_full_date(statement_date or "")
    default_identity = _inline_masked_identity(lines)
    transactions: list[NormalizedRawTransaction] = []
    statement_section: str | None = None
    source_section_label: str | None = None
    for line_index, line in enumerate(lines, start=1):
        if line in _SECTION_MAP:
            statement_section = _SECTION_MAP[line]
            source_section_label = line
            continue
        match = _ROW_RE.match(line)
        if not match:
            continue
        transaction_date = parse_month_day_with_statement_anchor(match.group(1), anchor=statement_anchor)
        posting_date = parse_month_day_with_statement_anchor(match.group(2), anchor=statement_anchor) if match.group(2) else None
        description = normalized_text(match.group(3))
        card_tail = mask_identity(match.group(5)) if match.group(5).isdigit() else default_identity
        settlement_amount = _money(match.group(6))
        transactions.append(NormalizedRawTransaction(
            source_row_index=line_index, source_row_identity=f"pdf-line-{line_index}",
            transaction_date=transaction_date,
            transaction_date_availability=FieldAvailability.AVAILABLE if transaction_date else FieldAvailability.AMBIGUOUS,
            posting_date=posting_date,
            posting_date_availability=FieldAvailability.AVAILABLE if posting_date else FieldAvailability.SOURCE_UNAVAILABLE,
            amount=_money(match.group(4)), currency="CNY", raw_description=description,
            account_masked=card_tail, instrument_masked=card_tail,
            settlement_amount=settlement_amount,
            settlement_currency="CNY",
            parser_provenance={
                "adapter": "cmb_credit_card_pdf", "source_row": str(line_index),
                "date_year_resolution": "statement_date_year_anchor",
                **({"statement_section": statement_section, "source_section": source_section_label} if statement_section else {}),
            },
            field_availability={
                **unavailable_fields("balance", "counterparty", "mcc"),
                "settlement_amount": FieldAvailability.AVAILABLE,
                "settlement_currency": FieldAvailability.AVAILABLE,
            },
        ))
    identity = next((item.instrument_masked for item in transactions if item.instrument_masked), None)
    metadata = StatementMetadata(
        institution="CMB", statement_type="CREDIT_CARD", source_format="PDF", parser_version=PARSER_VERSION,
        statement_date=statement_anchor, payment_due_date=parse_full_date(payment_due_date or ""),
        account_masked=identity, instrument_masked=identity, source_file_hash=source_file_hash(source_bytes),
        field_availability={
            "statement_date": FieldAvailability.AVAILABLE if statement_anchor else FieldAvailability.SOURCE_UNAVAILABLE,
            "payment_due_date": FieldAvailability.AVAILABLE if payment_due_date else FieldAvailability.SOURCE_UNAVAILABLE,
            "statement_period": FieldAvailability.SOURCE_UNAVAILABLE,
            "account_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
            "instrument_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
        },
    )
    return ParsedStatement(metadata=metadata, transactions=tuple(transactions))


def _value_after_label(lines: list[str], label: str) -> str | None:
    for index, line in enumerate(lines[:-1]):
        if line == label:
            return lines[index + 1]
    return None


def _inline_date_after_label(lines: list[str], label: str) -> str | None:
    for line in lines:
        if label in line and (match := re.search(r"20\d{2}[年/.-]\d{1,2}[月/.-]\d{1,2}", line)):
            return match.group(0)
    return None


def _inline_masked_identity(lines: list[str]) -> str | None:
    for line in lines:
        if "卡号" in line and (match := re.search(r"(?:\*{2,}|X{2,})\d{4}", line, re.I)):
            return mask_identity(match.group(0))
    return None


def _money(value: str):
    from backend.services.consumption.adapters.common import parse_decimal
    return parse_decimal(value)
