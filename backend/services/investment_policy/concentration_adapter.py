"""Read-only adapter for the original Allocation freeze and owner ETF evidence."""

import hashlib
import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from app.shared_sqlite import private_file

from .concentration import analyze_concentration
from .contracts import digest
from .etf_release import MAX_BYTES, OwnerEvidence, consumer_directory, read_observations, read_release
from .parser import PolicyError

_WIRE = TypeAdapter(dict)


def _read_etf_evidence(portfolio_id, *, at=None):
    path = os.environ.get("WEALTHPILOT_ETF_EVIDENCE_FILE")
    expected_sha256 = os.environ.get("WEALTHPILOT_ETF_EVIDENCE_SHA256")
    root = os.environ.get("WEALTHPILOT_ETF_RELEASE_DIRECTORY")
    public_key = os.environ.get("WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY")
    owner_uid = os.environ.get("WEALTHPILOT_ETF_RELEASE_OWNER_UID")
    if root or public_key or owner_uid:
        # An explicitly configured dynamic source fails closed if it cannot be verified.
        try:
            if (not root or not public_key or not owner_uid or not owner_uid.isascii()
                    or not owner_uid.isdecimal() or path or expected_sha256 or at is None):
                raise ValueError("ETF_RELEASE_CONFIGURATION_INVALID")
            directory = consumer_directory(root, int(owner_uid))
            receipt, manifest = read_release(directory, bytes.fromhex(public_key), portfolio_id, at)
        except (OSError, ValueError):
            return {}, {}, "ETF_OWNER_EVIDENCE_UNAVAILABLE", {}, {}
        records = {r.symbol: r for r in manifest.records}
        try:
            observations, issue = read_observations(directory, receipt, bytes.fromhex(public_key)), None
        except (OSError, ValueError):
            observations, issue = {}, "ETF_OBSERVATION_UNAVAILABLE"
        return records, {s: digest(r) for s, r in records.items()}, issue, receipt.close_caps_usd, observations
    if not path and not expected_sha256:
        return {}, {}, "ETF_OWNER_EVIDENCE_NOT_CONFIGURED", {}, {}
    try:
        with private_file(Path(path or "")).open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError("ETF_EVIDENCE_PIN_MISMATCH")
        manifest = OwnerEvidence.model_validate_json(data)
        records = {record.symbol: record for record in manifest.records}
        if manifest.portfolio_id != portfolio_id or len(records) != len(manifest.records):
            raise ValueError("ETF_EVIDENCE_SCOPE_MISMATCH")
    except (OSError, ValueError, ValidationError):
        return {}, {}, "ETF_OWNER_EVIDENCE_UNAVAILABLE", {}, {}
    # The entire independently pinned owner manifest binds each normalized row set.
    return records, {symbol: digest(record) for symbol, record in records.items()}, None, {}, {}


def allocation_concentration(view, *, projection, session, at, classified_assets, etf_inputs=None):
    if etf_inputs is None:
        etf_inputs = _read_etf_evidence(view["portfolio_id"], at=at)
    records, pins, issue, close_caps, observations = etf_inputs
    try:
        result = analyze_concentration(view, projection=projection, session=session, at=at,
                                       etf_evidence=records, evidence_pins=pins,
                                       classified_assets=classified_assets, close_caps_usd=close_caps, observations=observations)
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
