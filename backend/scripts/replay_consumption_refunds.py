"""Apply deterministic refund matching to existing local EconomicEvents."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.refund_matching import RefundMatcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay local consumption refund matches offline.")
    parser.add_argument("--no-backup", action="store_true", help="skip the default SQLite backup")
    args = parser.parse_args(argv)
    try:
        print("Backup: skipped by --no-backup" if args.no_backup else (
            "Backup: created" if _backup_database(Path(database.DB_PATH)) else "Backup: not needed"
        ))
        database.init_db()
        session = database.get_session()
        try:
            result = RefundMatcher().replay(session)
            if result.matched_refund_event_ids:
                ClassificationResolver().replay(session, result.matched_refund_event_ids)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Matched refunds: {result.matched_refund_count}")
        print(f"Matched amount: {result.matched_refund_amount}")
        print(f"Unmatched refunds: {result.unmatched_refund_count}")
        print(f"Unmatched amount: {result.unmatched_refund_amount}")
        return 0
    except Exception as exc:
        print(f"Refund replay failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
