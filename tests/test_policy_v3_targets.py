"""Offline amendment, immutable predecessor and B3 compatibility checks."""
from decimal import Decimal

import pytest

from backend.services.instruments.issuers import direct_issuer
from backend.services.investment_policy import runtime
from backend.services.investment_policy.contracts import digest, parse_snapshot
from backend.services.investment_policy.evaluation import budget_preview
from backend.services.investment_policy.parser import PolicyError, derive
from tests.test_policy_b3_versioned_scope import (
    APPROVAL, BLOCK, approve, b3, client, clock, hashes, read, rehearsal, release, source, sources,  # noqa: F401
)


AMENDMENT_DRAFT = "美国核心股内容批准：DRAFT_CONTENT"
# Fictional reference for offline schema exercise. No approval lookup or external writes.
AMENDMENT_APPROVAL = "美国核心股内容批准：APPROVED_CONTENT | 2022-02-02 | https://github.com/song-buaa/WealthPilot/issues/99999#issuecomment-99999"


def raw_amendment(marker=AMENDMENT_DRAFT):
    raw = sources(BLOCK.replace("DRAFT_CONTENT", APPROVAL))
    raw["policy"] = raw["policy"].decode().replace(
        "| 其他自主选股 | 3% |",
        "| NVDA（NVIDIA） | 2% |\n| ANT（Anthropic, PBC；Nasdaq Class A普通股） | 1% |\n| 其他自主选股 | 0% |",
    ).replace("已指定六股合计27%", "已指定八股合计30%\n" + marker).encode()
    return raw


def parse(raw, version=3, **kwargs):
    return derive(**raw, expected_hashes=hashes(raw), policy_version=version, **kwargs)


def test_policy_amendment_with_separate_approval_retains_b3_and_rules():
    old = parse(sources(BLOCK.replace("DRAFT_CONTENT", APPROVAL)), 2)
    draft = parse(raw_amendment())
    approved = parse(raw_amendment(AMENDMENT_APPROVAL))
    assert parse_snapshot(approved.model_dump(mode="json")) == approved
    assert approved.schema_version == old.schema_version == "wp.policy.v2"
    assert approved.content_approved_on == "2022-02-02"
    assert old.content_approved_on == draft.content_approved_on == "2021-01-01"
    assert draft.content_status == "DRAFT_CONTENT" and draft.amendment_approval.reference is None
    assert approved.content_status == "APPROVED_CONTENT"
    assert old.strategic_review_scope.nodes == approved.strategic_review_scope.nodes
    assert old.strategic_review_scope.content_approval_ref == approved.strategic_review_scope.content_approval_ref
    assert [r.model_dump(exclude={"source_clause", "policy_version"}) for r in old.rules] == [
        r.model_dump(exclude={"source_clause", "policy_version"}) for r in approved.rules
    ]
    assert len(approved.rules) == 8
    assert approved.allocation.neutral == old.allocation.neutral
    assert approved.allocation.etf_internal == old.allocation.etf_internal
    assert approved.allocation.specified_equity_total == Decimal(".30")
    assert approved.allocation.unused_active_budget == "US_ETF_CONDITIONAL"
    assert approved.allocation.direct_equity["unallocated"] == 0
    scenario = budget_preview(approved, china_used=Decimal(".03"), unallocated_us_used=Decimal(0),
                              temporary_cash=Decimal(0), specified_us_used=Decimal(".27"))
    assert Decimal(scenario["budget"]["us_etf"]) == Decimal(".35")
    assert Decimal(scenario["budget"]["us_direct"]) == Decimal(".27")
    assert scenario["auto_trade_allowed"] is False
    frozen = approved.model_dump(mode="json")
    del frozen["amendment_approval"]
    with pytest.raises(ValueError, match="POLICY_SOURCE_TARGET_COUNT_MISMATCH"):
        parse_snapshot(frozen)


@pytest.mark.parametrize("old,new", [
    (AMENDMENT_DRAFT, ""),
    ("已指定八股合计30%\n" + AMENDMENT_DRAFT, "已指定六股合计30%"),
    (AMENDMENT_DRAFT, AMENDMENT_APPROVAL.replace("/99999#", "/124#")),
    (AMENDMENT_DRAFT, AMENDMENT_APPROVAL.replace("2022-02-02", "2020-02-02")),
    (AMENDMENT_DRAFT, AMENDMENT_APPROVAL.replace("2022-02-02", "2022-02-30")),
    (AMENDMENT_DRAFT, AMENDMENT_APPROVAL.split("#")[0]),
    (AMENDMENT_DRAFT, AMENDMENT_DRAFT + "\n" + AMENDMENT_DRAFT),
    (APPROVAL, "DRAFT_CONTENT"),
    ("ANT（Anthropic, PBC；Nasdaq Class A普通股）", "ANT（Anthropic, PBC；Nasdaq Class B普通股）"),
    ("NVDA（NVIDIA）", "ZZZY"),
    ("NVDA（NVIDIA）", "GOOGL"),
    ("NVDA（NVIDIA） | 2%", "NVDA（NVIDIA） | 1%"),
])
def test_bad_amendment_sources_fail(old, new):
    raw = raw_amendment()
    raw["policy"] = raw["policy"].replace(old.encode(), new.encode())
    with pytest.raises(ValueError):
        parse(raw)


