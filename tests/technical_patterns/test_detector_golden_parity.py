from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.services.technical_patterns.detectors import GoldenParityComparator

from .test_detector_framework import _core_input, _proposal, _run


FIXTURE = Path(__file__).parent / "fixtures/tovest_tpg_v1_10_detector_framework_golden.json"


def test_mapped_tovest_framework_golden_matches_candidate_to_lifecycle_contract():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    core_input = _core_input()
    run, _, _ = _run(core_input, _proposal(core_input))

    result = GoldenParityComparator().compare(golden["case"]["expected"], run.results[0])

    assert golden["source_freeze"]["commit"] == "937edb62727f4d8c36d41b36e93521d077da20f9"
    assert golden["adaptation_contract"]["concrete_detector_parity"] == "deferred_to_stage_1c"
    assert result.passed is True
    assert result.differences == ()


def test_golden_comparator_applies_numeric_tolerance_and_exact_contract_fields():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = golden["case"]["expected"]
    within_tolerance = copy.deepcopy(expected)
    within_tolerance["candidate"]["geometry_facts"][0]["value"] += 5e-9
    outside_tolerance = copy.deepcopy(expected)
    outside_tolerance["candidate"]["geometry_facts"][0]["value"] += 1e-4

    comparator = GoldenParityComparator()

    assert comparator.compare(expected, within_tolerance).passed is True
    failure = comparator.compare(expected, outside_tolerance)
    assert failure.passed is False
    assert failure.differences[0].path == "$.candidate.geometry_facts[0].value"
    assert failure.differences[0].reason == "numeric_tolerance_exceeded"
