"""Explicit nullable-cost compatibility using the original migration CLI.

No tables or columns are added. Existing snapshot facts are copied byte-for-byte
inside the same short SQLite transaction; runtime only verifies this shape.
"""
import re

COLUMNS = {}
FIELDS = ("avg_cost", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct")


def plan(connection):
    from app.b2_schema import verify as verify_b2

    verify_b2(connection)
    columns = {r[1]: r for r in connection.execute("PRAGMA table_info(position_snapshots)")}
    if not all(name in columns for name in FIELDS):
        raise ValueError("BINANCE_SNAPSHOT_SCHEMA_CONFLICT")
    if all(not columns[name][3] for name in FIELDS):
        return []
    if not all(columns[name][3] for name in FIELDS):
        raise ValueError("BINANCE_PARTIAL_SCHEMA_REQUIRES_REVIEW")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='position_snapshots_nullable'").fetchone():
        raise ValueError("BINANCE_MIGRATION_TEMP_CONFLICT")
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if any(fk[2] == "position_snapshots" for fk in connection.execute(f'PRAGMA foreign_key_list("{row[0]}")')):
            raise ValueError("BINANCE_INBOUND_SNAPSHOT_REFERENCE_REQUIRES_REVIEW")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='position_snapshots'").fetchone():
        raise ValueError("BINANCE_SNAPSHOT_TRIGGER_REQUIRES_REVIEW")
    sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='position_snapshots'").fetchone()[0]
    for name in FIELDS:
        sql, count = re.subn(r'\b' + name + r'(\s+NUMERIC\s*\(\d+,\s*\d+\))\s+NOT NULL',
                            name + r'\1', sql, flags=re.I)
        if count != 1:
            raise ValueError("BINANCE_SNAPSHOT_DEFINITION_CONFLICT")
    sql = re.sub(r'CREATE TABLE\s+["\[]?position_snapshots["\]]?', "CREATE TABLE position_snapshots_nullable",
                 sql, count=1, flags=re.I)
    indexes = [r[0] for r in connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='position_snapshots' AND sql IS NOT NULL ORDER BY name")]
    names = ",".join('"' + name + '"' for name in columns)
    return [sql, f"INSERT INTO position_snapshots_nullable ({names}) SELECT {names} FROM position_snapshots",
            "DROP TABLE position_snapshots", "ALTER TABLE position_snapshots_nullable RENAME TO position_snapshots",
            *indexes]


def verify(connection):
    if plan(connection):
        raise ValueError("BINANCE_SCHEMA_REQUIRED")


def apply(connection):
    for statement in plan(connection):
        connection.execute(statement)
    verify(connection)