def test_amendment_cannot_be_read_as_legacy_schema():
    with pytest.raises(PolicyError, match="POLICY_SOURCE_SCHEMA_MISMATCH"):
        parse(raw_amendment(), expected_schema_version="wp.policy.v1")


@pytest.mark.parametrize("override", [
    {"symbol": "ANT:HK"}, {"currency": "HKD"}, {"vehicle_type": "ADR"},
    {"economic_asset_class": "ALTERNATIVE"}, {"share_class": "Class B"},
    {"cusip": "000000000"}, {"security_symbol": "OTHER:US", "security_currency": "USD"},
])
def test_anthropic_rejects_nonmatching_security(override):
    row = dict(symbol="ANT:US", currency="USD", vehicle_type="COMMON_STOCK", economic_asset_class="EQUITY")
    assert direct_issuer(row) == "SEC_CIK_0002130357"
    assert direct_issuer(row | override) is None


def stage_amendment(store, tmp_path, raw, version, at):
    paths = {role: tmp_path / f"{role}-{version}.md" for role in raw}
    for role, path in paths.items():
        path.write_bytes(raw[role])
    bundle = store.stage(scope="portfolio:7", version=version, philosophy_path=paths["philosophy"],
        policy_path=paths["policy"], expected_hashes=hashes(raw), request_id=f"stage-{version}",
        expected_head_version=version-1, created_at=at)
    store.transition(scope=bundle["scope"], bundle_id=bundle["bundle_id"], state="VALIDATED",
        expected_state="DRAFT", request_id=f"validate-{version}", at=at)
    return bundle


@pytest.mark.parametrize("draft_only", [True, False])
def test_v3_isolated_publisher_and_b3_readback(rehearsal, b3, client, clock, tmp_path, monkeypatch, draft_only):
    store, publisher = rehearsal
    now = publisher.now()
    v1 = stage_amendment(store, tmp_path, sources(""), 1, now)
    v2 = stage_amendment(store, tmp_path, sources(BLOCK.replace("DRAFT_CONTENT", APPROVAL)), 2, now)
    approve(store, publisher, v2)
    release(store, publisher, v2)
    v3 = stage_amendment(store, tmp_path, raw_amendment(
        AMENDMENT_DRAFT if draft_only else AMENDMENT_APPROVAL), 3, now)
    if draft_only:
        with pytest.raises(PolicyError, match="POLICY_CONTENT_APPROVAL_REQUIRED"):
            approve(store, publisher, v3, 1, digest(v2))
        assert runtime.resolve(7)["policy_reference"]["bundle_version"] == 2
    else:
        approve(store, publisher, v3, 1, digest(v2))
        release(store, publisher, v3, 1, digest(v2))
        view, _ = b3
        quality = view["data_quality"]
        equity = quality["security_inputs"][4]
        equity["market_value_cny"] = "270"
        evidence = quality["investable_scope_evidence"]
        for ident, ticker, amount in [(9, "NVDA:US", "20"), (10, "ANT:US", "10")]:
            quality["security_inputs"].append(dict(equity, id=ident, symbol=ticker, market_value_cny=amount))
            ref = f"position:{ident}"
            for domain in ("accounts", "deduplication"):
                evidence["coverage"][domain]["asset_refs"].append(ref)
            evidence["dispositions"][ref] = dict(evidence["dispositions"]["position:5"])
        response = read(b3, tmp_path, monkeypatch, client)
        assert response.status_code == 200, response.text
        allocation = response.json()["policy_allocation"]
        assert allocation["policy_reference"] == runtime.resolve(7)["policy_reference"]
        assert allocation["policy_reference"]["bundle_sha256"] == digest(v3)
        h = allocation["hierarchy"]
        assert h["direct_equity"]["status"] == "QUALIFIED"
        targets = h["direct_equity"]["issuers"]
        assert len(targets) == 8
        assert targets["NVDA（NVIDIA）"]["issuer_id"] == "SEC_CIK_0001045810"
        ant = targets["ANT（Anthropic, PBC；Nasdaq Class A普通股）"]
        assert ant["issuer_id"] == "SEC_CIK_0002130357"
        assert Decimal(ant["amount_cny"]) == 10 and Decimal(ant["target_ratio"]) == Decimal(".01")
        assert Decimal(targets["NVDA（NVIDIA）"]["amount_cny"]) == 20
        for consumer in ("web", "allocation", "discipline", "peer", "mcp"):
            assert runtime.context(7, consumer=consumer, view="portfolio")["policy_reference"] == allocation["policy_reference"]
        assert Decimal(h["direct_equity"]["unallocated"]["target_ratio"]) == 0
        assert Decimal(h["secondary"]["us_etf"]["target_ratio"]) == Decimal(".30")
        assert h["strategic_review"]["status"] == "NO_RULE_TRIGGERED"
        assert not h["execution_authorized"]
        for ticker, target in (("GOOG", ".05"), ("GOOGL:US", ".05"), ("NVDA", ".02"), ("ANT", ".01")):
            position = runtime.context(7, view="position", symbol=ticker)
            assert position["policy_reference"] == allocation["policy_reference"]
            assert [Decimal(weight) for weight in position["position_target"].values()] == [Decimal(target)]
        assert not runtime.context(7, view="position", symbol="ANT:HK")["position_target"]
    for old in (v1, v2):
        assert digest(store.read_bundle(old["scope"], old["bundle_id"])) == digest(old)
