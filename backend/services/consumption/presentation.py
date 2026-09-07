"""User-facing labels for consumption data without changing source facts."""
from __future__ import annotations

import re


_ACCOUNT_LABELS = {
    ("CCB", "CREDIT_CARD"): "建行信用卡",
    ("CMB", "CREDIT_CARD"): "招行信用卡",
    ("CMB", "DEBIT_CARD"): "招行借记卡",
}


def account_display_label(
    value: str | None, institution: str, account_type: str, *, include_mask: bool = True,
) -> str:
    """Localize known bank account labels while retaining only a safe card mask."""
    base = _ACCOUNT_LABELS.get((institution, account_type))
    if base is None:
        base = " ".join((value or f"{institution}账户").split())[:40] or f"{institution}账户"
    if not include_mask or not value:
        return base
    safe_value = re.sub(r"(?:\*{2,})?\d{2,}", "****", " ".join(value.split())[:40])
    return f"{base} ****" if "****" in safe_value else base
