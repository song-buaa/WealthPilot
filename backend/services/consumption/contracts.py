"""Pure, deterministic contracts for the consumption statement-adapter spike."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Any


class FieldAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    AMBIGUOUS = "AMBIGUOUS"


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.01")), "f") if value is not None else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def canonical_json(value: Any) -> str:
    """Stable JSON representation used for fixtures and fingerprint experiments."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class StatementMetadata:
    institution: str
    statement_type: str
    source_format: str
    parser_version: str
    statement_period_start: date | None = None
    statement_period_end: date | None = None
    account_masked: str | None = None
    instrument_masked: str | None = None
    source_file_hash: str | None = None
    field_availability: dict[str, FieldAvailability] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution": self.institution, "statement_type": self.statement_type,
            "source_format": self.source_format, "parser_version": self.parser_version,
            "statement_period_start": _date_text(self.statement_period_start),
            "statement_period_end": _date_text(self.statement_period_end),
            "account_masked": self.account_masked, "instrument_masked": self.instrument_masked,
            "source_file_hash": self.source_file_hash,
            "field_availability": {key: value.value for key, value in sorted(self.field_availability.items())},
        }


@dataclass(frozen=True)
class NormalizedRawTransaction:
    """A bank-source fact only; not an economic-event or classification model."""

    source_row_index: int
    source_row_identity: str
    transaction_date: date | None
    transaction_date_availability: FieldAvailability
    posting_date: date | None
    posting_date_availability: FieldAvailability
    amount: Decimal
    currency: str
    raw_description: str
    account_masked: str | None
    instrument_masked: str | None
    balance: Decimal | None = None
    counterparty: str | None = None
    settlement_amount: Decimal | None = None
    settlement_currency: str | None = None
    mcc: str | None = None
    parser_provenance: dict[str, str] = field(default_factory=dict)
    field_availability: dict[str, FieldAvailability] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_row_index": self.source_row_index, "source_row_identity": self.source_row_identity,
            "transaction_date": _date_text(self.transaction_date),
            "transaction_date_availability": self.transaction_date_availability.value,
            "posting_date": _date_text(self.posting_date),
            "posting_date_availability": self.posting_date_availability.value,
            "amount": _decimal_text(self.amount), "currency": self.currency,
            "raw_description": self.raw_description, "account_masked": self.account_masked,
            "instrument_masked": self.instrument_masked, "balance": _decimal_text(self.balance),
            "counterparty": self.counterparty, "settlement_amount": _decimal_text(self.settlement_amount),
            "settlement_currency": self.settlement_currency, "mcc": self.mcc,
            "parser_provenance": dict(sorted(self.parser_provenance.items())),
            "field_availability": {key: value.value for key, value in sorted(self.field_availability.items())},
        }


@dataclass(frozen=True)
class ParsedStatement:
    metadata: StatementMetadata
    transactions: tuple[NormalizedRawTransaction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"metadata": self.metadata.to_dict(), "transactions": [item.to_dict() for item in self.transactions]}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())


def source_file_hash(source_bytes: bytes) -> str:
    return hashlib.sha256(source_bytes).hexdigest()


def raw_row_fingerprint(*, institution: str, transaction: NormalizedRawTransaction) -> str:
    """Fixture-level candidate only, not a production uniqueness constraint."""
    payload = {
        "institution": institution, "account_masked": transaction.account_masked,
        "instrument_masked": transaction.instrument_masked, "transaction_date": _date_text(transaction.transaction_date),
        "posting_date": _date_text(transaction.posting_date), "amount": _decimal_text(transaction.amount),
        "currency": transaction.currency, "raw_description": " ".join(transaction.raw_description.split()),
        "source_row_identity": transaction.source_row_identity,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
