"""Explicit additive history facts, using the original receipt/digest migration."""
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from app.u2_schema import verify as verify_u2
from app.b0_schema import _norm
import app.models  # noqa: F401 - resolve the existing Portfolio FK in standalone CLI
from backend.services.portfolio_history.models import HistoryImport, HistoryEvent

TABLES = (HistoryImport.__table__, HistoryEvent.__table__)
COLUMNS = {t.name: {c.name: str(c.type) for c in t.columns} for t in TABLES}


def plan(connection):
    verify_u2(connection)
    statements, present = [], []
    for table in TABLES:
        row = connection.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?', (table.name,)).fetchone()
        wanted = str(CreateTable(table).compile(dialect=dialect())).strip()
        present.append(row is not None)
        if row:
            if _norm(row[0]) != _norm(wanted):
                raise ValueError('B2_TABLE_CONFLICT')
        else:
            statements.append(wanted)
        for index in sorted(table.indexes, key=lambda i:i.name):
            existing = connection.execute('SELECT sql FROM sqlite_master WHERE type="index" AND name=?', (index.name,)).fetchone()
            sql = str(CreateIndex(index).compile(dialect=dialect()))
            if existing:
                if _norm(existing[0]) != _norm(sql):raise ValueError('B2_INDEX_CONFLICT')
            elif row:
                raise ValueError('B2_PARTIAL_SCHEMA_REQUIRES_REVIEW')
            else:statements.append(sql)
    if any(present) and not all(present):raise ValueError('B2_PARTIAL_SCHEMA_REQUIRES_REVIEW')
    return statements


def verify(connection):
    if plan(connection):raise ValueError('B2_EXPLICIT_MIGRATION_REQUIRED')


def apply(connection):
    for statement in plan(connection):connection.execute(statement)
    verify(connection)
