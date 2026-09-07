"""Safely replay automatic classifications for existing local consumption events."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.classification_design import ClassificationSource
from backend.services.consumption.economic_events import EventType
from backend.services.consumption.models import ConsumptionInterpretation, EconomicEvent


_PROTECTED_SOURCES = (
    ClassificationSource.USER_CONFIRMATION.value,
    ClassificationSource.USER_RULE.value,
)


@dataclass(frozen=True)
class ReplaySummary:
    target_event_count: int
    updated_interpretation_count: int
    skipped_user_explicit_count: int


def automatic_consumption_event_ids(session: Session) -> tuple[str, ...]:
    """Return active CONSUMPTION events whose current interpretation is automatic.

    Local confirmations and local user rules are intentionally outside a system
    replay, even though the resolver itself would also preserve confirmations.
    """
    automatic = or_(
        ConsumptionInterpretation.id.is_(None),
        and_(
            ConsumptionInterpretation.user_confirmed.is_(False),
            ~ConsumptionInterpretation.classification_source.in_(_PROTECTED_SOURCES),
        ),
    )
    return tuple(
        row[0]
        for row in (
            session.query(EconomicEvent.id)
            .outerjoin(
                ConsumptionInterpretation,
                (ConsumptionInterpretation.event_id == EconomicEvent.id)
                & ConsumptionInterpretation.is_active.is_(True),
            )
            .filter(
                EconomicEvent.is_active.is_(True),
                EconomicEvent.event_type == EventType.CONSUMPTION.value,
                automatic,
            )
            .order_by(EconomicEvent.id)
        )
    )


def replay_automatic_consumption(session: Session) -> ReplaySummary:
    """Append current deterministic interpretations without touching user results."""
    event_ids = automatic_consumption_event_ids(session)
    before = {
        item.event_id: item.id
        for item in session.query(ConsumptionInterpretation).filter(
            ConsumptionInterpretation.is_active.is_(True),
            ConsumptionInterpretation.event_id.in_(event_ids),
        )
    } if event_ids else {}
    ClassificationResolver().replay(session, event_ids)
    after = {
        item.event_id: item.id
        for item in session.query(ConsumptionInterpretation).filter(
            ConsumptionInterpretation.is_active.is_(True),
            ConsumptionInterpretation.event_id.in_(event_ids),
        )
    } if event_ids else {}
    skipped = (
        session.query(ConsumptionInterpretation)
        .join(EconomicEvent, EconomicEvent.id == ConsumptionInterpretation.event_id)
        .filter(
            ConsumptionInterpretation.is_active.is_(True),
            EconomicEvent.is_active.is_(True),
            EconomicEvent.event_type == EventType.CONSUMPTION.value,
            or_(
                ConsumptionInterpretation.user_confirmed.is_(True),
                ConsumptionInterpretation.classification_source.in_(_PROTECTED_SOURCES),
            ),
        )
        .count()
    )
    return ReplaySummary(
        target_event_count=len(event_ids),
        updated_interpretation_count=sum(before.get(event_id) != interpretation_id for event_id, interpretation_id in after.items()),
        skipped_user_explicit_count=skipped,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay automatic local consumption classifications offline.")
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
            summary = replay_automatic_consumption(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Replay targets: {summary.target_event_count}")
        print(f"Interpretations updated: {summary.updated_interpretation_count}")
        print(f"User-explicit interpretations skipped: {summary.skipped_user_explicit_count}")
        return 0
    except Exception as exc:
        print(f"Consumption classification replay failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
