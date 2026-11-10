"""Read-only adapter for the original Allocation freeze and owner ETF evidence."""

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from app.shared_sqlite import private_file
from backend.services.instruments.etf_holdings import ETFHoldings

from .concentration import analyze_concentration
from .contracts import Contract, digest
from .parser import PolicyError

MAX_EVIDENCE_BYTES = 512 * 1024
_WIRE = TypeAdapter(dict)


class _OwnerEvidence(Contract):
    schema_version: Literal["wealthpilot.etf-evidence.v1"]
    portfolio_id: int
    records: tuple[ETFHoldings, ...] = Field(max_length=3)


def _read_etf_evidence(portfolio_id):
    path = os.environ.get("WEALTHPILOT_ETF_EVIDENCE_FILE")
    expected_sha256 = os.environ.get("WEALTHPILOT_ETF_EVIDENCE_SHA256")
    if not path and not expected_sha256:
        return {}, {}, "ETF_OWNER_EVIDENCE_NOT_CONFIGURED"
    try:
        with private_file(Path(path or "")).open("rb") as handle:
            data = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(data) > MAX_EVIDENCE_BYTES or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("ETF_EVIDENCE_PIN_MISMATCH")
        manifest = _OwnerEvidence.model_validate_json(data)
        records = {record.symbol: record for record in manifest.records}
        if manifest.portfolio_id != portfolio_id or len(records) != len(manifest.records):
            raise ValueError("ETF_EVIDENCE_SCOPE_MISMATCH")
    except (OSError, ValueError, ValidationError):
        return {}, {}, "ETF_OWNER_EVIDENCE_UNAVAILABLE"
    # The entire independently pinned owner manifest binds each normalized row set.
    return records, {symbol: digest(record) for symbol, record in records.items()}, None


def allocation_concentration(view, *, projection, session, at, classified_assets):
    records, pins, issue = _read_etf_evidence(view["portfolio_id"])
    try:
        result = analyze_concentration(view, projection=projection, session=session, at=at,
                                       etf_evidence=records, evidence_pins=pins,
                                       classified_assets=classified_assets)
    except PolicyError as exc:
        # Preserve B2/B3 success when a legacy or unknown policy rule cannot be
        # evaluated by C. Unexpected failures keep the existing 500 boundary.
        if str(exc) not in {"CONCENTRATION_REVIEW_RULE_UNAVAILABLE", "CONCENTRATION_BTC_RULES_UNAVAILABLE",
                            "CONCENTRATION_POLICY_SUBJECT_UNMAPPED", "CONCENTRATION_POLICY_BINDING_MISMATCH"}:
            raise
        return {"status": "UNKNOWN", "issues": [str(exc)], "execution_authorized": False}
    if issue:
        result["issues"].append(issue)
    return _WIRE.dump_python(result, mode="json")
