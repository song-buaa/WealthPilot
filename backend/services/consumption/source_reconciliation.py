"""Safe correction of parser-derived Raw fields from the identical source bytes.

This is deliberately not an import path: every supplied statement must already
exist as an ImportBatch with the same source-file hash.  It updates only parser
outputs, retires only rows omitted by a verified newer parser, and leaves all
source rows available for audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from backend.services.consumption.contracts import (
    ParsedStatement,
    canonical_json,
    raw_row_fingerprint,
)
from backend.services.consumption.import_service import ConsumptionImportService, _match_fingerprint
from backend.services.consumption.models import (
    ConsumptionInterpretation,
    EconomicEvent,
    EventRawLink,
    ImportBatch,
    Account,
    PaymentInstrument,
    RawTransaction,
)


_USER_EXPLICIT_SOURCES = {"USER_CONFIRMATION", "USER_RULE"}
_RETIRED_REASON = "VERIFIED_PARSER_DUPLICATE_PRESENTATION"


class SourceReconciliationError(RuntimeError):
    """The supplied source cannot safely reconcile the current local database."""


@dataclass(frozen=True)
class SourceReconciliationResult:
    reconciled_batches: int
    corrected_raw_rows: int
    inserted_raw_rows: int
    relocated_batch_count: int
    retired_duplicate_rows: int
    retired_event_ids: tuple[str, ...]


def reconcile_parsed_statements(
    session: Session, statements: tuple[ParsedStatement, ...],
) -> SourceReconciliationResult:
    """Apply a newer deterministic parser to already-imported identical bytes.

    Source identities are the safety boundary.  A parser may correct fields of
    an existing identity, but it may not invent a new identity during this
    repair.  Rows no longer emitted by the parser are retained and marked
    inactive only after checking that no user-explicit interpretation exists.
    """
    corrected = retired = inserted = relocated = 0
    retired_event_ids: set[str] = set()
    for statement in statements:
        source_hash = statement.metadata.source_file_hash
        batch = session.query(ImportBatch).filter_by(source_file_hash=source_hash).one_or_none()
        if batch is None:
            raise SourceReconciliationError("source file is not an existing ImportBatch")
        target_account = _target_account(session, batch, statement)
        if batch.account_id != target_account.id:
            batch.account_id = target_account.id
            for raw in batch.raw_transactions:
                raw.account_id = target_account.id
            relocated += 1
        parsed = {item.source_row_identity: item for item in statement.transactions}
        if len(parsed) != len(statement.transactions):
            raise SourceReconciliationError("parser returned duplicate source row identities")
        existing = {item.source_row_identity: item for item in batch.raw_transactions}
        unknown = set(parsed) - set(existing)
        for identity in sorted(unknown):
            transaction = parsed[identity]
            instrument = _instrument_for(session, target_account, transaction.instrument_masked)
            raw = ConsumptionImportService._raw_transaction(
                batch=batch, account=target_account, payment_instrument=instrument, transaction=transaction,
            )
            session.add(raw)
            existing[identity] = raw
            inserted += 1

        for identity, transaction in parsed.items():
            raw = existing[identity]
            instrument = _instrument_for(session, target_account, transaction.instrument_masked)
            values = {
                "account_id": target_account.id,
                "payment_instrument_id": instrument.id if instrument else None,
                "source_row_index": transaction.source_row_index,
                "transaction_date": transaction.transaction_date,
                "transaction_date_availability": transaction.transaction_date_availability.value,
                "posting_date": transaction.posting_date,
                "posting_date_availability": transaction.posting_date_availability.value,
                "amount": transaction.amount,
                "currency": transaction.currency.upper(),
                "settlement_amount": transaction.settlement_amount,
                "settlement_currency": transaction.settlement_currency.upper() if transaction.settlement_currency else None,
                "balance": transaction.balance,
                "raw_description": transaction.raw_description,
                "raw_counterparty": transaction.counterparty,
                "mcc": transaction.mcc,
                "parser_provenance": canonical_json(transaction.parser_provenance),
                "source_field_availability": canonical_json({
                    "transaction_date": transaction.transaction_date_availability.value,
                    "posting_date": transaction.posting_date_availability.value,
                    **{key: value.value for key, value in transaction.field_availability.items()},
                }),
                "source_row_fingerprint_candidate": raw_row_fingerprint(
                    institution=batch.institution, transaction=transaction,
                ),
                "match_fingerprint": _match_fingerprint(
                    account_id=raw.account_id,
                    payment_instrument_id=instrument.id if instrument else None,
                    institution=batch.institution,
                    transaction=transaction,
                ),
            }
            if any(getattr(raw, key) != value for key, value in values.items()) or not raw.is_active:
                for key, value in values.items():
                    setattr(raw, key, value)
                raw.is_active = True
                raw.retired_reason = None
                raw.retired_at = None
                corrected += 1

        for identity, raw in existing.items():
            if identity in parsed or not raw.is_active:
                continue
            active_links = [link for link in raw.event_links if link.is_active]
            event_ids = {link.event_id for link in active_links}
            if _has_user_explicit_interpretation(session, event_ids):
                raise SourceReconciliationError("parser duplicate is protected by a user-explicit interpretation")
            for link in active_links:
                link.is_active = False
                retired_event_ids.add(link.event_id)
            raw.is_active = False
            raw.retired_reason = _RETIRED_REASON
            raw.retired_at = datetime.utcnow()
            retired += 1

        batch.parser_version = statement.metadata.parser_version
        batch.row_count = len(statement.transactions)

    session.flush()
    for event_id in retired_event_ids:
        event = session.get(EconomicEvent, event_id)
        if event and not session.query(EventRawLink).filter_by(event_id=event.id, is_active=True).first():
            event.is_active = False
    ConsumptionImportService._refresh_match_statuses(
        session,
        (row.match_fingerprint for row in session.query(RawTransaction).filter_by(is_active=True)),
    )
    session.flush()
    return SourceReconciliationResult(
        reconciled_batches=len(statements),
        corrected_raw_rows=corrected,
        inserted_raw_rows=inserted,
        relocated_batch_count=relocated,
        retired_duplicate_rows=retired,
        retired_event_ids=tuple(sorted(retired_event_ids)),
    )


def _target_account(session: Session, batch: ImportBatch, statement: ParsedStatement) -> Account:
    masked = statement.metadata.account_masked
    if not masked:
        return batch.account
    account = session.query(Account).filter_by(
        institution=batch.institution, account_type=batch.statement_type,
        masked_account_identifier=masked,
    ).one_or_none()
    if account is not None:
        return account
    account = Account(
        institution=batch.institution, account_type=batch.statement_type,
        display_name=batch.account.display_name, masked_account_identifier=masked,
    )
    session.add(account)
    session.flush()
    return account


def _instrument_for(session: Session, account: Account, masked: str | None) -> PaymentInstrument | None:
    if not masked:
        return None
    instrument = session.query(PaymentInstrument).filter_by(
        account_id=account.id, masked_identifier=masked,
    ).one_or_none()
    if instrument is not None:
        return instrument
    instrument = PaymentInstrument(
        account_id=account.id, instrument_type="PHYSICAL_CARD", masked_identifier=masked,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _has_user_explicit_interpretation(session: Session, event_ids: set[str]) -> bool:
    if not event_ids:
        return False
    return bool(session.query(ConsumptionInterpretation).filter(
        ConsumptionInterpretation.event_id.in_(event_ids),
        ConsumptionInterpretation.is_active.is_(True),
        (
            ConsumptionInterpretation.user_confirmed.is_(True)
            | ConsumptionInterpretation.classification_source.in_(_USER_EXPLICIT_SOURCES)
        ),
    ).first())
