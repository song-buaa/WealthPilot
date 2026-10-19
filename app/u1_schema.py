"""Explicit U1 additive DDL; no new account, holdings or task table."""
from app.u0_schema import verify as verify_u0
from app.b0_schema import _norm

COLUMNS = {"wealth_snapshots": {"analysis_request_id": "TEXT", "payload_json": "TEXT"}}
INDEX = "uq_asset_analysis_request"
PREDICATE = "analysis_request_id IS NOT NULL"


def plan(connection):
    verify_u0(connection)
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(wealth_snapshots)")}
    statements = []
    for name in COLUMNS["wealth_snapshots"]:
        if name not in columns:
            statements.append(f"ALTER TABLE wealth_snapshots ADD COLUMN {name} TEXT")
        elif columns[name][2].upper() != "TEXT" or columns[name][3] or columns[name][4] is not None:
            raise ValueError("U1_COLUMN_CONFLICT")
    existing = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (INDEX,)).fetchone()
    wanted = f"CREATE UNIQUE INDEX {INDEX} ON wealth_snapshots (portfolio_id,analysis_request_id) WHERE {PREDICATE}"
    if existing:
        if _norm(existing[0]) != _norm(wanted):
            raise ValueError("U1_INDEX_CONFLICT")
    else:
        if "analysis_request_id" in columns and connection.execute(
                "SELECT 1 FROM wealth_snapshots WHERE " + PREDICATE +
                " GROUP BY portfolio_id,analysis_request_id HAVING count(*)>1 LIMIT 1").fetchone():
            raise ValueError("U1_DUPLICATE_REQUEST_REQUIRES_REVIEW")
        statements.append(wanted)
    return statements


def verify(connection):
    if plan(connection):
        raise ValueError("U1_EXPLICIT_MIGRATION_REQUIRED")


def apply(connection):
    for statement in plan(connection):
        connection.execute(statement)
    verify(connection)
