"""Explicit U0 additive DDL. Shared runtime is verification-only."""
from app.b0_schema import verify as verify_b0, _norm

COLUMNS = {"position_snapshot_runs":{"context_json":"TEXT"}}
INDEX = "uq_sync_active_owner"
PREDICATE = "status = 'running' AND portfolio_id IS NOT NULL"


def plan(connection):
    verify_b0(connection)
    columns = {r[1]:r for r in connection.execute("PRAGMA table_info(position_snapshot_runs)")}
    statements = []
    if "context_json" not in columns:
        statements.append("ALTER TABLE position_snapshot_runs ADD COLUMN context_json TEXT")
    elif columns["context_json"][2].upper() != "TEXT" or columns["context_json"][3] or columns["context_json"][4] is not None:
        raise ValueError("U0_COLUMN_CONFLICT")
    index = next((r for r in connection.execute("PRAGMA index_list(position_snapshot_runs)") if r[1] == INDEX),None)
    wanted = ["portfolio_id","broker","account_id","sync_source"]
    if index:
        actual = [r[2] for r in connection.execute(f"PRAGMA index_info({INDEX})")]
        sql = connection.execute("SELECT sql FROM sqlite_master WHERE name=?",(INDEX,)).fetchone()[0]
        if not index[2] or not index[4] or actual != wanted or _norm(sql.split("WHERE",1)[-1]) != _norm(PREDICATE):
            raise ValueError("U0_INDEX_CONFLICT")
    else:
        if connection.execute("SELECT 1 FROM position_snapshot_runs WHERE "+PREDICATE+
            " GROUP BY portfolio_id,broker,account_id,sync_source HAVING count(*)>1 LIMIT 1").fetchone():
            raise ValueError("U0_DUPLICATE_ACTIVE_SCOPE_REQUIRES_REVIEW")
        statements.append(f"CREATE UNIQUE INDEX {INDEX} ON position_snapshot_runs ({','.join(wanted)}) WHERE {PREDICATE}")
    return statements


def verify(connection):
    if plan(connection):
        raise ValueError("U0_EXPLICIT_MIGRATION_REQUIRED")


def apply(connection):
    for statement in plan(connection):
        connection.execute(statement)
    verify(connection)
