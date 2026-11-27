from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from time import perf_counter

import pytest

from backend.services.investment_policy import config_cli, runtime
from backend.services.investment_policy.configuration import from_snapshot
from backend.services.investment_policy.contracts import canonical, digest, parse_snapshot
from backend.services.investment_policy.external_content import content_only
from backend.services.investment_policy.fast_release import preview, preview_content, publish
from backend.services.investment_policy.formal_publisher import CONSUMERS
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import clock, utc_now
from tests.test_policy_config_fast_release import (
    CODE, SCOPE, candidate_checks, edits, isolated, policy_mcp, run_publish, sign,
)


def current(publisher):
    return PolicyService(publisher.store).get_active_policy_context(SCOPE)


def raw_active(publisher):
    bundle_id = current(publisher)["policy_reference"]["bundle_id"]
    with publisher.store.connection() as db:
        row = db.execute("SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?",
                         (SCOPE,bundle_id)).fetchone()
    return bytes(row["philosophy"]), bytes(row["policy"])


def signed(candidate, key, *, approved_at=None):
    approval = {k:candidate[k] for k in ("contract", "operation", "scope", "environment", "store_identity")}
    approval |= dict(candidate_sha256=digest(candidate), approved_by="TEST ONLY USER",
                    approved_at=approved_at or utc_now(), approval_id="test-only-content-"+digest(candidate)[:20])
    return approval, key.sign(canonical(approval).encode()).hex()


def run(isolated, tmp_path, candidate, sources, approval=None, **overrides):
    publisher, _, _, key, trust = isolated
    return publish(publisher, candidate, *(approval or signed(candidate, key)), trust, sources=sources, **({
        "check_candidates":candidate_checks,
        "read_consumers":lambda _: {},
        "backup":lambda c:config_cli._backup(publisher.store,tmp_path,c),
    } | overrides))


def sources_for(publisher):
    return tuple(map(content_only, raw_active(publisher)))


def threshold_patch(policy):
    return policy.replace("严格超过10%".encode(), "严格超过10.25%".encode(), 1).replace(
        "不重复按普通10%规则".encode(), "不重复按普通10.25%规则".encode())


