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

PARSER_VERSION = "cmb-debit-card-pdf-v2"
_ROW_RE = re.compile(
    r"^(20\d{2}-\d{1,2}-\d{1,2})\s+([A-Z]{3})\s+([+-]?\d[\d,]*\.\d{2})\s+([+-]?\d[\d,]*\.\d{2})\s+(.+)$"
)
_SOURCE_TYPES = tuple(sorted((
    "银联无卡自助消费", "银联快捷支付", "基金快速赎回", "信用卡还款", "银证转账",
    "朝朝宝转入", "朝朝宝转出", "转账汇款", "汇入汇款", "快捷支付", "代发款项",
    "集中代收", "退款", "手续费",
), key=len, reverse=True))


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
    lines = [normalized_text(raw_line) for raw_line in text.splitlines()]
    transactions: list[NormalizedRawTransaction] = []
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        match = _ROW_RE.match(line)
        if not match:
            line_index += 1
            continue
        continuations: list[str] = []
        next_index = line_index + 1
        while next_index < len(lines) and not _ROW_RE.match(lines[next_index]):
            if _is_continuation(lines[next_index]):
                continuations.append(lines[next_index])
            next_index += 1
        description = normalized_text(" ".join((match.group(5), *continuations)))
        source_type, counterparty = _source_fields(description)
        transaction_date = parse_full_date(match.group(1))
        transactions.append(NormalizedRawTransaction(
            source_row_index=line_index + 1, source_row_identity=f"pdf-line-{line_index + 1}",
            transaction_date=transaction_date, transaction_date_availability=FieldAvailability.AVAILABLE,
            posting_date=None, posting_date_availability=FieldAvailability.SOURCE_UNAVAILABLE,
            amount=parse_decimal(match.group(3)), currency=match.group(2), raw_description=description,
            account_masked=identity, instrument_masked=None, counterparty=counterparty,
            balance=parse_decimal(match.group(4)),
            parser_provenance={
                "adapter": "cmb_debit_card_pdf", "source_row": str(line_index + 1),
                **({"source_transaction_type": source_type} if source_type else {}),
                **({"continuation_line_count": str(len(continuations))} if continuations else {}),
            },
            field_availability={
                "balance": FieldAvailability.AVAILABLE,
                **unavailable_fields("counterparty", "settlement_amount", "settlement_currency", "mcc"),
            },
        ))
        line_index = next_index
    return ParsedStatement(metadata=metadata, transactions=tuple(transactions))


def _is_continuation(line: str) -> bool:
    if not line or re.fullmatch(r"\d+/\d+", line) or "————————————————" in line:
        return False
    headers = (
        "记账日期", "Date Currency", "Amount Balance", "招商银行交易流水", "Transaction Statement",
        "温馨提示", "账户类型", "申请时间", "账号：", "开户行", "验证码", "户 名", "Name",
        "Account ", "Sub Branch", "Verification", "特别提醒", "此流水",
    )
    return not any(line.startswith(header) for header in headers)


def _source_fields(description: str) -> tuple[str | None, str | None]:
    for source_type in _SOURCE_TYPES:
        if description.startswith(source_type):
            remainder = normalized_text(description[len(source_type):]) or None
            return source_type, remainder
    return None, None
