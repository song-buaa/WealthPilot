"""Idempotently backfill explicit user-provided historical rent expenses."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.classification_design import PrimaryCategory
from backend.services.consumption.manual_events import ManualConsumptionSpec, upsert_manual_consumption


HISTORICAL_RENTS = tuple(
    ManualConsumptionSpec(
        id=f"manual-rent-{year_month}",
        occurred_on=date.fromisoformat(f"{year_month}-15"),
        amount=Decimal("4500"),
        description="支付宝房租",
        primary_category=PrimaryCategory.HOUSING,
        secondary_category="RENT",
    )
    for year_month in (
        "2025-09", "2025-10", "2025-11", "2025-12",
        "2026-01", "2026-02", "2026-03", "2026-04",
    )
)


@dataclass(frozen=True)
class BackfillSummary:
    created_count: int
    reused_count: int


def backfill_historical_rent(session) -> BackfillSummary:
    created_count = sum(upsert_manual_consumption(session, spec) for spec in HISTORICAL_RENTS)
    return BackfillSummary(created_count=created_count, reused_count=len(HISTORICAL_RENTS) - created_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill explicit local rent facts without bank-source fabrication.")
    parser.add_argument("--no-backup", action="store_true", help="skip the default SQLite backup")
    args = parser.parse_args(argv)
    try:
        db_path = Path(database.DB_PATH)
        if args.no_backup:
            print("Backup: skipped by --no-backup")
        else:
            print("Backup: created" if _backup_database(db_path) else "Backup: not needed")
        database.init_db()
        session = database.get_session()
        try:
            summary = backfill_historical_rent(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Manual rent entries created: {summary.created_count}")
        print(f"Manual rent entries reused: {summary.reused_count}")
        return 0
    except Exception as exc:
        print(f"Historical rent backfill failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
