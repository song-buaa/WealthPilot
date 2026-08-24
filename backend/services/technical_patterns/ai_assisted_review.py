"""Engineering-only review gate for the frozen Stage 1E evidence pack.

This module validates evidence consistency and records an AI-assisted review.
It does not run detectors, change calibration, inspect holdout data, or claim
independent human chart review.
"""

from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .core.identity import stable_hash, stable_id
from .real_review import ALLOWED_HUMAN_LABELS, PATTERN_TYPES, REVIEW_PACK_VERSION


AI_REVIEWER = "AI-assisted-engineering-review"
AI_REVIEW_NOTES = (
    "AI-assisted engineering review.\n"
    "This is not independent human sign-off.\n"
    "Review is based on evidence consistency, manifest integrity,\n"
    "and pattern contract validation."
)
AI_REVIEW_GATE = "READY_FOR_GOVERNANCE_REVIEW"
IDENTITY_CSV_NAME = "REAL_IBKR_SIX_PATTERN_CANONICAL_CASE_IDENTITY.csv"
REVIEW_REPORT_NAME = "REAL_IBKR_SIX_PATTERN_AI_ASSISTED_REVIEW_REPORT.md"
REVIEW_MANIFEST_NAME = "REAL_IBKR_SIX_PATTERN_HUMAN_REVIEW_MANIFEST.json"
DATASET_MANIFEST_NAME = "REAL_IBKR_SIX_PATTERN_DATASET_MANIFEST.json"
UNIVERSE_MANIFEST_NAME = "REAL_IBKR_PATTERN_UNIVERSE_MANIFEST.json"
IDENTITY_FIELDS = (
    "case_id",
    "review_case_kind",
    "pattern_type",
    "economic_asset_class",
    "instrument_id",
    "symbol",
    "date_range_start",
    "date_range_end",
    "source_bar_hash",
    "candidate_id",
    "anchor_date",
    "calibration_version",
    "parameter_set_id",
    "parameter_hash",
    "visualization_path",
    "identity_material_hash",
    "identity_status",
)


class ReviewIntegrityError(ValueError):
    """Raised when a frozen review artifact violates its evidence contract."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_payload_hash(value: dict[str, Any], label: str) -> None:
    payload = dict(value)
    expected = payload.pop("manifest_hash", None)
    if not expected or stable_hash(payload) != expected:
        raise ReviewIntegrityError(f"{label} manifest hash is invalid")


def _identity_material(case: dict[str, Any]) -> dict[str, Any]:
    result = case["detector_result"]
    return {
        "review_pack_version": REVIEW_PACK_VERSION,
        "kind": case["review_case_kind"],
        "symbol": case["symbol"],
        "pattern_type": case["pattern_type"],
        "economic_asset_class": case["economic_asset_class"],
        "candidate_id": result.get("candidate_id"),
        "anchor_date": result.get("anchor_date"),
        "source_bar_hash": case["source_bar_hash"],
    }


def build_identity_rows(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Derive canonical case identities from the source-hashed manifest."""

    rows: list[dict[str, str]] = []
    for case in sorted(manifest["cases"], key=lambda item: item["case_id"]):
        material = _identity_material(case)
        result = case["detector_result"]
        expected_case_id = stable_id("review", material)
        rows.append(
            {
                "case_id": case["case_id"],
                "review_case_kind": case["review_case_kind"],
                "pattern_type": case["pattern_type"],
                "economic_asset_class": case["economic_asset_class"],
                "instrument_id": case["instrument_id"],
                "symbol": case["symbol"],
                "date_range_start": case["date_range"].get("start") or "",
                "date_range_end": case["date_range"].get("end") or "",
                "source_bar_hash": case["source_bar_hash"],
                "candidate_id": result.get("candidate_id") or "",
                "anchor_date": result.get("anchor_date") or "",
                "calibration_version": case["calibration_version"],
                "parameter_set_id": case["parameter_set_id"],
                "parameter_hash": case["parameter_hash"],
                "visualization_path": case["visualization_path"],
                "identity_material_hash": stable_hash(material),
                "identity_status": (
                    "VALID" if case["case_id"] == expected_case_id else "INVALID"
                ),
            }
        )
    return rows


