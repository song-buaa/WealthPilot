"""Explicit B0 additive DDL, shared by migration and fresh/local initialization.

Shared production startup verifies only. No contract relaxation or automatic
migration is allowed there. No B1/B2 tables are created by this module.
"""
import re

COLUMNS = {
    "order_records": {"portfolio_id": "INTEGER", "broker_account_id": "VARCHAR(100)",
                      "submission_key": "VARCHAR(64)", "submission_payload_json": "TEXT"},
    "position_snapshot_runs": {"portfolio_id": "INTEGER"},
}
INDEXES = {
    "uq_order_records_submission_key": (
        ["submission_key"], "submission_key IS NOT NULL"),
    "uq_order_records_batch_submission": (
        ["batch_id", "confirmation_version", "batch_leg_id"], "batch_id IS NOT NULL"),
}


def _norm(value):
    return re.sub(r'[\s"`\[\]]+', '', value).lower()


def plan(connection):
    """Validate physical definitions; never hide an incompatible namesake index."""
    statements = []
    for table, additions in COLUMNS.items():
        columns = {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns:
            raise ValueError(f"B0_BASE_TABLE_MISSING:{table}")
        for name, typ in additions.items():
            if name not in columns:
                statements.append(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
            elif columns[name][2].upper() != typ or columns[name][3] or columns[name][4] is not None:
                raise ValueError(f"B0_COLUMN_CONFLICT:{table}.{name}")
    indexes = {row[1]: row for row in connection.execute("PRAGMA index_list(order_records)")}
    for name, (columns, predicate) in INDEXES.items():
        if name in indexes:
            row = indexes[name]
            indexed = [r[2] for r in connection.execute(f"PRAGMA index_info({name})")]
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()[0]
            if not row[2] or not row[4] or indexed != columns or _norm(sql.split("WHERE", 1)[-1]) != _norm(predicate):
                raise ValueError(f"B0_INDEX_CONFLICT:{name}")
        else:
            if all(c in {r[1] for r in connection.execute('PRAGMA table_info(order_records)')} for c in columns):
                dup = connection.execute(f"SELECT 1 FROM order_records WHERE {predicate} GROUP BY {','.join(columns)} HAVING count(*) > 1 LIMIT 1").fetchone()
                if dup:
                    raise ValueError(f"B0_DUPLICATE_KEY:{name}")
            statements.append(f"CREATE UNIQUE INDEX {name} ON order_records ({', '.join(columns)}) WHERE {predicate}")
    return statements


def verify(connection):
    if plan(connection):
        raise ValueError("B0_EXPLICIT_MIGRATION_REQUIRED")


def apply(connection):
    statements = plan(connection)
    for statement in statements:
        connection.execute(statement)
    verify(connection)
    return statements
