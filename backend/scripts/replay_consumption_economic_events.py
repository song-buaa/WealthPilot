"""Replay local EconomicEvent evidence rules without re-importing statements."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.normalization import EconomicEventNormalizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay local Consumption EconomicEvent evidence rules offline.")
    parser.add_argument("--no-backup", action="store_true", help="skip the default SQLite backup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db_path = Path(database.DB_PATH)
        if args.no_backup:
            print("Backup: skipped by --no-backup")
        else:
            print("Backup: created" if _backup_database(db_path) else "Backup: not needed")
        database.init_db()
        session = database.get_session()
        try:
            result = EconomicEventNormalizer().replay(session)
            if result.new_event_ids:
                ClassificationResolver().replay(session, result.new_event_ids)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Raw facts replayed: {result.replayed_raw_count}")
        print(f"Corrected to non-consumption: {result.corrected_non_consumption_count}")
        print(f"Cross-batch duplicates collapsed: {result.collapsed_cross_batch_duplicate_count}")
        print(f"User-explicit results skipped: {result.skipped_user_explicit_count}")
        return 0
    except Exception as exc:
        print(f"EconomicEvent replay failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
