"""建设银行信用卡 EML → MIME/HTML/table normalized raw-statement contract."""

from __future__ import annotations

from decimal import Decimal
from email import policy
from email.parser import BytesParser
import re

from backend.services.consumption.adapters.common import (
    extract_period,
    find_money_values,
    mask_identity,
    normalized_text,
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

PARSER_VERSION = "ccb-credit-card-eml-spike-v1"


def _html_tables(html: str) -> list[list[list[str]]]:
    """Extract HTML tables without browser rendering or a network dependency."""
    from bs4 import BeautifulSoup

    tables: list[list[list[str]]] = []
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if cells:
                rows.append([normalized_text(cell.get_text(" ", strip=True)) for cell in cells])
        if rows:
            tables.append(rows)
    return tables


def _message_html(source_bytes: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(source_bytes)
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_type() not in {"text/html", "text/plain"}:
            continue
        try:
            payload = part.get_content()
        except Exception:
            payload = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
        (html_parts if part.get_content_type() == "text/html" else text_parts).append(payload)
    return "\n".join(html_parts) if html_parts else "\n".join(f"<p>{item}</p>" for item in text_parts)


def _header_index(cells: list[str]) -> dict[str, int]:
    aliases = {
        "transaction_date": ("交易日期", "交易日"),
        "posting_date": ("记账日期", "入账日期", "入账日"),
        "description": ("交易描述", "交易摘要", "商户名称", "交易内容"),
        "amount": ("交易金额", "金额"),
        "currency": ("交易币种", "币种"),
        "settlement_amount": ("入账金额", "结算金额", "人民币金额"),
        "settlement_currency": ("入账币种", "结算币种"),
        "instrument": ("卡号", "卡片"),
    }
    result: dict[str, int] = {}
    for index, cell in enumerate(cells):
        for key, names in aliases.items():
            if key not in result and any(name in cell for name in names):
                result[key] = index
    return result


def _cell(cells: list[str], indices: dict[str, int], key: str) -> str | None:
    index = indices.get(key)
    return cells[index] if index is not None and index < len(cells) else None


def _money_from_cell(value: str | None) -> Decimal | None:
    values = find_money_values(value or "")
    return values[-1] if values else None


def _fallback_transaction_cells(cells: list[str]) -> tuple[str, Decimal, str, Decimal | None, str | None] | None:
    """Observed CCB EML rows have two ISO dates followed by source columns.

    The mail's nested table does not expose semantic HTML headers. This fallback is
    deliberately structural: it requires two dates, exact 3-letter currencies and
    decimal amount cells, and preserves only source values.
    """
    if len(cells) < 6 or not parse_full_date(cells[0]) or not parse_full_date(cells[1]):
        return None
    tail = cells[2:]
    currency_cells = [value.upper() for value in tail if re.fullmatch(r"[A-Za-z]{3}", value.strip())]
    amount_cells = [
        _money_from_cell(value)
        for value in tail
        if re.fullmatch(r"[+-]?\d[\d,]*\.\d{2}", value.strip())
    ]
    amounts = [value for value in amount_cells if value is not None]
    description_candidates = [
        value for value in tail
        if value and not re.fullmatch(r"[A-Za-z]{3}", value.strip())
        and not re.fullmatch(r"[+-]?\d[\d,]*\.\d{2}", value.strip())
    ]
    if not currency_cells or not amounts or not description_candidates:
        return None
    return (
        max(description_candidates, key=len),
        amounts[0],
        currency_cells[0],
        amounts[-1] if len(amounts) >= 2 else None,
        currency_cells[-1] if len(currency_cells) >= 2 else None,
    )


def parse_ccb_credit_card_eml(
    source_bytes: bytes,
    source_metadata: dict[str, str] | None = None,
) -> ParsedStatement:
    del source_metadata
    html = _message_html(source_bytes)
    visible_text = normalized_text(re.sub(r"<[^>]+>", " ", html))
    period_start, period_end, period_status = extract_period(visible_text)
    candidates = re.findall(r"(?:卡号|尾号)[：:\s]*([0-9*Xx\- ]{4,})", visible_text)
    if not candidates:
        # CCB's nested HTML table can render the label and masked card value in
        # separate cells. A masked value remains safe to retain as an identity.
        candidates = re.findall(r"(?:\*{2,}|X{2,})\d{4}", visible_text, flags=re.I)
    identity = next((masked for value in candidates if (masked := mask_identity(value))), None)
    metadata = StatementMetadata(
        institution="CCB", statement_type="CREDIT_CARD", source_format="EML", parser_version=PARSER_VERSION,
        statement_period_start=period_start, statement_period_end=period_end,
        account_masked=identity, instrument_masked=identity, source_file_hash=source_file_hash(source_bytes),
        field_availability={
            "statement_period": period_status,
            "account_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
            "instrument_masked": FieldAvailability.AVAILABLE if identity else FieldAvailability.SOURCE_UNAVAILABLE,
        },
    )
    transactions: list[NormalizedRawTransaction] = []
    for table_index, table in enumerate(_html_tables(html), start=1):
        indices = _header_index(table[0])
        for row_offset, cells in enumerate(table[1:], start=1):
            structured = None
            if "description" in indices and "amount" in indices:
                amount = _money_from_cell(_cell(cells, indices, "amount"))
                description = normalized_text(_cell(cells, indices, "description") or "")
                transaction_date = parse_full_date(_cell(cells, indices, "transaction_date") or "")
                posting_date = parse_full_date(_cell(cells, indices, "posting_date") or "")
                currency = normalized_text(_cell(cells, indices, "currency") or "CNY").upper()
                settlement_amount = _money_from_cell(_cell(cells, indices, "settlement_amount"))
                settlement_currency = normalized_text(_cell(cells, indices, "settlement_currency") or "") or None
            else:
                structured = _fallback_transaction_cells(cells)
                if structured is None:
                    continue
                description, amount, currency, settlement_amount, settlement_currency = structured
                transaction_date, posting_date = parse_full_date(cells[0]), parse_full_date(cells[1])
            if amount is None or not description:
                continue
            row_identity = f"html-table-{table_index}-row-{row_offset}"
            instrument = mask_identity(_cell(cells, indices, "instrument")) or identity
            transactions.append(NormalizedRawTransaction(
                source_row_index=len(transactions) + 1, source_row_identity=row_identity,
                transaction_date=transaction_date,
                transaction_date_availability=FieldAvailability.AVAILABLE if transaction_date else FieldAvailability.SOURCE_UNAVAILABLE,
                posting_date=posting_date,
                posting_date_availability=FieldAvailability.AVAILABLE if posting_date else FieldAvailability.SOURCE_UNAVAILABLE,
                amount=amount, currency=currency, raw_description=description,
                account_masked=identity, instrument_masked=instrument,
                settlement_amount=settlement_amount, settlement_currency=settlement_currency,
                parser_provenance={"adapter": "ccb_credit_card_eml", "source_row": row_identity},
                field_availability={
                    **unavailable_fields("balance", "counterparty", "mcc"),
                    "settlement_amount": FieldAvailability.AVAILABLE if settlement_amount is not None else FieldAvailability.SOURCE_UNAVAILABLE,
                    "settlement_currency": FieldAvailability.AVAILABLE if settlement_currency else FieldAvailability.SOURCE_UNAVAILABLE,
                },
            ))
    return ParsedStatement(metadata=metadata, transactions=tuple(transactions))