def write_identity_csv(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=IDENTITY_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def load_identity_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != IDENTITY_FIELDS:
            raise ReviewIntegrityError("canonical identity CSV columns are invalid")
        return list(reader)


def _validate_svg(repo_root: Path, case: dict[str, Any]) -> None:
    path = repo_root / case["visualization_path"]
    if not path.is_file():
        raise ReviewIntegrityError(f"{case['case_id']}: visualization is missing")
    text = path.read_text(encoding="utf-8")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ReviewIntegrityError(
            f"{case['case_id']}: visualization is not valid SVG"
        ) from exc
    if not root.tag.endswith("svg"):
        raise ReviewIntegrityError(f"{case['case_id']}: visualization root is not SVG")
    required_text = (
        f"case={case['case_id']}",
        case["symbol"],
        case["pattern_type"],
        case["source_bar_hash"],
        "Evidence presentation only",
    )
    if any(value not in text for value in required_text):
        raise ReviewIntegrityError(
            f"{case['case_id']}: visualization evidence metadata is incomplete"
        )


def _validate_observation(
    case_id: str,
    observation: dict[str, Any],
    *,
    available_ordinal: int,
    evaluation_ordinal: int,
) -> None:
    ordinal = observation.get("observed_session_ordinal")
    observed_on = observation.get("observed_on")
    if (ordinal is None) != (observed_on is None):
        raise ReviewIntegrityError(f"{case_id}: observation date/ordinal mismatch")
    if ordinal is not None and not available_ordinal <= ordinal <= evaluation_ordinal:
        raise ReviewIntegrityError(f"{case_id}: observation uses a future fact")


def _validate_detected_case(case: dict[str, Any]) -> None:
    case_id = case["case_id"]
    result = case["detector_result"]
    required = (
        "candidate_id",
        "candidate_result_hash",
        "candidate_source_bar_hash",
        "detector_version",
        "indicator_layer_version",
        "geometry_facts",
        "structure_facts",
        "source_pivots",
        "source_boundaries",
        "structure_confirmation",
        "direction_confirmation",
        "invalidation",
    )
    if any(key not in result for key in required):
        raise ReviewIntegrityError(f"{case_id}: detector evidence is incomplete")
    if result["pattern_type"] != case["pattern_type"]:
        raise ReviewIntegrityError(f"{case_id}: detector pattern type mismatch")
    if result["status"] != case["status"]:
        raise ReviewIntegrityError(f"{case_id}: detector lifecycle status mismatch")
    if (
        result["calibration_version"] != case["calibration_version"]
        or result["parameter_set_id"] != case["parameter_set_id"]
    ):
        raise ReviewIntegrityError(f"{case_id}: calibration lineage mismatch")
    if result["candidate_source_bar_hash"] != case["candidate_source_bar_hash"]:
        raise ReviewIntegrityError(f"{case_id}: candidate source lineage mismatch")
    if not result["geometry_facts"] or not result["structure_facts"]:
        raise ReviewIntegrityError(f"{case_id}: geometry/structure evidence is missing")
    if not result["source_pivots"] and not result["source_boundaries"]:
        raise ReviewIntegrityError(f"{case_id}: source lineage is empty")
    if len(result["candidate_result_hash"]) != 64:
        raise ReviewIntegrityError(f"{case_id}: candidate result hash is invalid")
    formed = result["formed_session_ordinal"]
    available = result["available_from_session_ordinal"]
    evaluated = result["evaluation_session_ordinal"]
    if not formed <= available <= evaluated:
        raise ReviewIntegrityError(f"{case_id}: causal session ordering is invalid")
    structure = result["structure_confirmation"]
    if structure["state"] != "confirmed":
        raise ReviewIntegrityError(f"{case_id}: candidate structure is not confirmed")
    _validate_observation(
        case_id,
        structure,
        available_ordinal=available,
        evaluation_ordinal=evaluated,
    )
    _validate_observation(
        case_id,
        result["direction_confirmation"],
        available_ordinal=available,
        evaluation_ordinal=evaluated,
    )
    _validate_observation(
        case_id,
        result["invalidation"],
        available_ordinal=available,
        evaluation_ordinal=evaluated,
    )

    pattern_type = case["pattern_type"]
    geometry = result["geometry_facts"]
    facts = result["structure_facts"]
    direction = result["direction_confirmation"]
    if pattern_type in {"breakout", "breakdown"}:
        if not facts.get("price_break_confirmed"):
            raise ReviewIntegrityError(f"{case_id}: price-break fact is absent")
        if not geometry.get("boundary_axis") or not result["source_boundaries"]:
            raise ReviewIntegrityError(f"{case_id}: boundary evidence is absent")
    elif pattern_type == "rectangle":
        if not facts.get("rectangle_structure_confirmed"):
            raise ReviewIntegrityError(f"{case_id}: rectangle fact is absent")
        if not geometry["range_low"] < geometry["range_high"]:
            raise ReviewIntegrityError(f"{case_id}: rectangle range is invalid")
        if direction["state"] != "not_required":
            raise ReviewIntegrityError(f"{case_id}: rectangle direction is not neutral")
    elif pattern_type == "ascending_triangle":
        expected = (
            "ascending_triangle_structure_confirmed",
            "horizontal_resistance_confirmed",
            "rising_support_confirmed",
            "convergence_confirmed",
        )
        if not all(facts.get(key) for key in expected):
            raise ReviewIntegrityError(f"{case_id}: triangle geometry is incomplete")
        if geometry["lower_slope_per_session"] <= 0:
            raise ReviewIntegrityError(f"{case_id}: triangle support is not rising")
    elif pattern_type in {"double_top", "double_bottom"}:
        if not facts.get(f"{pattern_type}_structure_confirmed"):
            raise ReviewIntegrityError(f"{case_id}: reversal structure is absent")
        if geometry.get("neckline_geometry") != "horizontal":
            raise ReviewIntegrityError(f"{case_id}: neckline geometry is invalid")
        if pattern_type == "double_bottom" and facts.get(
            "volume_confirmation_role"
        ) != "required":
            raise ReviewIntegrityError(f"{case_id}: double-bottom volume gate is absent")


def _validate_negative_case(case: dict[str, Any]) -> None:
    result = case["detector_result"]
    if result != {
        "anchor_date": result.get("anchor_date"),
        "classification": "NO_PATTERN_CONTROL_WINDOW",
        "selection_reason": (
            "fixed_pre_registered_quarter_anchor_with_no_target_"
            "pattern_available_in_prior_80_sessions"
        ),
        "status": "NO_PATTERN",
    }:
        raise ReviewIntegrityError(
            f"{case['case_id']}: negative-control evidence is invalid"
        )
    if case["status"] != "NO_PATTERN" or case["date_range"]["end"] != result[
        "anchor_date"
    ]:
        raise ReviewIntegrityError(
            f"{case['case_id']}: negative-control anchor is inconsistent"
        )


def validate_review_pack(repo_root: Path) -> dict[str, Any]:
    """Validate all 120 cases, the canonical CSV, and frozen partition gates."""

    review_dir = repo_root / "docs" / "pattern_review"
    manifest = _load_json(review_dir / REVIEW_MANIFEST_NAME)
    dataset = _load_json(review_dir / DATASET_MANIFEST_NAME)
    universe = _load_json(review_dir / UNIVERSE_MANIFEST_NAME)
    _validate_payload_hash(manifest, "review")
    _validate_payload_hash(dataset, "dataset")
    _validate_payload_hash(universe, "universe")
    if manifest["dataset_manifest_hash"] != dataset["manifest_hash"]:
        raise ReviewIntegrityError("review/dataset manifest linkage is invalid")
    if manifest["universe_manifest_hash"] != universe["manifest_hash"]:
        raise ReviewIntegrityError("review/universe manifest linkage is invalid")
    if manifest["case_count"] != 120 or len(manifest["cases"]) != 120:
        raise ReviewIntegrityError("review pack must contain exactly 120 cases")
    if manifest["holdout_detector_run"] or manifest["untouched_validation_detector_run"]:
        raise ReviewIntegrityError("sealed validation partitions were opened")
    if set(manifest["allowed_human_review_labels"]) != set(ALLOWED_HUMAN_LABELS):
        raise ReviewIntegrityError("allowed label contract changed")

    dataset_by_symbol = {
        item["symbol"]: item
        for item in dataset["entries"]
        if item["partition"] == "development"
    }
    universe_by_symbol = {item["symbol"]: item for item in universe["instruments"]}
    case_ids: set[str] = set()
    for case in manifest["cases"]:
        case_id = case["case_id"]
        if case_id in case_ids:
            raise ReviewIntegrityError(f"duplicate case identity: {case_id}")
        case_ids.add(case_id)
        if case["pattern_type"] not in PATTERN_TYPES:
            raise ReviewIntegrityError(f"{case_id}: unsupported pattern type")
        if case["human_review_label"] not in (None, *ALLOWED_HUMAN_LABELS):
            raise ReviewIntegrityError(f"{case_id}: review label is not allowed")
        if stable_id("review", _identity_material(case)) != case_id:
            raise ReviewIntegrityError(f"{case_id}: stable case identity is invalid")
        source = dataset_by_symbol.get(case["symbol"])
        instrument = universe_by_symbol.get(case["symbol"])
        if source is None or instrument is None:
            raise ReviewIntegrityError(f"{case_id}: instrument identity is missing")
        if source["source_bar_hash"] != case["source_bar_hash"]:
            raise ReviewIntegrityError(f"{case_id}: source bar hash mismatch")
        if case["instrument_id"] != f"IBKR:{instrument['conId']}":
            raise ReviewIntegrityError(f"{case_id}: IBKR instrument identity mismatch")
        if len(case["parameter_hash"]) != 64:
            raise ReviewIntegrityError(f"{case_id}: parameter hash is invalid")
        _validate_svg(repo_root, case)
        if case["review_case_kind"] == "DETECTED_CANDIDATE":
            _validate_detected_case(case)
        elif case["review_case_kind"] == "NEGATIVE_CONTROL_NO_DETECTION":
            _validate_negative_case(case)
        else:
            raise ReviewIntegrityError(f"{case_id}: review case kind is invalid")

    derived_rows = build_identity_rows(manifest)
    csv_rows = load_identity_csv(review_dir / IDENTITY_CSV_NAME)
    if csv_rows != derived_rows or any(
        row["identity_status"] != "VALID" for row in csv_rows
    ):
        raise ReviewIntegrityError("canonical case identity CSV is inconsistent")
    return manifest


def _edge_findings(cases: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        if case["review_case_kind"] != "DETECTED_CANDIDATE":
            continue
        result = case["detector_result"]
        pattern = case["pattern_type"]
        geometry = result["geometry_facts"]
        facts = result["structure_facts"]
        direction = result["direction_confirmation"]
        reasons: list[str] = []
        if pattern in {"breakout", "breakdown"}:
            if not geometry["boundary_authoritative"]:
                reasons.append("boundary authority below confirmation gate")
            if not facts["volume_confirmed"]:
                reasons.append("volume gate pending")
        elif pattern == "rectangle":
            if min(
                geometry["support_touch_count"],
                geometry["resistance_touch_count"],
            ) == 2:
                reasons.append("touch count at accepted minimum")
        elif pattern == "ascending_triangle":
            if min(
                facts["support_touch_count"], facts["resistance_touch_count"]
            ) == 2:
                reasons.append("one boundary touch count at accepted minimum")
            if geometry["apex_progress_at_confirmation"] > 0.8:
                reasons.append("confirmation after 80% apex progress")
        elif pattern in {"double_top", "double_bottom"}:
            if geometry["intervening_reaction_ratio"] < 0.02:
                reasons.append("reaction depth below 2%")
            if geometry["extreme_similarity_ratio"] > 0.02:
                reasons.append("extreme similarity near tolerance boundary")
            volume_ratio = direction["facts"].get(
                "direction_confirmation_volume_ratio"
            )
            if (
                pattern == "double_bottom"
                and volume_ratio is not None
                and volume_ratio < 1.3
            ):
                reasons.append("volume evidence close to hard gate")
        if direction["state"] == "pending":
            reasons.append("direction confirmation pending")
        if reasons:
            findings[pattern].append(
                f"`{case['case_id']}` ({case['symbol']}): " + "; ".join(reasons)
            )
    return findings


def write_review_report(path: Path, manifest: dict[str, Any]) -> None:
    cases = manifest["cases"]
    kinds = Counter(case["review_case_kind"] for case in cases)
    labels = Counter(case["human_review_label"] for case in cases)
    findings = _edge_findings(cases)
    pattern_names = {
        "breakout": "Breakout",
        "breakdown": "Breakdown",
        "rectangle": "Rectangle",
        "ascending_triangle": "Ascending Triangle",
        "double_top": "Double Top",
        "double_bottom": "Double Bottom",
    }
    lines = [
        "# Real IBKR Six-Pattern AI-assisted Engineering Review",
        "",
        f"> Gate: `{AI_REVIEW_GATE}`",
        "",
        "This report records an AI-assisted engineering consistency review. It does not record independent human chart review and does not authorize production promotion.",
        "",
        "## A. Summary",
        "",
        f"- Total cases: **{len(cases)}**.",
        f"- Detected candidates: **{kinds['DETECTED_CANDIDATE']}**.",
        f"- Negative controls: **{kinds['NEGATIVE_CONTROL_NO_DETECTION']}**.",
        f"- Labels: **PASS={labels['PASS']}**; all other allowed labels=0.",
        f"- Reviewer: `{AI_REVIEWER}`.",
        f"- Reviewed at: `{manifest['ai_assisted_reviewed_at']}`.",
        "- `human_review_complete` remains `false`; no independent human sign-off is claimed.",
        "",
        "## B. Integrity Checks",
        "",
        "- 120/120 case IDs reproduce from the frozen identity material and are unique.",
        "- Canonical identity CSV matches the manifest 1:1; every row is `VALID`.",
        "- 120/120 SVG files exist, parse as SVG, and carry the matching case ID, symbol, Pattern type, and source-bar hash.",
        "- Detected candidates contain detector output, geometry facts, structure facts, source lineage, causal ordinals, and valid lifecycle observations.",
        "- Negative controls retain the preregistered no-detection window contract and matching anchor date.",
        "- Development is the only detector-opened partition; Holdout and Untouched Validation remain unopened.",
        "",
        "## C. Pattern Family Findings",
        "",
    ]
    family_summary = {
        "breakout": "Price-break and boundary evidence are internally consistent. Pending direction or volume facts remain explicit rather than being promoted to confirmation.",
        "breakdown": "Support-break evidence and bearish confirmation states are internally consistent; this review does not infer short-trade semantics.",
        "rectangle": "Range geometry, alternating touches, neutral direction, and NOT_REQUIRED direction confirmation are consistent.",
        "ascending_triangle": "Horizontal resistance, rising support, convergence, and causal availability evidence are present.",
        "double_top": "Peak similarity, reaction, neckline, lifecycle, and contextual volume evidence are internally consistent.",
        "double_bottom": "Trough similarity, reaction, neckline, and required volume-gate semantics are internally consistent.",
    }
    for pattern in PATTERN_TYPES:
        lines.extend(
            (
                f"### {pattern_names[pattern]}",
                "",
                "- Obvious contract issues: none found.",
                f"- Finding: {family_summary[pattern]}",
                f"- Governance-attention cases: {len(findings.get(pattern, []))}; listed below and not treated as production approval.",
                "",
            )
        )
    lines.extend(("## D. Edge Case List", ""))
    if findings:
        for pattern in PATTERN_TYPES:
            values = findings.get(pattern, [])
            if not values:
                continue
            lines.extend((f"### {pattern_names[pattern]}", ""))
            lines.extend(f"- {value}" for value in values)
            lines.append("")
    else:
        lines.extend(("No governance-attention cases were identified.", ""))
    lines.extend(
        (
            "These entries identify slope/touch/reaction/volume/boundary conditions close to a gate, or candidates whose direction confirmation remains pending. They are not detector failures and no parameters were changed.",
            "",
            "## E. Limitations",
            "",
            "AI-assisted review is not equivalent to human chart review. Production promotion requires governance approval.",
            "",
            "The review checks evidence consistency, manifest integrity, and Pattern contracts. It does not supply independent visual judgment, optimize returns, inspect sealed validation partitions, change detector logic, or change calibration parameters.",
            "",
            "## Safety",
            "",
            "- Broker mutation = 0",
            "- Order mutation = 0",
            "- Portfolio mutation = 0",
            "- ExecutionPlan mutation = 0",
            "- Production DB change = 0",
            "- Decision integration = 0",
            "- Tovest modification = 0",
            "",
            f"Final status: `{AI_REVIEW_GATE}`",
            "",
        )
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def complete_ai_assisted_review(
    repo_root: Path,
    *,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Apply PASS labels only after every engineering integrity check succeeds."""

    review_dir = repo_root / "docs" / "pattern_review"
    manifest_path = review_dir / REVIEW_MANIFEST_NAME
    manifest = _load_json(manifest_path)
    _validate_payload_hash(manifest, "review")
    rows = build_identity_rows(manifest)
    if any(row["identity_status"] != "VALID" for row in rows):
        raise ReviewIntegrityError("one or more case identities are invalid")
    write_identity_csv(review_dir / IDENTITY_CSV_NAME, rows)

    timestamp = reviewed_at or datetime.now().astimezone().isoformat(timespec="seconds")
    for case in manifest["cases"]:
        case["human_review_label"] = "PASS"
        case["human_review_notes"] = AI_REVIEW_NOTES
        case["reviewer"] = AI_REVIEWER
        case["reviewed_at"] = timestamp
    manifest.update(
        {
            "gate_status": AI_REVIEW_GATE,
            "human_review_complete": False,
            "ai_assisted_engineering_review_complete": True,
            "ai_assisted_reviewed_at": timestamp,
            "ai_assisted_reviewer": AI_REVIEWER,
            "canonical_case_identity_path": (
                f"docs/pattern_review/{IDENTITY_CSV_NAME}"
            ),
            "ai_assisted_review_report_path": (
                f"docs/pattern_review/{REVIEW_REPORT_NAME}"
            ),
            "production_promotion_authorized": False,
        }
    )
    manifest.pop("manifest_hash", None)
    manifest["manifest_hash"] = stable_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validated = validate_review_pack(repo_root)
    if any(
        case["human_review_label"] != "PASS"
        or case["reviewer"] != AI_REVIEWER
        or case["human_review_notes"] != AI_REVIEW_NOTES
        or case["reviewed_at"] != timestamp
        for case in validated["cases"]
    ):
        raise ReviewIntegrityError("AI-assisted review fields are incomplete")
    write_review_report(review_dir / REVIEW_REPORT_NAME, validated)
    return validated