def test_single_approval_releases_l2_then_l1_and_rollback(isolated, tmp_path):
    publisher, originals, _, key, trust = isolated
    p, m = sources_for(publisher)
    sources = p + b"\nTEST ONLY philosophy clarification.\n", m + "\n## 11. 非执行政策说明\n\nTEST ONLY prose. 中国权益历史案例保留9%中性预算。\n".encode()
    candidate = preview_content(publisher,SCOPE,sources,code_sha=CODE)
    assert not candidate["changes"]["executable_thresholds"]
    assert set(candidate["changes"]["descriptive_diff"]) == {"philosophy","policy_notes"}
    assert len(publisher.store.history(SCOPE)) == 9
    approval = signed(candidate,key)
    started = perf_counter()
    receipt = run(isolated,tmp_path,candidate,sources,approval)
    assert receipt["status"] == "ACTIVATED_READBACK_PENDING"
    assert raw_active(publisher) == sources
    active = current(publisher)
    assert active["snapshot"]["content_approved_on"] is None
    assert active["release"]["verified_release_approval"]["approved_at"] == approval[0]["approved_at"]
    with policy_mcp() as mcp:
        def readbacks(_):
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            from backend.api.discipline import router
            from backend.services.allocation_service import get_targets
            from backend.services.discipline_service import get_rules_config
            service = FastAPI()
            service.include_router(router)
            with TestClient(service) as web, runtime.bind(runtime.current(7)) as session:
                get_targets()
                refs = dict(web=web.get("/policy-context").json()["policy_reference"],
                    backend=current(publisher)["policy_reference"], allocation=session.receipts["allocation"],
                    discipline=get_rules_config()["policy_reference"],
                    peer=json.loads(runtime.prompt(7).rsplit("\n",1)[1])["policy_reference"])
            if mcp:
                refs["mcp"] = mcp()["policy_reference"]
            else:
                refs["mcp"] = runtime.context(7,consumer="mcp")["policy_reference"]
            return {name:dict(consumer=name, phase="ACTIVE", code_sha=CODE, runtime_id="test-only",
                              policy_reference=ref) for name, ref in refs.items()}
        retried = run(isolated,tmp_path,candidate,sources,approval,read_consumers=readbacks)
    assert retried["status"] == "ACTIVE_READBACK_PASS" and set(retried["consumers"]) == CONSUMERS
    assert receipt["release"] == retried["release"]
    assert len(publisher.store.release_history(SCOPE)) == 4
    print("V11_REHEARSAL_SECONDS=", round(perf_counter()-started,3), "MCP_PROTOCOL=", bool(mcp))

    rationale, policy = sources
    second_sources = rationale, threshold_patch(policy)
    second = preview_content(publisher,SCOPE,second_sources,code_sha=CODE)
    assert second["changes"]["executable_thresholds"] == {"issuer.warning.default":{"before":"10.0%","after":"10.2500%"}}
    run(isolated,tmp_path,second,second_sources)
    config = edits(parse_snapshot(current(publisher)["snapshot"]))
    l1, _ = preview(publisher,SCOPE,config,trust["listings"],code_sha=CODE)
    run_publish(publisher,l1,sign(key,l1),trust,tmp_path)
    snapshot = parse_snapshot(current(publisher)["snapshot"])
    assert next(r.threshold for r in snapshot.rules if r.rule_id=="issuer.warning.default") == Decimal(".1025")
    from backend.services.investment_policy.contracts import MetricEvidence
    from backend.services.investment_policy.evaluation import evaluate_snapshot
    at = utc_now()
    metric = MetricEvidence(value=Decimal(".101"), complete=True, fresh=True,
                            identity_verified=True, denominator_qualified=True, as_of=at)
    def triggers(value):
        return evaluate_snapshot(value, metrics={"issuer_weight":metric}, issuer_symbol="TEST:US",
            behavior_facts={}, as_of=at, max_age=timedelta(minutes=5))["triggers"]
    assert any(row["rule_id"]=="issuer.warning.default" for row in triggers(parse_snapshot(originals[-1]["snapshot"])))
    assert not any(row["rule_id"]=="issuer.warning.default" for row in triggers(snapshot))
    assert b"TEST ONLY philosophy clarification" in raw_active(publisher)[0]
    assert "中国权益历史案例保留9%中性预算。".encode() in raw_active(publisher)[1]
    rollback, _ = preview(publisher,SCOPE,None,[],code_sha=CODE,rollback_to=originals[-1]["bundle_id"])
    run_publish(publisher,rollback,sign(key,rollback),trust,tmp_path)
    assert current(publisher)["policy_reference"] == rollback["target"]
    for old in originals:
        assert publisher.store.read_bundle(SCOPE,old["bundle_id"]) == old


