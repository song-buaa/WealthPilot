"""Isolated private configuration tests; no actual user confirmation or DB writes."""
from datetime import datetime, timezone
import json

import pytest

from backend.services.allocation_scenarios import target_context
from backend.services.stage_targets import StageTargetStore, read_target_context

AT = datetime(2026, 10, 22, tzinfo=timezone.utc)


def proposal(**changes):
    value = dict(version="synthetic-v1", stage="synthetic-transition",
        effective_from="2026-10-20T00:00:00+00:00", valid_until="2026-12-20T00:00:00+00:00",
        issuers=[dict(issuer_id="SYNTHETIC", target_weight=.2)])
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "private-targets.json"
    monkeypatch.setenv("WEALTHPILOT_STAGE_TARGETS_PATH", str(path))
    return StageTargetStore(path)


def candidate(store, **changes):
    return store.create_candidate(proposal(**changes), portfolio_id=1, source="synthetic-prd", at=AT)


def activate(store, record, **changes):
    kwargs = dict(portfolio_id=1, expected_digest=record["digest"], operator="test-operator",
        reference="isolated fixture confirmation", explicit_confirmation=True, at=AT)
    kwargs.update(changes)
    return store.activate(record["content"]["version"], **kwargs)


def test_candidate_never_confirms_and_client_flag_digest_cannot_activate(store):
    record = candidate(store)
    assert store.resolve(1, at=AT) is None
    raw = {**proposal(), "confirmed_at": proposal()["effective_from"], "confirmed_by_user": True}
    raw.pop("effective_from")
    context = target_context({"portfolio_id": 1}, None, [raw], {}, {}, at=AT)
    assert context["confirmed_stage_targets"] is None
    assert context["target_confirmation_status"] == "unverified"
    with pytest.raises(ValueError, match="EXPLICIT_LOCAL"):
        activate(store, record, explicit_confirmation=False)
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        activate(store, record, expected_digest="forged")
    assert store.resolve(1, at=AT) is None


def test_server_confirmation_does_not_need_client_targets_and_is_portfolio_bound(store):
    record = candidate(store)
    activate(store, record)
    context = target_context({"portfolio_id": 1}, None, [], {}, {}, at=AT)
    trusted = context["confirmed_stage_targets"]
    assert trusted["basis"] == "server_stage_target_store"
    assert trusted["digest"] == record["digest"]
    assert trusted["confirmation_source"] == "local_operator_cli"
    assert store.resolve(2, at=AT) is None
    assert store.resolve(None, at=AT) is None
    assert store.resolve(1, at=datetime(2027, 1, 1, tzinfo=timezone.utc)) is None
    assert store.resolve(1, at=datetime(2026, 10, 21, tzinfo=timezone.utc)) is None


def test_versions_immutable_lineage_and_revocation(store):
    first = candidate(store)
    activate(store, first)
    with pytest.raises(ValueError, match="IMMUTABLE"):
        candidate(store)
    second = candidate(store, version="synthetic-v2")
    with pytest.raises(ValueError, match="LINEAGE"):
        activate(store, second)
    third = candidate(store, version="synthetic-v3", supersedes="synthetic-v1")
    activate(store, third)
    assert store.resolve(1, at=AT)["version"] == "synthetic-v3"
    store.deactivate("synthetic-v3", portfolio_id=1, reason="fixture revocation")
    assert store.resolve(1, at=AT) is None


def test_corruption_and_unsafe_permissions_fail_closed(store):
    record = candidate(store)
    activate(store, record)
    document = json.loads(store.path.read_text())
    document["records"][0]["content"]["issuers"][0]["target_weight"] = .9
    store.path.write_text(json.dumps(document))
    context = read_target_context(1, at=AT)
    assert context["confirmed"] is None
    assert context["issues"] == ["STAGE_TARGET_STORE_INVALID"]
    store.path.chmod(0o666)
    with pytest.raises(ValueError, match="OWNER_OR_MODE"):
        store.resolve(1, at=AT)


def test_server_candidate_available_without_request_and_discipline_still_blocks(store):
    candidate(store)
    context = target_context({"portfolio_id": 1}, "SYNTHETIC", [], {"max_single_position": .1}, {}, at=AT)
    assert context["proposed_stage_targets"]["basis"] == "server_candidate_unconfirmed"
    assert context["confirmed_stage_targets"] is None
    assert context["warnings"] == ["TARGET_DISCIPLINE_CONCENTRATION_CONFLICT:SYNTHETIC"]


def test_activation_cannot_bless_expired_candidate(store):
    record = candidate(store)
    with pytest.raises(ValueError, match="EXPIRED"):
        activate(store, record, at=datetime(2027, 1, 1, tzinfo=timezone.utc))


def test_no_server_path_means_no_authority(monkeypatch):
    monkeypatch.delenv("WEALTHPILOT_STAGE_TARGETS_PATH", raising=False)
    assert read_target_context(1, at=AT) == dict(confirmed=None, candidate=None, issues=[])


