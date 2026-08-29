"""招商银行借记卡流水 PDF → pure normalized raw-statement contract."""

from __future__ import annotations

import re
from typing import Callable

from backend.services.consumption.adapters.common import (
    extract_masked_identity,
    extract_period,
    normalized_text,
    parse_decimal,
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

PARSER_VERSION = "cmb-debit-card-pdf-spike-v1"
_ROW_RE = re.compile(
    r"^(20\d{2}-\d{1,2}-\d{1,2})\s+([A-Z]{3})\s+([+-]?\d[\d,]*\.\d{2})\s+([+-]?\d[\d,]*\.\d{2})\s+(.+)$"
)


def _extract_pdf_text(source_bytes: bytes) -> str:
    from io import BytesIO
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(source_bytes)).pages)


def parse_cmb_debit_card_pdf(
    source_bytes: bytes,
    source_metadata: dict[str, str] | None = None,
    *,
    text_extractor: Callable[[bytes], str] = _extract_pdf_text,
) -> ParsedStatement:
    del source_metadata
    text = text_extractor(source_bytes)
    period_start, period_end, period_status = extract_period(text)
    identity = extract_masked_identity(text)
    metadata = StatementMetadata(
        institution="CMB", statement_type="DEBIT_CARD", source_format="PDF",
        parser_version=PARSER_VERSION, statement_period_start=period_start, statement_period_end=period_end,
        account_masked=identity, source_file_hash=source_file_hash(source_bytes),
        field_availability={
            "statement_period": period_status,
            "account_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
            "instrument_masked": FieldAvailability.SOURCE_UNAVAILABLE,
        },
    )
    transactions: list[NormalizedRawTransaction] = []
    for line_index, raw_line in enumerate(text.splitlines(), start=1):
        line = normalized_text(raw_line)
        match = _ROW_RE.match(line)
        if not match:
            continue
        transaction_date = parse_full_date(match.group(1))
        transactions.append(NormalizedRawTransaction(
            source_row_index=line_index, source_row_identity=f"pdf-line-{line_index}",
            transaction_date=transaction_date, transaction_date_availability=FieldAvailability.AVAILABLE,
            posting_date=None, posting_date_availability=FieldAvailability.SOURCE_UNAVAILABLE,
            amount=parse_decimal(match.group(3)), currency=match.group(2),
            raw_description=normalized_text(match.group(5)), account_masked=identity, instrument_masked=None,
            balance=parse_decimal(match.group(4)),
            parser_provenance={"adapter": "cmb_debit_card_pdf", "source_row": str(line_index)},
            field_availability={
                "balance": FieldAvailability.AVAILABLE,
                **unavailable_fields("counterparty", "settlement_amount", "settlement_currency", "mcc"),
            },
        ))
    return ParsedStatement(metadata=metadata, transactions=tuple(transactions))