def test_candidate_binding_and_reject_unsupported_changes(isolated, tmp_path, monkeypatch):
    publisher, _, _, key, _ = isolated
    ph, policy = sources_for(publisher)
    source = ph + b"\nReview draft.\n", policy
    candidate = preview_content(publisher,SCOPE,source,code_sha=CODE)
    auth = signed(candidate,key)
    for replacement in ("达到或超过10%","严格超过0%","严格超过95%"):
        changed = policy.replace("严格超过10%".encode(),replacement.encode(),1)
        with pytest.raises(PolicyError):
            preview_content(publisher,SCOPE,(ph,changed),code_sha=CODE)
    with pytest.raises(PolicyError,match="SIGNED_USER_APPROVAL_REQUIRED"):
        run(isolated,tmp_path,candidate,source,(auth[0],"00"*64))
    with pytest.raises(PolicyError,match="FROZEN_CONTENT_CANDIDATE_MISMATCH"):
        run(isolated,tmp_path,candidate,(source[0]+b"x",policy),auth)
    with pytest.raises(PolicyError,match="FAST_RELEASE_INSTALLATION_BINDING_MISMATCH"):
        run(isolated,tmp_path,candidate|{"scope":"portfolio:9"},source)
    with pytest.raises(PolicyError,match="FAST_RELEASE_INSTALLATION_BINDING_MISMATCH"):
        run(isolated,tmp_path,candidate|{"store_identity":"0"*64},source)
    with pytest.raises(PolicyError,match="APPROVAL_TARGET_MISMATCH"):
        run(isolated,tmp_path,candidate|{"changes":{}},source,auth)
    old_clock = publisher.now
    monkeypatch.setattr(publisher,"now",lambda:(clock(candidate["created_at"])+timedelta(days=2)).isoformat())
    with pytest.raises(PolicyError,match="POLICY_CANDIDATE_EXPIRED"):
        run(isolated,tmp_path,candidate,source,auth)
    monkeypatch.setattr(publisher,"now",old_clock)
    assert len(publisher.store.release_history(SCOPE)) == 3
    changed = ph + b"\nNew content.\n", policy
    fresh = preview_content(publisher,SCOPE,changed,code_sha=CODE)
    run(isolated,tmp_path,fresh,changed)
    with pytest.raises(PolicyError,match="STALE_POLICY_CANDIDATE"):
        run(isolated,tmp_path,candidate,source,auth)


def test_real_v4_archive_reissues_exact_btc_content_in_isolation(isolated, tmp_path):
    import os
    from pathlib import Path
    from tests.test_policy_publisher import approval, installation

    archive_path = os.environ.get("WP_POLICY_V11_V4_ARCHIVE")
    if not archive_path:
        pytest.skip("Private frozen v4 archive is available only in the authorized local rehearsal")
    archive = Path(archive_path)
    original = tuple(next(archive.glob(pattern)).read_bytes() for pattern in (
        "投资理念_*.md", "*BTC_L2_PENDING_REVIEW_ONLY.md"))
    assert [sha256(raw).hexdigest() for raw in original] == [
        "699ab8b70dacb5eeeed79379061a3e401f7a0cca5babe8a42c108cc9292ea625",
        "4db2e3ce98a4e818589a579e54ea53e13f130e2a99f4873cdbe5511942971370",
    ]
    publisher, earlier, _, key, trust = isolated
    store = publisher.store
    paths = {}
    for role, raw in zip(("philosophy","policy"),original,strict=True):
        path = tmp_path / f"gold-{role}.md"
        path.write_bytes(raw)
        paths[role] = path
    gold = store.stage(scope=SCOPE, version=4, philosophy_path=paths["philosophy"],
        policy_path=paths["policy"], expected_hashes={role:sha256(path.read_bytes()).hexdigest()
            for role,path in paths.items()}, request_id="historical-v4",
        expected_head_version=3, created_at=utc_now())
    assert gold["structured_sha256"] == "1cb93c9080c75d0b2d1608bf631c4a8bb99d60b4a0ff011ef3f65e7482fe05b0"
    store.transition(scope=SCOPE, bundle_id=gold["bundle_id"], state="VALIDATED",
        expected_state="DRAFT", request_id="historical-v4-validate", at=utc_now())
    auth = approval(store,gold,sequence=3,active=digest(earlier[-1]))
    auth["approved_at"] = utc_now()
    publisher.approve(authorization=auth,request_id="historical-v4-fixture-approve")
    install = installation(gold,installed_at=utc_now())
    auth.update(operation="ACTIVATE",approved_at=utc_now(),installation_sha256=digest(install))
    publisher.release(authorization=auth,installation=install,request_id="historical-v4-fixture-release")

    candidate_sources = sources_for(publisher)
    candidate = preview_content(publisher,SCOPE,candidate_sources,code_sha=CODE)
    run(isolated,tmp_path,candidate,candidate_sources)
    assert raw_active(publisher) == candidate_sources
    before = parse_snapshot(gold["snapshot"])
    after = parse_snapshot(current(publisher)["snapshot"])
    assert from_snapshot(before) == from_snapshot(after)
    assert [r.model_dump(exclude={"source_clause","policy_version"}) for r in before.rules] == [
        r.model_dump(exclude={"source_clause","policy_version"}) for r in after.rules]
    assert before.btc_governance.model_dump(exclude={"source_clauses"}) == after.btc_governance.model_dump(
        exclude={"source_clauses"})
    assert store.read_bundle(SCOPE,gold["bundle_id"]) == gold
    l1, _ = preview(publisher,SCOPE,from_snapshot(after),trust["listings"],code_sha=CODE)
    run_publish(publisher,l1,sign(key,l1),trust,tmp_path)
    assert parse_snapshot(current(publisher)["snapshot"]).btc_governance.eligible_instrument == "UNLEVERAGED_SPOT"


