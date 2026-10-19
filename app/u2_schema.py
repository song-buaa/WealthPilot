"""One nullable order progress column; no new tables, indexes or data edits."""
from app.u1_schema import verify as verify_u1

COLUMNS = {"order_records":{"fill_sync_json":"TEXT"}}


def plan(connection):
    verify_u1(connection)
    columns={r[1]:r for r in connection.execute("PRAGMA table_info(order_records)")}
    row=columns.get("fill_sync_json")
    if row is None:
        return ["ALTER TABLE order_records ADD COLUMN fill_sync_json TEXT"]
    if row[2].upper()!="TEXT" or row[3] or row[4] is not None:
        raise ValueError("U2_COLUMN_CONFLICT")
    return []


def verify(connection):
    if plan(connection):raise ValueError("U2_EXPLICIT_MIGRATION_REQUIRED")


def apply(connection):
    for statement in plan(connection):connection.execute(statement)
    verify(connection)
