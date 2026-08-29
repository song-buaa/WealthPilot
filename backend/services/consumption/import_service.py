"""Deterministic persistence from adapter output to consumption raw facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from typing import Iterable

from sqlalchemy.orm import Session

from backend.services.consumption.adapters.common import normalized_text
from backend.services.consumption.contracts import (
    FieldAvailability,
    NormalizedRawTransaction,
    ParsedStatement,
    canonical_json,
    raw_row_fingerprint,
)
from backend.services.consumption.models import Account, ImportBatch, PaymentInstrument, RawTransaction


COVERAGE_EXPLICIT = "EXPLICIT"
COVERAGE_OBSERVED_ONLY = "OBSERVED_ONLY"
COVERAGE_UNKNOWN = "UNKNOWN"

DEDUP_UNIQUE = "UNIQUE"
DEDUP_CANDIDATE_DUPLICATE = "CANDIDATE_DUPLICATE"
DEDUP_AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class PersistedImport:
    import_batch: ImportBatch
    reused_existing_batch: bool


def _coverage(parsed: ParsedStatement) -> tuple[str, date | None, date | None, date | None, date | None]:
    metadata = parsed.metadata
    transaction_dates = [row.transaction_date for row in parsed.transactions if row.transaction_date]
    observed_start = min(transaction_dates) if transaction_dates else None
    observed_end = max(transaction_dates) if transaction_dates else None
    if metadata.statement_period_start and metadata.statement_period_end:
        return (
            COVERAGE_EXPLICIT,
            metadata.statement_period_start,
            metadata.statement_period_end,
            observed_start,
            observed_end,
        )
    if observed_start and observed_end:
        return COVERAGE_OBSERVED_ONLY, None, None, observed_start, observed_end
    return COVERAGE_UNKNOWN, None, None, None, None


def _match_fingerprint(
    *, account_id: str, payment_instrument_id: str | None, institution: str,
    transaction: NormalizedRawTransaction,
) -> str:
    """A conservative candidate key, deliberately not a uniqueness guarantee.

    It uses only superficial whitespace/case normalization of the raw description;
    no merchant inference, semantic normalization, or classification occurs here.
    """
    payload = {
        "institution": institution,
        "account_id": account_id,
        "payment_instrument_id": payment_instrument_id,
        "transaction_date": transaction.transaction_date.isoformat() if transaction.transaction_date else None,
        "posting_date": transaction.posting_date.isoformat() if transaction.posting_date else None,
        "amount": format(transaction.amount, "f"),
        "currency": transaction.currency.upper(),
        "raw_description": normalized_text(transaction.raw_description).casefold(),
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ConsumptionImportService:
    """Persists already parsed statements without upload, network, AI, or classification."""

    def persist(
        self,
        session: Session,
        *,
        account: Account,
        parsed_statement: ParsedStatement,
        payment_instrument: PaymentInstrument | None = None,
    ) -> PersistedImport:
        if not account.id:
            raise ValueError("account must be persisted before importing a statement")
        if payment_instrument and payment_instrument.account_id != account.id:
            raise ValueError("payment instrument must belong to the import account")

        metadata = parsed_statement.metadata
        if not metadata.source_file_hash or len(metadata.source_file_hash) != 64:
            raise ValueError("parsed statement must include its SHA-256 source_file_hash")

        existing = (
            session.query(ImportBatch)
            .filter(ImportBatch.source_file_hash == metadata.source_file_hash)
            .one_or_none()
        )
        if existing is not None:
            return PersistedImport(import_batch=existing, reused_existing_batch=True)

        identities = [row.source_row_identity for row in parsed_statement.transactions]
        if len(identities) != len(set(identities)):
            raise ValueError("parsed statement contains duplicate source_row_identity values")

        coverage_status, period_start, period_end, observed_start, observed_end = _coverage(parsed_statement)
        batch = ImportBatch(
            account_id=account.id,
            source_format=metadata.source_format,
            institution=metadata.institution,
            statement_type=metadata.statement_type,
            source_file_hash=metadata.source_file_hash,
            parser_version=metadata.parser_version,
            statement_period_start=period_start,
            statement_period_end=period_end,
            statement_period_availability=metadata.field_availability.get(
                "statement_period", FieldAvailability.SOURCE_UNAVAILABLE
            ).value,
            coverage_status=coverage_status,
            observed_transaction_start=observed_start,
            observed_transaction_end=observed_end,
            row_count=len(parsed_statement.transactions),
        )
        session.add(batch)
        session.flush()

        persisted_rows = [
            self._raw_transaction(
                batch=batch,
                account=account,
                payment_instrument=payment_instrument,
                transaction=transaction,
            )
            for transaction in parsed_statement.transactions
        ]
        session.add_all(persisted_rows)
        session.flush()
        self._refresh_match_statuses(session, (row.match_fingerprint for row in persisted_rows))
        return PersistedImport(import_batch=batch, reused_existing_batch=False)

    @staticmethod
    def _raw_transaction(
        *, batch: ImportBatch, account: Account, payment_instrument: PaymentInstrument | None,
        transaction: NormalizedRawTransaction,
    ) -> RawTransaction:
        return RawTransaction(
            import_batch_id=batch.id,
            account_id=account.id,
            payment_instrument_id=payment_instrument.id if payment_instrument else None,
            source_row_index=transaction.source_row_index,
            source_row_identity=transaction.source_row_identity,
            source_row_fingerprint_candidate=raw_row_fingerprint(
                institution=batch.institution, transaction=transaction
            ),
            match_fingerprint=_match_fingerprint(
                account_id=account.id,
                payment_instrument_id=payment_instrument.id if payment_instrument else None,
                institution=batch.institution,
                transaction=transaction,
            ),
            transaction_date=transaction.transaction_date,
            transaction_date_availability=transaction.transaction_date_availability.value,
            posting_date=transaction.posting_date,
            posting_date_availability=transaction.posting_date_availability.value,
            amount=transaction.amount,
            currency=transaction.currency.upper(),
            settlement_amount=transaction.settlement_amount,
            settlement_currency=transaction.settlement_currency.upper() if transaction.settlement_currency else None,
            balance=transaction.balance,
            raw_description=transaction.raw_description,
            raw_counterparty=transaction.counterparty,
            mcc=transaction.mcc,
            parser_provenance=canonical_json(transaction.parser_provenance),
            source_field_availability=canonical_json({
                "transaction_date": transaction.transaction_date_availability.value,
                "posting_date": transaction.posting_date_availability.value,
                **{key: value.value for key, value in transaction.field_availability.items()},
            }),
        )

    @staticmethod
    def _refresh_match_statuses(session: Session, fingerprints: Iterable[str]) -> None:
        """Refresh candidate state without deleting or linking any raw fact."""
        for fingerprint in set(fingerprints):
            rows = (
                session.query(RawTransaction)
                .filter(RawTransaction.match_fingerprint == fingerprint)
                .order_by(RawTransaction.import_batch_id, RawTransaction.source_row_index)
                .all()
            )
            batches = {row.import_batch_id for row in rows}
            batch_sizes = {
                batch_id: sum(row.import_batch_id == batch_id for row in rows)
                for batch_id in batches
            }
            if any(count > 1 for count in batch_sizes.values()):
                status = DEDUP_AMBIGUOUS
            elif len(batches) > 1:
                status = DEDUP_CANDIDATE_DUPLICATE
            else:
                status = DEDUP_UNIQUE
            for row in rows:
                row.dedup_status = status
