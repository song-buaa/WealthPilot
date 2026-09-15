"""Thin offline orchestration for local consumption-statement bootstrap.

This module deliberately composes the existing adapters, raw persistence,
normalizer, resolver, and analytics service.  It contains no parsing,
classification, or analytics rules of its own.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from backend.services.consumption.adapters.ccb_credit_card_eml import (
    parse_ccb_credit_card_eml,
)
from backend.services.consumption.adapters.cmb_credit_card_pdf import (
    parse_cmb_credit_card_pdf,
)
from backend.services.consumption.adapters.cmb_debit_card_pdf import (
    parse_cmb_debit_card_pdf,
)
from backend.services.consumption.analytics import ConsumptionAnalyticsService
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.contracts import ParsedStatement
from backend.services.consumption.import_service import ConsumptionImportService
from backend.services.consumption.models import (
    Account,
    ConsumptionInterpretation,
    EconomicEvent,
    EventRawLink,
    PaymentInstrument,
    RawTransaction,
)
from backend.services.consumption.normalization import EconomicEventNormalizer


class SourceKind(StrEnum):
    CMB_CREDIT = "CMB_CREDIT"
    CCB_CREDIT = "CCB_CREDIT"
    CMB_DEBIT = "CMB_DEBIT"


@dataclass(frozen=True)
class SourceDefinition:
    label: str
    institution: str
    statement_type: str
    parser: Callable[[bytes], ParsedStatement]


SOURCE_DEFINITIONS = {
    SourceKind.CMB_CREDIT: SourceDefinition(
        "CMB Credit", "CMB", "CREDIT_CARD", parse_cmb_credit_card_pdf
    ),
    SourceKind.CCB_CREDIT: SourceDefinition(
        "CCB Credit", "CCB", "CREDIT_CARD", parse_ccb_credit_card_eml
    ),
    SourceKind.CMB_DEBIT: SourceDefinition(
        "CMB Debit", "CMB", "DEBIT_CARD", parse_cmb_debit_card_pdf
    ),
}


class BootstrapError(RuntimeError):
    """A safe, source-level bootstrap failure without raw-source content."""


@dataclass(frozen=True)
class BootstrapSource:
    kind: SourceKind
    source_bytes: bytes
    source_name: str
    parser: Callable[[bytes], ParsedStatement] | None = None


@dataclass(frozen=True)
class PreparedSource:
    kind: SourceKind
    source_name: str
    parsed_statement: ParsedStatement


@dataclass(frozen=True)
class AccountOutcome:
    label: str
    created: bool
    reused: bool


@dataclass(frozen=True)
class BootstrapResult:
    source_statement_count: int
    parsed_row_count: int
    new_batch_count: int
    reused_batch_count: int
    inserted_raw_row_count: int
    reused_raw_row_count: int
    active_event_count: int
    eligibility_counts: dict[str, int]
    classification_counts: dict[str, int]
    analytics_non_empty: bool
    analytics_month_count: int
    latest_month_status: str | None
    invariant_passed: bool
    account_outcomes: tuple[AccountOutcome, ...]
    dry_run: bool = False


def prepare_sources(sources: Iterable[BootstrapSource]) -> tuple[PreparedSource, ...]:
    """Parse and validate every explicit source before opening a DB transaction."""
    prepared: list[PreparedSource] = []
    for source in sources:
        definition = SOURCE_DEFINITIONS[source.kind]
        try:
            parsed = (source.parser or definition.parser)(source.source_bytes)
        except Exception as exc:  # parser details may include source text; never surface them
            raise BootstrapError(
                f"{definition.label} parser failed ({type(exc).__name__})"
            ) from exc
        metadata = parsed.metadata
        if (
            metadata.institution != definition.institution
            or metadata.statement_type != definition.statement_type
        ):
            raise BootstrapError(f"{definition.label} source metadata does not match its selected type")
        if not metadata.source_file_hash or len(metadata.source_file_hash) != 64:
            raise BootstrapError(f"{definition.label} parser did not produce a source hash")
        if not parsed.transactions:
            raise BootstrapError(f"{definition.label} source contains no parsable transaction rows")
        prepared.append(PreparedSource(source.kind, source.source_name, parsed))
    if not prepared:
        raise BootstrapError("at least one explicit statement source is required")
    return tuple(prepared)


def _upsert_account(session: Session, prepared: PreparedSource) -> tuple[Account, bool]:
    definition = SOURCE_DEFINITIONS[prepared.kind]
    metadata = prepared.parsed_statement.metadata
    query = session.query(Account).filter_by(
        institution=definition.institution,
        account_type=definition.statement_type,
    )
    if metadata.account_masked is not None:
        account = query.filter(
            Account.masked_account_identifier == metadata.account_masked
        ).one_or_none()
    else:
        candidates = query.filter(Account.masked_account_identifier.is_(None)).all()
        account = candidates[0] if len(candidates) == 1 else None
    if account is not None:
        return account, False
    account = Account(
        institution=definition.institution,
        account_type=definition.statement_type,
        display_name=definition.label,
        masked_account_identifier=metadata.account_masked,
    )
    session.add(account)
    session.flush()
    return account, True


def _upsert_instrument(
    session: Session, account: Account, prepared: PreparedSource
) -> PaymentInstrument | None:
    metadata = prepared.parsed_statement.metadata
    if prepared.kind == SourceKind.CMB_DEBIT or not metadata.instrument_masked:
        return None
    instrument = session.query(PaymentInstrument).filter_by(
        account_id=account.id,
        masked_identifier=metadata.instrument_masked,
    ).one_or_none()
    if instrument is not None:
        return instrument
    instrument = PaymentInstrument(
        account_id=account.id,
        instrument_type="PHYSICAL_CARD",
        masked_identifier=metadata.instrument_masked,
    )
    session.add(instrument)
    session.flush()
    return instrument


def _empty_result(prepared: tuple[PreparedSource, ...]) -> BootstrapResult:
    return BootstrapResult(
        source_statement_count=len(prepared),
        parsed_row_count=sum(len(item.parsed_statement.transactions) for item in prepared),
        new_batch_count=0,
        reused_batch_count=0,
        inserted_raw_row_count=0,
        reused_raw_row_count=0,
        active_event_count=0,
        eligibility_counts={},
        classification_counts={},
        analytics_non_empty=False,
        analytics_month_count=0,
        latest_month_status=None,
        invariant_passed=False,
        account_outcomes=(),
        dry_run=True,
    )


def bootstrap_prepared_sources(
    session: Session,
    prepared: tuple[PreparedSource, ...],
    *,
    as_of: date | None = None,
    dry_run: bool = False,
) -> BootstrapResult:
    """Persist prepared sources atomically through the production pipeline."""
    if dry_run:
        return _empty_result(prepared)

    account_outcomes: dict[SourceKind, AccountOutcome] = {}
    inserted_rows: list[RawTransaction] = []
    new_batches = reused_batches = inserted_raw_rows = reused_raw_rows = 0
    import_service = ConsumptionImportService()

    try:
        with (session.begin_nested() if session.in_transaction() else session.begin()):
            for item in prepared:
                account, created = _upsert_account(session, item)
                definition = SOURCE_DEFINITIONS[item.kind]
                current = account_outcomes.get(item.kind)
                account_outcomes[item.kind] = AccountOutcome(
                    label=definition.label,
                    created=(current.created if current else False) or created,
                    reused=(current.reused if current else False) or not created,
                )
                instrument = _upsert_instrument(session, account, item)
                persisted = import_service.persist(
                    session,
                    account=account,
                    parsed_statement=item.parsed_statement,
                    payment_instrument=instrument,
                )
                if persisted.reused_existing_batch:
                    reused_batches += 1
                    reused_raw_rows += persisted.import_batch.row_count
                else:
                    new_batches += 1
                    inserted_raw_rows += persisted.import_batch.row_count
                    inserted_rows.extend(persisted.import_batch.raw_transactions)

            normalizer = EconomicEventNormalizer()
            normalizer.normalize(session, inserted_rows)
            # Statement ranges may overlap. Preserve every bank-source row for
            # audit, then immediately collapse deterministic cross-batch source
            # matches before analytics or classification can count them twice.
            normalizer.replay(session)
            raw_ids = tuple(row.id for row in inserted_rows)
            if raw_ids:
                event_ids = tuple(
                    row[0]
                    for row in session.query(EventRawLink.event_id)
                    .filter(EventRawLink.raw_transaction_id.in_(raw_ids))
                    .distinct()
                    .all()
                )
                ClassificationResolver().replay(session, event_ids)

            active_event_count = session.query(EconomicEvent).filter_by(is_active=True).count()
            interpretations = session.query(ConsumptionInterpretation).filter_by(is_active=True).all()
            eligibility_counts = dict(Counter(item.eligibility_status for item in interpretations))
            classification_counts = dict(Counter(item.classification_status for item in interpretations))
            summary = ConsumptionAnalyticsService(session).summary(
                as_of=as_of or date.today(), months=12
            )
            invariant_passed = all(
                point.daily_cny + point.travel_cny + point.housing_cny
                + point.unclassified_eligible_cny
                == point.total_spending_cny
                for point in summary.months
            )
            analytics_non_empty = any(point.eligible_event_count > 0 for point in summary.months)
            latest = summary.months[-1] if summary.months else None
    except Exception as exc:
        if isinstance(exc, BootstrapError):
            raise
        raise BootstrapError(f"database pipeline failed ({type(exc).__name__})") from exc

    return BootstrapResult(
        source_statement_count=len(prepared),
        parsed_row_count=sum(len(item.parsed_statement.transactions) for item in prepared),
        new_batch_count=new_batches,
        reused_batch_count=reused_batches,
        inserted_raw_row_count=inserted_raw_rows,
        reused_raw_row_count=reused_raw_rows,
        active_event_count=active_event_count,
        eligibility_counts=eligibility_counts,
        classification_counts=classification_counts,
        analytics_non_empty=analytics_non_empty,
        analytics_month_count=len(summary.months),
        latest_month_status=latest.data_coverage_status.value if latest else None,
        invariant_passed=invariant_passed,
        account_outcomes=tuple(account_outcomes[key] for key in sorted(account_outcomes)),
    )


def bootstrap_sources(
    session: Session,
    sources: Iterable[BootstrapSource],
    *,
    as_of: date | None = None,
    dry_run: bool = False,
) -> BootstrapResult:
    return bootstrap_prepared_sources(
        session, prepare_sources(sources), as_of=as_of, dry_run=dry_run
    )
