"""招商银行信用卡 PDF → pure normalized raw-statement contract."""

from __future__ import annotations

import re
from typing import Callable

from backend.services.consumption.adapters.common import (
    extract_masked_identity,
    extract_labeled_period,
    extract_period,
    find_money_values,
    normalized_text,
    parse_month_day_in_period,
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

PARSER_VERSION = "cmb-credit-card-pdf-spike-v1"
_ROW_RE = re.compile(r"^(\d{1,2}/\d{1,2})\s+(\d{1,2}/\d{1,2})\s+(.+)$")


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
    period_start, period_end, period_status = extract_labeled_period(text)
    statement_anchor, _, _ = extract_period(text)
    identity = extract_masked_identity(text)
    metadata = StatementMetadata(
        institution="CMB", statement_type="CREDIT_CARD", source_format="PDF",
        parser_version=PARSER_VERSION, statement_period_start=period_start, statement_period_end=period_end,
        account_masked=identity, instrument_masked=identity, source_file_hash=source_file_hash(source_bytes),
        field_availability={
            "statement_period": period_status,
            "account_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
            "instrument_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
        },
    )
    transactions: list[NormalizedRawTransaction] = []
    for line_index, raw_line in enumerate(text.splitlines(), start=1):
        line = normalized_text(raw_line)
        match = _ROW_RE.match(line)
        if not match:
            continue
        if period_status is FieldAvailability.AVAILABLE:
            transaction_date = parse_month_day_in_period(match.group(1), period_start=period_start, period_end=period_end)
            posting_date = parse_month_day_in_period(match.group(2), period_start=period_start, period_end=period_end)
            date_provenance = "explicit_statement_period"
        else:
            transaction_date = parse_month_day_with_statement_anchor(match.group(1), anchor=statement_anchor)
            posting_date = parse_month_day_with_statement_anchor(match.group(2), anchor=statement_anchor)
            date_provenance = "statement_date_year_anchor"
        remainder = match.group(3)
        amounts = find_money_values(remainder)
        if not amounts:
            continue
        amount = amounts[-2] if len(amounts) >= 2 else amounts[-1]
        settlement_amount = amounts[-1] if len(amounts) >= 2 else None
        first_amount = re.search(r"[+-]?\d[\d,]*(?:\.\d{1,2})?", remainder)
        description = normalized_text(remainder[: first_amount.start()]) if first_amount else remainder
        if not description:
            continue
        currency_match = re.search(r"\b([A-Z]{3})\b", remainder)
        currency = currency_match.group(1) if currency_match else "CNY"
        transactions.append(NormalizedRawTransaction(
            source_row_index=line_index, source_row_identity=f"pdf-line-{line_index}",
            transaction_date=transaction_date,
            transaction_date_availability=FieldAvailability.AVAILABLE if transaction_date else FieldAvailability.AMBIGUOUS,
            posting_date=posting_date,
            posting_date_availability=FieldAvailability.AVAILABLE if posting_date else FieldAvailability.AMBIGUOUS,
            amount=amount, currency=currency, raw_description=description,
            account_masked=identity, instrument_masked=identity,
            settlement_amount=settlement_amount,
            settlement_currency="CNY" if settlement_amount is not None else None,
            parser_provenance={"adapter": "cmb_credit_card_pdf", "source_row": str(line_index), "date_year_resolution": date_provenance},
            field_availability={
                **unavailable_fields("balance", "counterparty", "mcc"),
                "settlement_amount": FieldAvailability.AVAILABLE if settlement_amount is not None else FieldAvailability.SOURCE_UNAVAILABLE,
                "settlement_currency": FieldAvailability.AVAILABLE if settlement_amount is not None else FieldAvailability.SOURCE_UNAVAILABLE,
            },
        ))
    return ParsedStatement(metadata=metadata, transactions=tuple(transactions))
