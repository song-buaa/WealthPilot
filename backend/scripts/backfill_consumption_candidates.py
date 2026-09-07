"""Confirm a bounded, current local candidate queue as historical consumption.

This maintenance command contains no text, amount, or source-specific rule.  It
is deliberately a one-time, rolling-window action: future ambiguous outflows
remain in the candidate-review queue until a user reviews them.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

from sqlalchemy import func

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.candidate_review import ConsumptionCandidateReviewService
from backend.services.consumption.classification_design import EligibilityStatus
from backend.services.consumption.models import (
    ConsumptionInterpretation, EconomicEvent, EconomicEventProjectionRevision,
)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _months_before(month: date, count: int) -> date:
    ordinal = month.year * 12 + month.month - 1 - count
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def latest_consumption_month(session) -> date | None:
    """Return the current Analytics latest month, not a Raw/import timestamp."""
    latest = (
        session.query(func.max(EconomicEvent.analytics_effective_date))
        .join(
            ConsumptionInterpretation,
            (ConsumptionInterpretation.event_id == EconomicEvent.id)
            & ConsumptionInterpretation.is_active.is_(True),
        )
        .join(
            EconomicEventProjectionRevision,
            (EconomicEventProjectionRevision.event_id == EconomicEvent.id)
            & EconomicEventProjectionRevision.is_active.is_(True),
        )
        .filter(
            EconomicEvent.is_active.is_(True),
            ConsumptionInterpretation.eligibility_status == EligibilityStatus.ELIGIBLE.value,
        )
        .scalar()
    )
    return _month_start(latest) if latest else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Confirm the current rolling candidate queue as local consumption.")
    parser.add_argument("--months", type=int, default=12, help="rolling natural-month window; default: 12")
    parser.add_argument("--no-backup", action="store_true", help="skip the default SQLite backup")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.months < 1:
        print("Candidate backfill failed: --months must be positive", file=sys.stderr)
        return 2
    try:
        db_path = Path(database.DB_PATH)
        print("Backup: skipped by --no-backup" if args.no_backup else f"Backup: {'created' if _backup_database(db_path) else 'not needed'}")
        database.init_db()
        session = database.get_session()
        try:
            latest = latest_consumption_month(session)
            if latest is None:
                print("Candidate backfill failed: no current consumption analytics month", file=sys.stderr)
                return 1
            start = _months_before(latest, args.months - 1)
            next_month = _months_before(latest, -1)
            end = next_month - timedelta(days=1)
            summary = ConsumptionCandidateReviewService(session).confirm_candidates_in_period(
                start_date=start, end_date=end,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Rolling window: {start.isoformat()} to {end.isoformat()}")
        print(f"Candidates confirmed: {summary.candidate_count}")
        print(f"Candidates amount CNY: {summary.candidate_amount_cny}")
        print(f"Automatically classified: {summary.classified_count} / {summary.classified_amount_cny}")
        print(f"Needs classification review: {summary.needs_review_count} / {summary.needs_review_amount_cny}")
        return 0
    except Exception as exc:
        print(f"Candidate backfill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