def test_cli_one_confirmed_reference_routes_by_frozen_candidate(isolated, tmp_path, monkeypatch, capsys):
    import sys

    publisher, _, _, key, trust = isolated
    private = lambda name, data: config_cli._write(tmp_path / name, data)
    export = tmp_path / "export"
    candidate_file = tmp_path / "frozen.json"
    operator_input = tmp_path / "approved-test-receipt.json"
    script = tmp_path / "test_operator.py"
    script.write_text(
        "import json,sys\n"
        "signed=json.load(open(sys.argv[1]))\n"
        "request=json.load(sys.stdin)\n"
        "assert all(signed['approval'][key]==value for key,value in request.items())\n"
        "print(json.dumps(signed))\n")
    trust |= dict(backup_dir=str(tmp_path), approval_command=[sys.executable,str(script),str(operator_input)])
    private("trust.json", canonical(trust).encode())
    prefix = ["--db",str(publisher.store.path),"--scope",SCOPE,"--environment","ISOLATED_REHEARSAL",
              "--rehearsal-trust",str(tmp_path/"trust.json")]
    monkeypatch.setattr(config_cli,"_probes",lambda trust,phase,candidate,bundle=None: {
        name:dict(consumer=name,phase=phase,code_sha=CODE,runtime_id=f"test-{name}",
                  policy_reference=candidate["target"] if bundle else current(publisher)["policy_reference"])
        for name in CONSUMERS})
    config_cli.main([*prefix,"export-content","--out",str(export)])
    capsys.readouterr()
    config_cli.main([*prefix,"preview","--philosophy",str(export)+".philosophy.md",
                     "--policy",str(export)+".policy.md","--out",str(candidate_file)])
    summary = json.loads(capsys.readouterr().out)
    candidate = json.loads(candidate_file.read_bytes())
    assert summary["source_hashes"] == candidate["source_hashes"] and summary["candidate_sha256"] == digest(candidate)
    auth, signature = signed(candidate,key)
    private(operator_input.name, canonical(dict(approval=auth,signature=signature)).encode())
    with pytest.raises(PolicyError, match="TRUSTED_APPROVAL_OPERATOR_REJECTED"):
        config_cli.main([*prefix,"publish","--candidate",str(candidate_file),"--confirmed-ref","wrong"])
    cmd = [*prefix,"publish","--candidate",str(candidate_file),"--confirmed-ref",auth["approval_id"]]
    assert config_cli.main(cmd) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "ACTIVE_READBACK_PASS" and receipt["approval_ref"] == auth["approval_id"]
    assert config_cli.main(cmd) == 0
    assert json.loads(capsys.readouterr().out) == receipt
    assert len(publisher.store.release_history(SCOPE)) == 4
