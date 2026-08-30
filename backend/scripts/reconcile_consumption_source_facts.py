"""Reparse existing local statement bytes without re-importing them."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app import database
from backend.scripts.import_consumption_statements import _archive_sources, _backup_database
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.import_runner import prepare_sources
from backend.services.consumption.normalization import EconomicEventNormalizer
from backend.services.consumption.source_reconciliation import (
    SourceReconciliationError,
    reconcile_parsed_statements,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile parser-derived consumption source facts from existing local statements."
    )
    parser.add_argument("--archive", nargs="+", required=True, metavar="PATH")
    parser.add_argument("--no-backup", action="store_true", help="skip the default SQLite backup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sources = _archive_sources([Path(value) for value in args.archive])
        prepared = prepare_sources(sources)
        if args.no_backup:
            print("Backup: skipped by --no-backup")
        else:
            print("Backup: created" if _backup_database(Path(database.DB_PATH)) else "Backup: not needed")
        database.init_db()
        session = database.get_session()
        try:
            source_result = reconcile_parsed_statements(
                session, tuple(item.parsed_statement for item in prepared),
            )
            event_result = EconomicEventNormalizer().replay(session)
            if event_result.new_event_ids:
                ClassificationResolver().replay(session, event_result.new_event_ids)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Reconciled batches: {source_result.reconciled_batches}")
        print(f"Corrected parser rows: {source_result.corrected_raw_rows}")
        print(f"Retired parser-duplicate rows: {source_result.retired_duplicate_rows}")
        print(f"Replayed active Raw facts: {event_result.replayed_raw_count}")
        print(f"Corrected to non-consumption: {event_result.corrected_non_consumption_count}")
        print(f"User-explicit results skipped: {event_result.skipped_user_explicit_count}")
        return 0
    except (SourceReconciliationError, ValueError) as exc:
        print(f"Source reconciliation blocked: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Source reconciliation failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
