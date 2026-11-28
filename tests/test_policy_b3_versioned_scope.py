"""Offline formal Publisher and B3 HTTP goldens on fictional policy inputs."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from backend.services.investment_policy import runtime
from backend.services.investment_policy.allocation_hierarchy import formal_strategic_review
from backend.services.investment_policy.contracts import Snapshot, SnapshotV2, digest, parse_snapshot
from backend.services.investment_policy.formal_publisher import PolicyPublisher
from backend.services.investment_policy.parser import PolicyError, derive
from backend.services.investment_policy.publisher import publish_synthetic
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import PolicyStore
from tests.test_b3_allocation import B3_POLICY, b3, client, clock, read, source  # noqa: F401
from tests.test_investable_assets import AT, STAMP
from tests.test_investment_policy import PHILOSOPHY
from tests.test_policy_publisher import approval, installation

BLOCK = """B3节点合同：wp.policy.v2
B3节点 growth_and_alternatives：70%
B3节点 stable_pool：30%
B3节点复核单位：PORTFOLIO_PER_SNAPSHOT
B3节点现金范围：QUALIFIED_TEMPORARY_INVESTMENT_CASH_IN_STABLE_POOL
B3节点产品决定：https://github.com/song-buaa/WealthPilot/issues/111#issuecomment-6814354290
B3节点内容批准：DRAFT_CONTENT
"""
APPROVAL = "APPROVED_CONTENT | 2021-01-01 | TEST_ONLY_CONTENT_APPROVAL"


def sources(block):
    philosophy = PHILOSOPHY.replace(b"2030-12-01", b"2020-01-01")
    policy = B3_POLICY.replace(b"2030-12-01", b"2020-01-01")
    if block:
        policy = policy.replace("## 7. 行为".encode(), (block + "\n## 7. 行为").encode())
    return dict(philosophy=philosophy, policy=policy)


def hashes(raw):
    return {role: sha256(data).hexdigest() for role, data in raw.items()}


def stage(store, tmp_path, block, version):
    raw = sources(block)
    paths = {}
    for role, data in raw.items():
        paths[role] = tmp_path / f"{role}-{version}.md"
        paths[role].write_bytes(data)
    kwargs = dict(scope="portfolio:7", version=version, philosophy_path=paths["philosophy"],
                  policy_path=paths["policy"], expected_hashes=hashes(raw), request_id=f"stage-{version}",
                  expected_head_version=version-1, created_at=(AT-timedelta(days=1)).isoformat())
    bundle = store.stage(**kwargs)
    assert store.stage(**kwargs) == bundle
    store.transition(scope=bundle["scope"], bundle_id=bundle["bundle_id"], state="VALIDATED",
                     expected_state="DRAFT", request_id=f"validate-{version}", at=kwargs["created_at"])
    return bundle


@pytest.fixture
def rehearsal(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    store = PolicyStore(tmp_path / "policy.sqlite")
    store.init_candidate()
    publisher = PolicyPublisher(store, environment="ISOLATED_REHEARSAL", now=lambda: STAMP)
    monkeypatch.setenv("WEALTHPILOT_POLICY_DB_PATH", str(store.path))
    monkeypatch.setenv("WEALTHPILOT_POLICY_SCOPE", "portfolio:7")
    return store, publisher


def authorize(store, bundle, op, sequence=0, active=None, at=STAMP):
    auth = approval(store, bundle, op, sequence, active)
    auth["approved_at"] = at
    install = installation(bundle, installed_at=at)
    if op != "APPROVE_STRUCTURED":
        auth["installation_sha256"] = digest(install)
    return auth, install


def approve(store, publisher, bundle, seq=0, active=None):
    auth, _ = authorize(store, bundle, "APPROVE_STRUCTURED", seq, active)
    return publisher.approve(authorization=auth, request_id=f"approve-{bundle['bundle_version']}")


def release(store, publisher, bundle, seq=0, active=None, op="ACTIVATE"):
    auth, install = authorize(store, bundle, op, seq, active, at=publisher.now())
    kwargs = dict(authorization=auth, installation=install, request_id=f"{op}-{bundle['bundle_version']}-{seq}")
    receipt = publisher.release(**kwargs)
    assert publisher.release(**kwargs) == receipt
    return receipt


def test_draft_cannot_be_formally_approved_or_activated(rehearsal, tmp_path):
    store, publisher = rehearsal
    bundle = stage(store, tmp_path, BLOCK, 1)
    assert bundle["content_status"] == "DRAFT_CONTENT"
    with pytest.raises(PolicyError, match="POLICY_CONTENT_APPROVAL_REQUIRED"):
        approve(store, publisher, bundle)
    with pytest.raises(PolicyError, match="POLICY_CONTENT_APPROVAL_REQUIRED"):
        release(store, publisher, bundle)
    with pytest.raises(PolicyError, match="VERSIONED_SCOPE_REQUIRES_FORMAL_APPROVAL"):
        store.transition(scope=bundle["scope"], bundle_id=bundle["bundle_id"], state="APPROVED",
                         expected_state="VALIDATED", request_id="generic-approve", at=STAMP, approval_ref="test")
    with pytest.raises(PolicyError, match="VERSIONED_SCOPE_REQUIRES_FORMAL_RELEASE"):
        publish_synthetic(store, scope=bundle["scope"], bundle_id=bundle["bundle_id"],
                          request_id="synthetic-release", expected_sequence=0, at=STAMP)
    assert PolicyService(store).get_active_policy_context(bundle["scope"], as_of=STAMP)["status"] == "NOT_ACTIVE"
    assert not store.release_history(bundle["scope"])


@pytest.mark.parametrize("change", [
    lambda s: s.replace("stable_pool：30%", "stable_pool：29%"),
    lambda s: s.replace("stable_pool：30%", "cash：30%"),
    lambda s: s.replace("wp.policy.v2", "wp.policy.v9"),
    lambda s: s.replace("B3节点 stable_pool：30%\n", ""),
    lambda s: s.replace("PORTFOLIO_PER_SNAPSHOT", "PER_NODE"),
    lambda s: s.replace("DRAFT_CONTENT", "APPROVED_CONTENT | 2021-01-01 | https://github.com/song-buaa/WealthPilot/issues/111#issuecomment-6814354290"),
])
def test_bad_source_mapping_fails(change):
    raw = sources(change(BLOCK))
    with pytest.raises(ValueError):
        derive(**raw, expected_hashes=hashes(raw), policy_version=2)


def test_version_hash_cas_history_and_rollback(rehearsal, tmp_path):
    store, publisher = rehearsal
    old = stage(store, tmp_path, "", 1)
    old_hash = digest(old)
    assert type(parse_snapshot(old["snapshot"])) is Snapshot
    approve(store, publisher, old)
    release(store, publisher, old)
    service = PolicyService(store)
    v1_ref = service.get_active_policy_context(old["scope"], as_of=STAMP)["policy_reference"]
    new = stage(store, tmp_path, BLOCK.replace("DRAFT_CONTENT", APPROVAL), 2)
    assert isinstance(parse_snapshot(new["snapshot"]), SnapshotV2)
    with pytest.raises(ValueError):
        Snapshot.model_validate(new["snapshot"])
    assert digest(new["snapshot"]) == new["structured_sha256"]
    v1_rules = [{k:r[k] for k in ("rule_id", "operator", "threshold", "subject", "action", "scope")}
                for r in old["snapshot"]["rules"]]
    assert v1_rules == [{k:r[k] for k in v1_rules[0]} for r in new["snapshot"]["rules"]]
    with pytest.raises(PolicyError, match="FORMAL_STRUCTURED_APPROVAL_REQUIRED"):
        release(store, publisher, new, 1, old_hash)
    auth, _ = authorize(store, new, "APPROVE_STRUCTURED", 1, old_hash)
    auth["structured_sha256"] = old["structured_sha256"]
    with pytest.raises(PolicyError, match="AUTHORIZATION_TARGET_MISMATCH"):
        publisher.approve(authorization=auth, request_id="wrong-hash")
    approve(store, publisher, new, 1, old_hash)
    assert service.get_active_policy_context(old["scope"], as_of=STAMP)["policy_reference"] == v1_ref
    with pytest.raises(PolicyError, match="RELEASE_CAS_CONFLICT"):
        release(store, publisher, new)
    auth, install = authorize(store, new, "ACTIVATE", 1, old_hash)
    with pytest.raises(PolicyError, match="REQUEST_ID_CONFLICT"):
        publisher.release(authorization=auth, installation=install, request_id="ACTIVATE-1-0")
    install["bundle_sha256"] = old_hash
    with pytest.raises(ValueError):
        publisher.release(authorization=auth, installation=install, request_id="wrong-install")
    later = (AT + timedelta(seconds=1)).isoformat()
    publisher.now = lambda: later
    release(store, publisher, new, 1, old_hash)
    assert service.get_active_policy_context(old["scope"], as_of=STAMP)["policy_reference"] == v1_ref
    assert service.get_active_policy_context(new["scope"], as_of=later)["policy_reference"]["bundle_sha256"] == digest(new)
    release(store, publisher, old, 2, digest(new), "ROLLBACK")
    assert service.get_active_policy_context(old["scope"], as_of=later)["policy_reference"] == v1_ref
    assert len(store.release_history(old["scope"])) == 3
    assert digest(store.read_bundle(old["scope"], old["bundle_id"])) == old_hash


@pytest.fixture
def active_v2(rehearsal, tmp_path):
    store, publisher = rehearsal
    bundle = stage(store, tmp_path, BLOCK.replace("DRAFT_CONTENT", APPROVAL), 1)
    approve(store, publisher, bundle)
    release(store, publisher, bundle)
    return store, bundle


@pytest.mark.parametrize("shift,hit", [("50",False),("-50",False),("50.01",True),("-50.01",True),("0",False)])
def test_formal_b3_review_at_real_http(active_v2, b3, tmp_path, monkeypatch, client, shift, hit):
    view, _ = b3
    inputs = view["data_quality"]["security_inputs"]
    inputs[4]["market_value_cny"] = str(300 + Decimal(shift))
    inputs[6]["market_value_cny"] = str(200 - Decimal(shift))
    # Replace CBU3 with qualified investment cash without changing the stable total.
    inputs[0]["market_value_cny"] = "100"
    inputs[7]["market_value_cny"] = "0"
    before = active_v2[0].path.read_bytes()
    result = read(b3, tmp_path, monkeypatch, client)
    assert result.status_code == 200, result.text
    meta = result.json()["policy_allocation"]
    hierarchy = meta["hierarchy"]
    review = hierarchy["strategic_review"]
    assert hierarchy["classified_assets"]["position:1"]["bucket"] == "cash"
    assert hierarchy["secondary"]["cash"]["amount_cny"] == "100"
    assert Decimal(hierarchy["secondary"]["cash"]["gap_pp"]) > 5  # Never independently triggers.
    assert len(hierarchy["direct_equity"]["issuers"]) == 6
    assert hierarchy["direct_equity"]["unallocated"]["target_kind"] == "NEUTRAL_POLICY"
    assert review["status"] == ("HUMAN_REVIEW" if hit else "NO_RULE_TRIGGERED")
    assert set(review["triggered_classes"]) == ({"growth_and_alternatives","stable_pool"} if hit else set())
    assert bool(review["review_id"]) == hit
    again = client.get("/api/allocation/snapshot").json()["policy_allocation"]["hierarchy"]["strategic_review"]
    assert again == review
    assert active_v2[0].path.read_bytes() == before
    from backend.services.allocation_service import _assembled_policy_assets
    with _assembled_policy_assets(7, frozen_view=view):
        result = runtime.diagnose(7)
        assert len([t for t in result["triggers"] if t["rule_id"] == "allocation.deviation"]) == int(hit)
        assert not result["auto_trade_allowed"]


@pytest.mark.parametrize("bad", ["scope", "version", "source", "bundle", "structured", "synthetic", "partial"])
def test_wrong_binding_does_not_promote_existing_candidates(active_v2, bad):
    raw = runtime.resolve(7)
    snapshot = parse_snapshot(raw["snapshot"])
    candidate = {"status":"RULE_SCOPE_UNVERIFIED","triggered_classes":[],
                 "candidate_hit_classes":["growth_and_alternatives","stable_pool"],"rule_id":"allocation.deviation"}
    hierarchy = {"status":"QUALIFIED","strategic_review":candidate}
    if bad == "scope": raw["policy_reference"]["scope"] = "portfolio:8"
    if bad == "version": raw["policy_reference"]["bundle_version"] += 1
    if bad == "source": raw["source_hashes"] = {}
    if bad == "bundle": raw["policy_reference"]["bundle_sha256"] = "0"*64
    if bad == "structured": raw["policy_reference"]["structured_sha256"] = "0"*64
    if bad == "synthetic": raw["release"]["synthetic"] = True
    if bad == "partial": hierarchy["status"] = "PARTIAL"
    assert formal_strategic_review(snapshot, hierarchy, policy_context=raw, portfolio_id=7,
                                   snapshot_sha256="a"*64, denominator=Decimal("1000")) == candidate


def test_explicit_historical_parser_keeps_unstructured_prose():
    raw = sources("")
    raw["policy"] += "\nB3节点合同：历史备注\n".encode()
    old = derive(**raw, expected_hashes=hashes(raw), policy_version=1, expected_schema_version="wp.policy.v1")
    assert type(old) is Snapshot
    assert digest(old) == "e0d7b84025ed099d345e112e16a389725ed44b7923890da313263296b96b4a98"
    for block, version in [("", "wp.policy.v2"), ("", "wp.policy.v9")]:
        raw = sources(block)
        with pytest.raises(PolicyError, match="POLICY_SOURCE_SCHEMA_MISMATCH"):
            derive(**raw, expected_hashes=hashes(raw), policy_version=1, expected_schema_version=version)


def test_exact_decimal_beyond_default_context_and_fail_closed_denominator(active_v2):
    raw = runtime.resolve(7)
    snapshot = parse_snapshot(raw["snapshot"])
    unverified = {"status":"RULE_SCOPE_UNVERIFIED","triggered_classes":[],
                  "candidate_hit_classes":[],"rule_id":"allocation.deviation"}
    hierarchy = {"status":"QUALIFIED", "strategic_review":unverified,
                 "classified_assets":{
                     "growth":{"bucket":"us_etf","amount_cny":"750.00000000000000000000000001"},
                     "stable":{"bucket":"cash","amount_cny":"249.99999999999999999999999999"},
                 }}
    kwargs = dict(policy_context=raw, portfolio_id=7, snapshot_sha256="a"*64)
    result = formal_strategic_review(snapshot, hierarchy, denominator=Decimal(1000), **kwargs)
    assert result["status"] == "HUMAN_REVIEW" and len(result["triggered_classes"]) == 2
    for denominator in (None, Decimal(0), Decimal(1001)):
        assert formal_strategic_review(snapshot, hierarchy, denominator=denominator, **kwargs) == unverified


@pytest.mark.parametrize("bad", ["classification", "denominator"])
def test_qualified_v2_still_requires_complete_source(active_v2, b3, tmp_path, monkeypatch, client, bad):
    view, _ = b3
    if bad == "classification":
        view["data_quality"]["security_inputs"][1]["symbol"] = "FXI:US"
        response = read(b3, tmp_path, monkeypatch, client)
        assert response.status_code == 200
        hierarchy = response.json()["policy_allocation"]["hierarchy"]
        assert hierarchy["strategic_review"]["status"] == "RULE_SCOPE_UNVERIFIED"
        assert not hierarchy["strategic_review"]["triggered_classes"]
        assert hierarchy["strategic"]["stable_pool"]["current_ratio"] is None
    else:
        view["data_quality"]["investable_scope_evidence"]["coverage"]["btc"]["status"] = "UNKNOWN"
        response = read(b3, tmp_path, monkeypatch, client)
        assert response.status_code == 409 and response.json()["qualification"]["status"] == "UNKNOWN"
