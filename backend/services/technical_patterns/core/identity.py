"""Stable, database-independent identities for Pattern Core facts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


IDENTITY_VERSION = "WP-PATTERN-CORE-IDENTITY-2.0"
PATTERN_CANDIDATE_IDENTITY_VERSION = "wp-pattern-candidate-identity-v2"


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("identity material cannot contain non-finite Decimal")
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def canonicalize(value: Any) -> Any:
    """Convert supported values into a deterministic JSON-safe structure."""

    if dataclasses.is_dataclass(value):
        return canonicalize(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [canonicalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("identity material cannot contain non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported identity material: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, material: Any, *, length: int = 20) -> str:
    if not prefix or length < 8:
        raise ValueError("stable identity requires a prefix and at least eight hash characters")
    return f"{prefix}_{stable_hash(material)[:length]}"
