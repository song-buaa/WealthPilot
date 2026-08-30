"""Apply one bounded local Consumption user rule and replay interpretations.

This CLI deliberately accepts all rule scope as explicit arguments.  It never
contains user-specific identifiers or source text, and it only prints safe
counts so a local rule can remain local to the ignored SQLite database.
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

from app import database
from backend.scripts.import_consumption_statements import _backup_database
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.classification_design import EligibilityStatus, PrimaryCategory
from backend.services.consumption.economic_events import EventType, RuleSource
from backend.services.consumption.models import ConsumptionInterpretation, UserClassificationRule
from backend.services.consumption.normalization.service import EconomicEventNormalizer


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("amount must be a decimal number") from exc


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def apply_local_rule(
    session,
    *,
    account_id: str | None,
    match_text: str | None,
    amount: Decimal | None,
    amount_tolerance: Decimal,
    effective_from: date | None,
    effective_to: date | None,
    eligibility_action: EligibilityStatus,
    primary_category: PrimaryCategory | None,
    secondary_category: str | None,
) -> tuple[UserClassificationRule, bool, int]:
    """Upsert an exact bounded rule and replay existing interpretations.

    The returned count is the number of active interpretations governed by this
    rule, not a source-row or counterparty disclosure.
    """
    if not (account_id or match_text or amount is not None):
        raise ValueError("at least one rule scope is required")
    if eligibility_action == EligibilityStatus.ELIGIBLE and (primary_category is None) != (secondary_category is None):
        raise ValueError("eligible classification requires primary and secondary together")
    if amount_tolerance < 0:
        raise ValueError("amount tolerance must not be negative")
    if effective_to and effective_from and effective_to < effective_from:
        raise ValueError("effective_to must not precede effective_from")

    exact = (
        session.query(UserClassificationRule)
        .filter_by(
            rule_type="TEXT_AMOUNT_SCOPE",
            eligibility_action=eligibility_action.value,
            primary_category=primary_category.value if primary_category else None,
            secondary_category=secondary_category,
            account_id=account_id,
            match_text=match_text,
            amount=amount,
            amount_tolerance=amount_tolerance,
            effective_from=effective_from,
            effective_to=effective_to,
            status="ACTIVE",
        )
        .one_or_none()
    )
    created = exact is None
    rule = exact or UserClassificationRule(
        rule_type="TEXT_AMOUNT_SCOPE",
        eligibility_action=eligibility_action.value,
        primary_category=primary_category.value if primary_category else None,
        secondary_category=secondary_category,
        account_id=account_id,
        match_text=match_text,
        amount=amount,
        amount_tolerance=amount_tolerance,
        effective_from=effective_from,
        effective_to=effective_to,
        status="ACTIVE",
    )
    if created:
        session.add(rule)
        session.flush()
    interpretations = ClassificationResolver().replay(session)
    # OTHER is deliberately projection-free during source normalization.  A
    # bounded user rule that promotes it to eligible consumption needs the
    # same already-derived CNY projection as an ordinary consumption event.
    # This changes no source row or event fact and remains unavailable when
    # base currency resolution is absent.
    normalizer = EconomicEventNormalizer()
    for interpretation in interpretations:
        if (
            interpretation.rule_id == rule.id
            and interpretation.eligibility_status == EligibilityStatus.ELIGIBLE.value
            and interpretation.event.event_type == EventType.OTHER.value
        ):
            event = interpretation.event
            normalizer._append_projection_if_changed(
                session, event, "USER_RULE_PROMOTION", RuleSource.DESCRIPTION_RULE,
            )
    matched_count = session.query(ConsumptionInterpretation).filter_by(is_active=True, rule_id=rule.id).count()
    return rule, created, matched_count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply a bounded local consumption user rule offline.")
    parser.add_argument("--account-id")
    parser.add_argument("--match-text")
    parser.add_argument("--amount", type=_decimal)
    parser.add_argument("--amount-tolerance", type=_decimal, default=Decimal("0"))
    parser.add_argument("--effective-from", type=_date)
    parser.add_argument("--effective-to", type=_date)
    parser.add_argument("--eligibility", choices=[item.value for item in EligibilityStatus], default=EligibilityStatus.ELIGIBLE.value)
    parser.add_argument("--primary", choices=[item.value for item in PrimaryCategory])
    parser.add_argument("--secondary")
    parser.add_argument("--no-backup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if (args.primary is None) != (args.secondary is None):
            raise ValueError("--primary and --secondary must be supplied together")
        db_path = Path(database.DB_PATH)
        if args.no_backup:
            print("Backup: skipped by --no-backup")
        else:
            backup = _backup_database(db_path)
            print("Backup: created" if backup else "Backup: not needed")
        database.init_db()
        session = database.get_session()
        try:
            _rule, created, matched_count = apply_local_rule(
                session,
                account_id=args.account_id,
                match_text=args.match_text,
                amount=args.amount,
                amount_tolerance=args.amount_tolerance,
                effective_from=args.effective_from,
                effective_to=args.effective_to,
                eligibility_action=EligibilityStatus(args.eligibility),
                primary_category=PrimaryCategory(args.primary) if args.primary else None,
                secondary_category=args.secondary,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        print(f"Local consumption rule: {'created' if created else 'reused'}")
        print(f"Replay: active matched events: {matched_count}")
        return 0
    except (ValueError, argparse.ArgumentTypeError) as exc:
        print(f"Local rule failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