def test_client_cannot_replace_active_server_target(store):
    record = candidate(store)
    activate(store, record)
    raw = proposal(version="forged-v9", confirmed_by_user=True,
                   confirmed_at="2026-10-20T00:00:00+00:00")
    raw.pop("effective_from")
    context = target_context({"portfolio_id": 1}, None, [raw], {}, {}, at=AT)
    assert context["confirmed_stage_targets"]["version"] == "synthetic-v1"
    assert context["proposed_stage_targets"]["version"] == "forged-v9"
    assert "TARGET_REQUEST_DIFFERS_FROM_SERVER_CONFIRMED" in context["warnings"]


def test_candidate_phase_validation_and_broker_policy(store):
    raw = proposal(funding_policy={"broker:tiger": "offshore_fixed_income", "broker:guojin": "repay_cny_loan"})
    raw["issuers"][0]["phases"] = [.15, .1]
    saved = store.create_candidate(raw, portfolio_id=1, source="synthetic", at=AT)
    assert saved["content"]["funding_policy"]["broker:guojin"] == "repay_cny_loan"
    raw["version"] = "invalid-phases"
    raw["issuers"][0]["phases"] = [.3]
    with pytest.raises(ValueError, match="PHASES_MUST_DECREASE"):
        store.create_candidate(raw, portfolio_id=1, source="synthetic", at=AT)


def test_reads_do_not_mutate_file_and_symlink_is_rejected(store, tmp_path):
    record = candidate(store)
    activate(store, record)
    before = store.path.read_bytes()
    for _ in range(2):
        assert read_target_context(1, at=AT)["confirmed"]
    assert store.path.read_bytes() == before
    link = tmp_path / "unsafe-link.json"
    link.symlink_to(store.path)
    with pytest.raises(ValueError, match="OWNER_OR_MODE"):
        StageTargetStore(link).resolve(1, at=AT)


def test_cli_candidate_and_exact_confirmation_are_separate(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    raw = proposal()
    raw["effective_from"] = "2020-01-01T00:00:00+00:00"
    raw["valid_until"] = "2099-01-01T00:00:00+00:00"
    content = tmp_path / "proposal.json"
    content.write_text(json.dumps(raw))
    path = tmp_path / "private.json"
    command = [sys.executable, str(root / "scripts/stage_targets.py"), "--store", str(path), "--portfolio-id", "1"]
    run = subprocess.run(command + ["candidate", "--content", str(content), "--source", "synthetic"],
                         capture_output=True, text=True, check=True)
    record = json.loads(run.stdout)
    assert record["status"] == "candidate"
    assert StageTargetStore(path).resolve(1) is None
    args = ["activate", "--version", record["content"]["version"], "--digest", record["digest"],
            "--operator", "fixture-operator", "--confirmation-reference", "fixture only"]
    refused = subprocess.run(command + args, capture_output=True, text=True)
    assert refused.returncode != 0
    assert StageTargetStore(path).resolve(1) is None
    subprocess.run(command + args + ["--confirm-stage-target", "I_CONFIRM_THIS_VERSION_AND_DIGEST"],
                   capture_output=True, text=True, check=True)
    assert StageTargetStore(path).resolve(1)["confirmation_source"] == "local_operator_cli"


def test_confirmed_target_cannot_override_seed_market_or_asset_veto(store):
    from tests.test_v42_exit_plan import setup_case
    from backend.services.position_analysis import prepare
    view, executed, planner, raw = setup_case("LI:US")
    view["portfolio_id"] = 1
    record = store.create_candidate(raw, portfolio_id=1, source="synthetic", at=AT)
    activate(store, record)
    assessment = prepare(planner, executed, asset_view=view, at=AT)
    assert assessment["allocation_context"]["target_confirmation_status"] == "confirmed"
    assert not assessment["qualification"]["L3"]["allowed"]
    assert "CONFIRMED_STAGE_TARGETS_REQUIRED" not in assessment["qualification"]["L3"]["blockers"]
    assert not assessment["qualification"]["L4"]["allowed"]
    assert not view["data_quality"]["can_propose_rebalance"]



@pytest.mark.parametrize("bad", [[], {"schema_version":"wealthpilot.stage-targets.v1", "records":[None]},
    {"schema_version":"wealthpilot.stage-targets.v1", "records":[{"content":"wrong"}]}])
def test_malformed_configuration_degrades_explicitly(store, bad):
    store.path.write_text(json.dumps(bad))
    store.path.chmod(0o600)
    assert read_target_context(1, at=AT) == dict(confirmed=None, candidate=None, issues=["STAGE_TARGET_STORE_INVALID"])



def test_configuration_rejects_boolean_portfolio_id(store):
    candidate(store)
    document = json.loads(store.path.read_text())
    document["records"][0]["portfolio_id"] = True
    store.path.write_text(json.dumps(document))
    assert read_target_context(1, at=AT)["issues"] == ["STAGE_TARGET_STORE_INVALID"]
