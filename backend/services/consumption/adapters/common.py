"""Shared deterministic parsing helpers. No persistence, network, or AI."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re

from backend.services.consumption.contracts import FieldAvailability

MONEY_RE = re.compile(r"(?<![\d.])([+-]?\d[\d,]*(?:\.\d{1,2})?)(?![\d.])")
FULL_DATE_RE = re.compile(r"(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})")
MONTH_DAY_RE = re.compile(r"(\d{1,2})/(\d{1,2})")


def normalized_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "").replace("￥", "").replace("¥", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"invalid monetary field: {value!r}") from exc


def find_money_values(value: str) -> list[Decimal]:
    return [parse_decimal(match.group(1)) for match in MONEY_RE.finditer(value)]


def parse_full_date(value: str) -> date | None:
    match = FULL_DATE_RE.search(value)
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def parse_month_day(value: str, *, anchor_year: int | None) -> date | None:
    match = MONTH_DAY_RE.fullmatch(value.strip())
    return date(anchor_year, int(match.group(1)), int(match.group(2))) if match and anchor_year else None


def parse_month_day_in_period(value: str, *, period_start: date | None, period_end: date | None) -> date | None:
    """Resolve an MM/DD source date against an explicit statement period.

    This prevents a December row in a December–January statement from being
    incorrectly assigned to the period-end year. If no matching period year can
    be proven, callers should retain the field as ambiguous instead of guessing.
    """
    match = MONTH_DAY_RE.fullmatch(value.strip())
    if not match or not period_start or not period_end:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    candidates = []
    for year in range(period_start.year, period_end.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if period_start <= candidate <= period_end:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def parse_month_day_with_statement_anchor(value: str, *, anchor: date | None) -> date | None:
    """Resolve MM/DD with a dated statement anchor when no explicit period exists."""
    match = MONTH_DAY_RE.fullmatch(value.strip())
    if not match or not anchor:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    year = anchor.year - 1 if month > anchor.month else anchor.year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_labeled_period(text: str) -> tuple[date | None, date | None, FieldAvailability]:
    """Accept a period only when an explicit source label accompanies it."""
    for line in text.splitlines():
        if "账单周期" not in line and "账单期间" not in line and "查询期间" not in line:
            continue
        return extract_period(line)
    return None, None, FieldAvailability.SOURCE_UNAVAILABLE


def extract_period(text: str) -> tuple[date | None, date | None, FieldAvailability]:
    dates = [date(int(y), int(m), int(d)) for y, m, d in FULL_DATE_RE.findall(text)]
    if len(dates) >= 2:
        return dates[0], dates[1], FieldAvailability.AVAILABLE
    if dates:
        return dates[0], None, FieldAvailability.AMBIGUOUS
    return None, None, FieldAvailability.SOURCE_UNAVAILABLE


def mask_identity(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", "", value)
    digits = re.sub(r"\D", "", compact)
    if len(digits) >= 4:
        return f"****{digits[-4:]}"
    return compact[-8:] if "*" in compact else None


def extract_masked_identity(text: str) -> str | None:
    for candidate in re.findall(r"(?:卡号|账号|尾号|账户)[：:\s]*([0-9*Xx\- ]{4,})", text):
        if masked := mask_identity(candidate):
            return masked
    return None


def unavailable_fields(*names: str) -> dict[str, FieldAvailability]:
    return {name: FieldAvailability.SOURCE_UNAVAILABLE for name in names}
