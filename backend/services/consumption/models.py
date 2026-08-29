"""Persistence models for the consumption raw-fact boundary.

These tables intentionally stop before EconomicEvent and classification.  A row
in ``RawTransaction`` is an observed bank-source record, not a claim about what
the money means.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.utcnow()


class Account(Base):
    """A consumption-domain bank account; unrelated to investment portfolios."""

    __tablename__ = "consumption_accounts"

    id = Column(String(36), primary_key=True, default=_uuid)
    institution = Column(String(30), nullable=False)
    account_type = Column(String(30), nullable=False)  # CREDIT_CARD / DEBIT_CARD
    display_name = Column(String(120), nullable=True)
    masked_account_identifier = Column(String(64), nullable=True)
    base_currency = Column(String(3), nullable=False, default="CNY")
    status = Column(String(20), nullable=False, default="ACTIVE")
    ownership_status = Column(String(30), nullable=False, default="UNCONFIRMED")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    payment_instruments = relationship(
        "PaymentInstrument", back_populates="account", cascade="all, delete-orphan"
    )
    import_batches = relationship("ImportBatch", back_populates="account")
    raw_transactions = relationship("RawTransaction", back_populates="account")

    __table_args__ = (
        Index("ix_consumption_accounts_institution_type", "institution", "account_type"),
        Index("ix_consumption_accounts_ownership", "ownership_status"),
    )


class PaymentInstrument(Base):
    """A card or payment instrument beneath a consumption account."""

    __tablename__ = "consumption_payment_instruments"

    id = Column(String(36), primary_key=True, default=_uuid)
    account_id = Column(
        String(36), ForeignKey("consumption_accounts.id", ondelete="CASCADE"), nullable=False
    )
    instrument_type = Column(String(30), nullable=False)  # PHYSICAL_CARD / VIRTUAL_CARD / OTHER
    masked_identifier = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="ACTIVE")
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    account = relationship("Account", back_populates="payment_instruments")
    raw_transactions = relationship("RawTransaction", back_populates="payment_instrument")

    __table_args__ = (
        Index("ix_consumption_instruments_account", "account_id"),
        Index("ix_consumption_instruments_masked", "masked_identifier"),
    )


class ImportBatch(Base):
    """One parsed source file, identified idempotently by its SHA-256 bytes hash."""

    __tablename__ = "consumption_import_batches"

    id = Column(String(36), primary_key=True, default=_uuid)
    account_id = Column(
        String(36), ForeignKey("consumption_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    source_format = Column(String(20), nullable=False)
    institution = Column(String(30), nullable=False)
    statement_type = Column(String(30), nullable=False)
    source_file_hash = Column(String(64), nullable=False, unique=True)
    parser_version = Column(String(120), nullable=False)
    statement_period_start = Column(Date, nullable=True)
    statement_period_end = Column(Date, nullable=True)
    statement_period_availability = Column(String(30), nullable=False)
    coverage_status = Column(String(20), nullable=False)  # EXPLICIT / OBSERVED_ONLY / UNKNOWN
    observed_transaction_start = Column(Date, nullable=True)
    observed_transaction_end = Column(Date, nullable=True)
    imported_at = Column(DateTime, nullable=False, default=_utcnow)
    row_count = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="COMPLETED")

    account = relationship("Account", back_populates="import_batches")
    raw_transactions = relationship(
        "RawTransaction", back_populates="import_batch", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_consumption_batches_account_imported", "account_id", "imported_at"),
        Index("ix_consumption_batches_coverage", "coverage_status"),
    )


class RawTransaction(Base):
    """An append-only observed source row, never an economic-event interpretation."""

    __tablename__ = "consumption_raw_transactions"

    id = Column(String(36), primary_key=True, default=_uuid)
    import_batch_id = Column(
        String(36), ForeignKey("consumption_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    account_id = Column(
        String(36), ForeignKey("consumption_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    payment_instrument_id = Column(
        String(36), ForeignKey("consumption_payment_instruments.id", ondelete="RESTRICT"), nullable=True
    )
    source_row_index = Column(Integer, nullable=False)
    source_row_identity = Column(String(160), nullable=False)
    source_row_fingerprint_candidate = Column(String(64), nullable=False)
    match_fingerprint = Column(String(64), nullable=False)
    dedup_status = Column(String(30), nullable=False, default="UNIQUE")
    transaction_date = Column(Date, nullable=True)
    transaction_date_availability = Column(String(30), nullable=False)
    posting_date = Column(Date, nullable=True)
    posting_date_availability = Column(String(30), nullable=False)
    amount = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    settlement_amount = Column(Numeric(20, 8), nullable=True)
    settlement_currency = Column(String(3), nullable=True)
    balance = Column(Numeric(20, 8), nullable=True)
    raw_description = Column(Text, nullable=False)
    raw_counterparty = Column(Text, nullable=True)
    mcc = Column(String(30), nullable=True)
    parser_provenance = Column(Text, nullable=False)
    source_field_availability = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    import_batch = relationship("ImportBatch", back_populates="raw_transactions")
    account = relationship("Account", back_populates="raw_transactions")
    payment_instrument = relationship("PaymentInstrument", back_populates="raw_transactions")
    event_links = relationship("EventRawLink", back_populates="raw_transaction")

    __table_args__ = (
        UniqueConstraint("import_batch_id", "source_row_identity", name="uq_consumption_raw_batch_row"),
        Index("ix_consumption_raw_account_transaction_date", "account_id", "transaction_date"),
        Index("ix_consumption_raw_match_fingerprint", "match_fingerprint"),
    )


class EconomicEvent(Base):
    """Immutable economic-event fact; mutable analytical totals live in revisions."""

    __tablename__ = "consumption_economic_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    semantic_key = Column(String(64), nullable=False)
    event_type = Column(String(40), nullable=False)
    event_date = Column(Date, nullable=True)
    analytics_effective_date = Column(Date, nullable=True)
    amount = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    economic_direction = Column(String(20), nullable=False)
    base_currency = Column(String(3), nullable=False, default="CNY")
    base_amount = Column(Numeric(20, 8), nullable=True)
    fx_rate = Column(Numeric(20, 8), nullable=True)
    fx_source = Column(String(30), nullable=False)
    resolution_status = Column(String(30), nullable=False)
    resolution_reason = Column(String(120), nullable=True)
    original_event_id = Column(
        String(36), ForeignKey("consumption_economic_events.id", ondelete="RESTRICT"), nullable=True
    )
    normalizer_version = Column(String(120), nullable=False)
    rule_sources = Column(Text, nullable=False)
    provenance = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    original_event = relationship(
        "EconomicEvent", remote_side=[id], back_populates="refund_events"
    )
    refund_events = relationship("EconomicEvent", back_populates="original_event")
    raw_links = relationship("EventRawLink", back_populates="event", cascade="all, delete-orphan")
    projection_revisions = relationship(
        "EconomicEventProjectionRevision", back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("normalizer_version", "semantic_key", name="uq_consumption_event_semantic"),
        Index("ix_consumption_events_type_date", "event_type", "event_date"),
        Index("ix_consumption_events_original", "original_event_id"),
        Index("ix_consumption_events_resolution", "resolution_status"),
    )


class EventRawLink(Base):
    """Explainable active association from immutable Raw facts to an Event."""

    __tablename__ = "consumption_event_raw_links"

    id = Column(String(36), primary_key=True, default=_uuid)
    event_id = Column(
        String(36), ForeignKey("consumption_economic_events.id", ondelete="CASCADE"), nullable=False
    )
    raw_transaction_id = Column(
        String(36), ForeignKey("consumption_raw_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    link_role = Column(String(30), nullable=False)
    rule_source = Column(String(40), nullable=False)
    evidence = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    event = relationship("EconomicEvent", back_populates="raw_links")
    raw_transaction = relationship("RawTransaction", back_populates="event_links")

    __table_args__ = (
        UniqueConstraint("event_id", "raw_transaction_id", name="uq_consumption_event_raw_link"),
        Index(
            "uq_consumption_active_raw_event", "raw_transaction_id", unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_consumption_event_links_event", "event_id"),
    )


class EconomicEventProjectionRevision(Base):
    """Append-only derived spending projection for a consumption event."""

    __tablename__ = "consumption_event_projection_revisions"

    id = Column(String(36), primary_key=True, default=_uuid)
    event_id = Column(
        String(36), ForeignKey("consumption_economic_events.id", ondelete="CASCADE"), nullable=False
    )
    revision_number = Column(Integer, nullable=False)
    gross_amount = Column(Numeric(20, 8), nullable=False)
    refund_amount = Column(Numeric(20, 8), nullable=False)
    net_amount = Column(Numeric(20, 8), nullable=False)
    base_currency = Column(String(3), nullable=False, default="CNY")
    base_net_amount = Column(Numeric(20, 8), nullable=True)
    reason = Column(String(80), nullable=False)
    rule_source = Column(String(40), nullable=False)
    supersedes_revision_id = Column(
        String(36), ForeignKey("consumption_event_projection_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    event = relationship("EconomicEvent", back_populates="projection_revisions")
    supersedes_revision = relationship(
        "EconomicEventProjectionRevision", remote_side=[id], uselist=False
    )

    __table_args__ = (
        UniqueConstraint("event_id", "revision_number", name="uq_consumption_event_projection_revision"),
        Index(
            "uq_consumption_active_event_projection", "event_id", unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_consumption_projection_event_created", "event_id", "created_at"),
    )
