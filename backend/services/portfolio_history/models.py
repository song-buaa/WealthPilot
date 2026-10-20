"""Two factual tables; monetary precision lives in canonical decimal JSON strings."""
from sqlalchemy import Column, Integer, Text, ForeignKey, Index
from app.database import Base


class HistoryImport(Base):
    __tablename__ = 'portfolio_history_imports'
    id = Column(Text, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    broker = Column(Text, nullable=False)
    account_id = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    digest = Column(Text, nullable=False)
    imported_at = Column(Text, nullable=False)
    coverage_start = Column(Text, nullable=False)
    coverage_end = Column(Text, nullable=False)
    evidence_json = Column(Text, nullable=False)
    __table_args__ = (Index('uq_history_import', 'broker', 'account_id', 'source', 'digest', unique=True),)


class HistoryEvent(Base):
    __tablename__ = 'portfolio_history_events'
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    broker = Column(Text, nullable=False)
    account_id = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    external_id = Column(Text, nullable=False)
    instrument_key = Column(Text, nullable=False)
    symbol = Column(Text, nullable=False)
    currency = Column(Text, nullable=False)
    executed_at = Column(Text, nullable=False)
    import_id = Column(Text, ForeignKey('portfolio_history_imports.id'), nullable=False)
    payload_json = Column(Text, nullable=False)
    __table_args__ = (Index('uq_history_event', 'broker', 'account_id', 'kind', 'external_id', unique=True),)
