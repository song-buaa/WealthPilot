"""Exact candidate and signed publishing against a temporary private SQLite store."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import select
import subprocess
import sys
from threading import Barrier

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import state
from backend.api.discipline import router
from backend.services.allocation_service import get_targets
from backend.services.discipline_service import get_rules_config
from backend.services.instruments.issuers import IDENTITIES, extended_common_issuer
from backend.services.investment_policy import config_cli, config_probe, runtime
from backend.services.investment_policy.configuration import TargetConfig, from_snapshot
from backend.services.investment_policy.contracts import canonical, digest, parse_snapshot
from backend.services.investment_policy.evaluation import budget_preview
from backend.services.investment_policy.fast_release import CANDIDATE_VERSION, fast_store_identity, preview, publish
from backend.services.investment_policy.formal_publisher import CONSUMERS, PolicyPublisher
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import PolicyStore, utc_now
from tests.test_policy_b3_versioned_scope import APPROVAL, BLOCK, sources
from tests.test_policy_publisher import approval, installation

CODE = os.getenv("WP_POLICY_REHEARSAL_CODE_SHA", "a" * 40)
ISOLATED_COST = os.getenv("WP_POLICY_REHEARSAL_COST")
EXTRA = "SEC_CIK_0000909832" if ISOLATED_COST else "SEC_CIK_0000000123"
EXTRA_SYMBOL = "COST" if ISOLATED_COST else "NEWX"
SCOPE = "portfolio:7"


def legacy_three(tmp_path):
    tmp_path.chmod(0o700)
    store = PolicyStore(tmp_path / "policies.sqlite")
    store.init_candidate()
    publisher = PolicyPublisher(store, environment="ISOLATED_REHEARSAL")
    previous = None
    bundles = []
    original_bytes = []
    for version in range(1, 4):
        raw = sources("" if version == 1 else BLOCK.replace("DRAFT_CONTENT", APPROVAL))
        if version == 3:
            from tests.test_policy_v3_targets import raw_amendment, AMENDMENT_APPROVAL
            raw = raw_amendment(AMENDMENT_APPROVAL)
        paths = {}
        for role, source in raw.items():
            paths[role] = tmp_path / f"{role}-{version}.md"
            paths[role].write_bytes(source)
        bundle = store.stage(scope=SCOPE, version=version, philosophy_path=paths["philosophy"],
            policy_path=paths["policy"], expected_hashes={k:sha256(b).hexdigest() for k,b in raw.items()},
            expected_head_version=version-1, request_id=f"seed-{version}", created_at=utc_now())
        store.transition(scope=SCOPE, bundle_id=bundle["bundle_id"], state="VALIDATED", expected_state="DRAFT",
            request_id=f"validate-{version}", at=utc_now())
        auth = approval(store, bundle, sequence=version-1, active=previous)
        auth["approved_at"] = utc_now()
        publisher.approve(authorization=auth, request_id=f"approve-{version}")
        installed = installation(bundle, installed_at=utc_now())
        auth.update(operation="ACTIVATE", approved_at=utc_now(), installation_sha256=digest(installed))
        publisher.release(authorization=auth, installation=installed, request_id=f"release-{version}")
        previous = digest(bundle)
        bundles.append(bundle)
        original_bytes.append(raw)
    return publisher, bundles, original_bytes


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    publisher, bundles, original_bytes = legacy_three(tmp_path)
    store = publisher.store
    monkeypatch.setenv("WEALTHPILOT_POLICY_DB_PATH", str(store.path))
    monkeypatch.setenv("WEALTHPILOT_POLICY_SCOPE", SCOPE)
    monkeypatch.setattr(state, "portfolio_id", 7)
    key = Ed25519PrivateKey.generate()
    listings = [dict(symbol="NEWX:US", issuer_id=EXTRA, issuer_name="TEST ONLY NewX",
        listing_market="NASDAQ", share_class="Class A", cusip="000000AA0",
        evidence_url="https://www.sec.gov/Archives/edgar/data/123/000000012326000001/fixture.htm",
        evidence_sha256="b"*64, valid_from="2020-01-01", valid_until="2090-01-01")]
    if ISOLATED_COST:
        proof = json.loads(Path(ISOLATED_COST).read_text())
        assert proof["usage"] == "ISOLATED_TEST_ONLY"
        assert sha256(Path(ISOLATED_COST).with_name("cost-filing.html").read_bytes()).hexdigest() == proof["listing"]["evidence_sha256"]
        listings = [proof["listing"]]
    trust = dict(code_sha=CODE, scope=SCOPE, approval_key=key.public_key().public_bytes_raw().hex(),
                 approved_by="TEST ONLY USER", listings=listings, environment=publisher.environment,
                 store_identity=fast_store_identity(store))
    return publisher, bundles, original_bytes, key, trust


def sign(key, candidate):
    auth = dict(contract=CANDIDATE_VERSION, operation=candidate["operation"], candidate_sha256=digest(candidate),
        scope=candidate["scope"], store_identity=candidate["store_identity"], environment=candidate["environment"],
        approved_by="TEST ONLY USER", approval_id="isolated-"+digest(candidate)[:20], approved_at=utc_now())
    return auth, key.sign(canonical(auth).encode()).hex()


def edits(snapshot):
    cfg = from_snapshot(snapshot).model_dump(mode="json")
    cfg["direct_issuer_targets"]["SEC_CIK_0001045810"] = "0.01"
    cfg["direct_issuer_targets"][EXTRA] = "0.01"
    return cfg


def candidate_checks(candidate, bundle):
    # Candidate compatibility projection does not claim that any consumer
    # has read the candidate as ACTIVE.
    snapshot = parse_snapshot(bundle["snapshot"])
    assert len(snapshot.rules) == 8
    assert digest(snapshot) == candidate["target"]["structured_sha256"]
    return {name: dict(consumer=name, phase="CANDIDATE", code_sha=CODE,
        runtime_id=f"fixture-{name}", policy_reference=candidate["target"]) for name in CONSUMERS}


def run_publish(publisher, candidate, approval_data, trust, tmp_path, readers=None, checks=candidate_checks):
    auth, signature = approval_data
    return publish(publisher, candidate, auth, signature, trust,
        check_candidates=checks, read_consumers=readers or (lambda _: {}),
        backup=lambda c: config_cli._backup(publisher.store, tmp_path, c))


@contextmanager
def policy_mcp():
    interpreter = os.getenv("WP_POLICY_REHEARSAL_MCP_PYTHON")
    if not interpreter:
        yield None
        return
    environment = os.environ | {"WP_POLICY_REHEARSAL_BUSINESS_PYTHON": sys.executable}
    with subprocess.Popen([interpreter, "-m", "tests.policy_mcp_rehearsal"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=environment) as process:
        def read(bundle=None):
            process.stdin.write(canonical(bundle)+"\n")
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 15)[0], "MCP rehearsal timed out"
            return json.loads(process.stdout.readline())
        try:
            yield read
        finally:
            process.stdin.close()
            try:
                assert process.wait(timeout=15) == 0
            finally:
                if process.poll() is None:
                    process.kill()


def test_live_reads_consecutive_config_versions_no_restarts_and_rollback(isolated, tmp_path):
    publisher, bundles, sources_before, key, trust = isolated
    store = publisher.store
    app = FastAPI()
    app.include_router(router)
    start_pid = os.getpid()
    latest = parse_snapshot(bundles[-1]["snapshot"])
    cfg = edits(latest)
    first, _ = preview(publisher, SCOPE, cfg, trust["listings"], code_sha=CODE)
    assert EXTRA not in IDENTITIES.values()
    assert first["changes"]["direct_issuer_targets"]["after"][EXTRA] == "0.01"
    assert len(store.history(SCOPE)) == 9

    status = "ACTIVE_READBACK_PASS" if os.getenv("WP_POLICY_REHEARSAL_MCP_PYTHON") else "ACTIVATED_READBACK_PENDING"
    with TestClient(app) as web, policy_mcp() as mcp:
        mcp_pid = None
        def readers(candidate):
            nonlocal mcp_pid
            web_ref = web.get("/policy-context").json()["policy_reference"]
            backend_ref = PolicyService(store).get_active_policy_context(SCOPE)["policy_reference"]
            peer_ref = json.loads(runtime.prompt(7).rsplit("\n", 1)[1])["policy_reference"]
            with runtime.bind(runtime.current(7)) as allocation_session:
                assert sum(Decimal(str(row.mid_ratio)) for row in get_targets()) == 1
                allocation_ref = allocation_session.receipts["allocation"]
            discipline_ref = get_rules_config()["policy_reference"]
            refs = dict(web=web_ref, backend=backend_ref, peer=peer_ref, allocation=allocation_ref, discipline=discipline_ref)
            assert all(ref == candidate["target"] for ref in refs.values())
            if mcp:
                received = mcp()
                mcp_pid = mcp_pid or received["pid"]
                assert mcp_pid == received["pid"]
                assert received["policy_reference"] == candidate["target"]
                refs["mcp"] = received["policy_reference"]
            assert os.getpid() == start_pid
            return {name:dict(consumer=name, phase="ACTIVE", code_sha=CODE,
                runtime_id=f"{mcp_pid if name == 'mcp' else start_pid}/{name}", policy_reference=ref) for name,ref in refs.items()}

        def checks(candidate, bundle):
            nonlocal mcp_pid
            if not mcp:
                return candidate_checks(candidate, bundle)
            with runtime.bind(config_probe.candidate_session(7, SCOPE, bundle)):
                refs = {name:config_probe._reference(name, 7, "CANDIDATE", None, None)
                        for name in CONSUMERS if name != "mcp"}
            value = mcp(bundle)
            mcp_pid = mcp_pid or value["pid"]
            assert mcp_pid == value["pid"]
            refs["mcp"] = value["policy_reference"]
            assert all(ref == candidate["target"] for ref in refs.values())
            return {name:dict(consumer=name, phase="CANDIDATE", code_sha=CODE,
                runtime_id=f"{mcp_pid if name == 'mcp' else start_pid}/{name}", policy_reference=ref)
                for name, ref in refs.items()}

        auth = sign(key, first)
        result = run_publish(publisher, first, auth, trust, tmp_path, readers, checks)
        assert result["status"] == status
        failed_readback = run_publish(publisher, first, auth, trust, tmp_path)
        assert failed_readback["status"] == "ACTIVATED_READBACK_PENDING"
        assert failed_readback["release"] == result["release"]
        assert run_publish(publisher, first, auth, trust, tmp_path, readers)["status"] == status
        active = PolicyService(store).get_active_policy_context(SCOPE)
        current = parse_snapshot(active["snapshot"])
        assert len(current.target_issuers) == 9
        assert current.listings[f"{EXTRA_SYMBOL}:US"].issuer_id == EXTRA
        assert runtime.context(7,view="position",symbol=EXTRA_SYMBOL)["position_target"] == {EXTRA_SYMBOL:"0.01"}
        assert budget_preview(current, china_used=Decimal(".03"), unallocated_us_used=Decimal(0),
            temporary_cash=Decimal(0), specified_us_used=Decimal(".27"))["budget"]["us_etf"] == "0.35"
        next_config = from_snapshot(current).model_dump(mode="json")
        for issuer in (EXTRA, "SEC_CIK_0001045810"):
            del next_config["direct_issuer_targets"][issuer]
        next_config["unallocated"] = "0.02"
        next_config["neutral"].update(us_etf="0.31", ib01="0.19")
        next_config["etf_internal"].update(SPY="0.32", QQQ="0.48")
        second, _ = preview(publisher, SCOPE, next_config, trust["listings"], code_sha=CODE)
        assert run_publish(publisher, second, sign(key, second), trust, tmp_path, readers, checks)["status"] == status
        updated = parse_snapshot(PolicyService(store).get_active_policy_context(SCOPE)["snapshot"])
        assert len(updated.target_issuers) == 7
        assert updated.strategic_review_scope.nodes["growth_and_alternatives"] == Decimal(".71")
        assert updated.allocation.us_core_floor == Decimal(".50")  # unchanged synthetic rule
        assert updated.allocation.etf_internal["SPY"] == Decimal(".32")
        rollback, _ = preview(publisher, SCOPE, None, [], code_sha=CODE, rollback_to=bundles[-1]["bundle_id"])
        assert run_publish(publisher, rollback, sign(key, rollback), trust, tmp_path, readers, checks)["release"]["action"] == "ROLLBACK"
        assert PolicyService(store).get_active_policy_context(SCOPE)["policy_reference"] == rollback["target"]
        if mcp and os.getenv("WP_POLICY_REHEARSAL_RECEIPT"):
            Path(os.environ["WP_POLICY_REHEARSAL_RECEIPT"]).write_text(canonical({
                "code_sha":CODE, "web_and_backend_pid":start_pid, "mcp_pid":mcp_pid,
                "scope":SCOPE, "first":first["target"], "second":second["target"], "rollback":rollback["target"],
                "first_readback":result["consumers"], "releases":len(store.release_history(SCOPE)),
                "fixture_only":True, "production_writes":0})+"\n")
        for bundle, original in zip(bundles, sources_before):
            assert store.read_bundle(SCOPE, bundle["bundle_id"]) == bundle
            with store.connection() as db:
                row = db.execute("SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?",
                                 (SCOPE,bundle["bundle_id"])).fetchone()
                assert dict(row) == original


@pytest.mark.parametrize("bad", ["negative", "not_closed", "rule_change", "cash_override", "unknown", "expired", "dup_listing", "wrong_class"])
def test_config_exclusions(isolated, bad):
    pub, bundles, _, _, trust = isolated
    cfg = edits(parse_snapshot(bundles[-1]["snapshot"]))
    listings = trust["listings"]
    if bad == "negative":
        cfg["unallocated"] = "-0.1"
    elif bad == "not_closed":
        cfg["unallocated"] = "0.1"
    elif bad == "rule_change":
        cfg["risk_rules"] = {"5pp":"4pp"}
    elif bad == "cash_override":
        cfg["neutral"].update(ib01="0", cbu3="0", cash="0.30")
    elif bad == "unknown":
        listings = []
    elif bad == "expired":
        listings[0]["valid_until"] = "2020-01-02"
    elif bad == "dup_listing":
        listings.append(deepcopy(listings[0]))
    else:
        listings[0]["vehicle"] = "ADR"
    with pytest.raises(ValueError):
        preview(pub, SCOPE, cfg, listings, code_sha=CODE)
    assert len(pub.store.release_history(SCOPE)) == 3


def test_signed_candidate_tampering_stale_cas_and_atomic_abort(isolated, tmp_path, monkeypatch):
    pub, bundles, _, key, trust = isolated
    cfg = edits(parse_snapshot(bundles[-1]["snapshot"]))
    candidate, _ = preview(pub, SCOPE, cfg, trust["listings"], code_sha=CODE)
    auth, sig = sign(key, candidate)
    change = deepcopy(candidate)
    change["inputs"]["config"]["unallocated"] = "0.1"
    with pytest.raises(PolicyError, match="APPROVAL_TARGET_MISMATCH"):
        run_publish(pub, change, (auth,sig), trust, tmp_path)
    with pytest.raises(PolicyError, match="SIGNED_USER_APPROVAL_REQUIRED"):
        run_publish(pub, candidate, (auth,"aa"*64), trust, tmp_path)
    with pytest.raises(PolicyError, match="FAST_RELEASE_INSTALLATION_BINDING_MISMATCH"):
        run_publish(pub, candidate | {"store_identity":"a"*64}, (auth,sig), trust, tmp_path)
    append = pub.store._append
    with monkeypatch.context() as isolated_patch:
        def abort(db, scope, bundle_id, state, *args, **kwargs):
            if state == "APPROVED":
                raise RuntimeError("simulated interruption after Stage and Validate")
            return append(db, scope, bundle_id, state, *args, **kwargs)
        isolated_patch.setattr(pub.store, "_append", abort)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            run_publish(pub, candidate, (auth,sig), trust, tmp_path)
    assert len(pub.store.history(SCOPE)) == 9
    other, _ = preview(pub, SCOPE, cfg, trust["listings"], code_sha=CODE)
    run_publish(pub, candidate, (auth,sig), trust, tmp_path)
    with pytest.raises(PolicyError, match="STALE_POLICY_CANDIDATE"):
        run_publish(pub, other, sign(key, other), trust, tmp_path)


def test_extended_listing_checks_conflicting_holding_details(isolated):
    pub, bundles, _, _, trust = isolated
    candidate, _ = preview(pub, SCOPE, edits(parse_snapshot(bundles[-1]["snapshot"])), trust["listings"], code_sha=CODE)
    from backend.services.investment_policy.configuration import Listing
    listings = {item["symbol"]:Listing.model_validate(item) for item in candidate["inputs"]["listings"]}
    row = dict(symbol=f"{EXTRA_SYMBOL}:US", economic_asset_class="EQUITY", vehicle_type="COMMON_STOCK", currency="USD")
    assert extended_common_issuer(row, listings) == EXTRA
    for details in ({"cusip":"123456789"}, {"share_class":"Class B"}, {"security_symbol":"OTHER:US"},
                    {"vehicle_type":"ADR"}, {"currency":"HKD"}):
        assert extended_common_issuer(row | details, listings) is None


def test_cli_preview_and_signed_publish(isolated, tmp_path, capsys):
    pub, bundles, _, key, trust = isolated
    python = Path(sys.executable)
    runner = tmp_path / "probe.py"
    runner.write_text(
        "import json, os, sys\n"
        "from backend.services.investment_policy.contracts import digest\n"
        "from backend.services.investment_policy.service import PolicyService\n"
        "from backend.services.investment_policy.store import PolicyStore\n"
        "req=json.load(sys.stdin)\n"
        "scope=req['scope']\n"
        "if req['phase']=='CANDIDATE':\n"
        " b=req['candidate_bundle']\n"
        " ref={k:b[k] for k in ['scope','bundle_id','bundle_version','structured_sha256']}\n"
        " ref['bundle_sha256']=digest(b)\n"
        "else:\n"
        " ref=PolicyService(PolicyStore(os.environ['WEALTHPILOT_POLICY_DB_PATH'])).get_active_policy_context(scope)['policy_reference']\n"
        "print(json.dumps(dict(consumer=sys.argv[1], phase=req['phase'], code_sha=sys.argv[2],"
        " runtime_id='MOCK_PROBE', policy_reference=ref)))\n")
    trust |= {"backup_dir":str(tmp_path), "probes":{
        phase:{name:[str(python), str(runner), name, CODE] for name in CONSUMERS}
        for phase in ("CANDIDATE", "ACTIVE")}}

    def private(name, payload):
        path = tmp_path / name
        path.touch(mode=0o600)
        path.write_bytes(payload)
        return str(path)

    conf = private("trust.json", canonical(trust).encode())
    cfg = private("config.json", canonical(edits(parse_snapshot(bundles[-1]["snapshot"]))).encode())
    candidate_file = tmp_path / "candidate.json"
    prefix = ["--db", str(pub.store.path), "--environment", "ISOLATED_REHEARSAL", "--scope", SCOPE, "--rehearsal-trust", conf]
    # Use -m on a separate interpreter in production adapters; the fixture
    # subprocess inherits this repository on its import path.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
        assert config_cli.main([*prefix, "preview", "--config", cfg, "--out", str(candidate_file)]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "PREVIEW_ONLY"
        assert len(pub.store.history(SCOPE)) == 9
        candidate = json.loads(candidate_file.read_bytes())
        auth, sig = sign(key, candidate)
        approval_file = private("approval.json", canonical(auth).encode())
        signature = private("approval.sig", bytes.fromhex(sig))
        command = [*prefix, "publish", "--candidate", str(candidate_file), "--approval", approval_file, "--signature", signature]
        assert config_cli.main(command) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["status"] == "ACTIVE_READBACK_PASS"
        assert len(set(receipt["consumers"])) == 6
        assert config_cli.main(command) == 0
        assert json.loads(capsys.readouterr().out) == receipt
        assert len(pub.store.release_history(SCOPE)) == 4


def test_frozen_release_survives_owner_alias_admission_but_live_preview_uses_owner(isolated, tmp_path, monkeypatch):
    pub, bundles, _, key, trust = isolated
    candidate, _ = preview(pub, SCOPE, edits(parse_snapshot(bundles[-1]["snapshot"])), trust["listings"], code_sha=CODE)
    run_publish(pub, candidate, sign(key, candidate), trust, tmp_path)
    monkeypatch.setitem(IDENTITIES, ("FUTUREALIAS:US", "USD"), "SEC_CIK_0001045810")
    actual = PolicyService(pub.store).get_active_policy_context(SCOPE)
    assert actual["policy_reference"] == candidate["target"]
    assert "FUTUREALIAS:US" not in actual["snapshot"]["target_symbols"]
    again, _ = preview(pub, SCOPE, edits(parse_snapshot(bundles[-1]["snapshot"])), trust["listings"], code_sha=CODE)
    assert "FUTUREALIAS:US" in again["inputs"]["target_bindings"]["symbols"]


def test_concurrent_candidates_do_not_both_activate(isolated, tmp_path):
    pub, bundles, _, key, trust = isolated
    cfg = edits(parse_snapshot(bundles[-1]["snapshot"]))
    candidates = [preview(pub, SCOPE, cfg, trust["listings"], code_sha=CODE)[0] for _ in range(2)]
    approvals = [sign(key, candidate) for candidate in candidates]
    shared = Barrier(2, timeout=10)

    def checked(candidate, bundle):
        result = candidate_checks(candidate, bundle)
        shared.wait()
        return result

    def attempt(index):
        try:
            return run_publish(pub, candidates[index], approvals[index], trust, tmp_path, checks=checked)["status"]
        except PolicyError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert outcomes.count("ACTIVATED_READBACK_PENDING") == 1
    assert set(outcomes) & {"STALE_POLICY_CANDIDATE", "BACKUP_PREDECESSOR_MISMATCH"}
    assert len(pub.store.release_history(SCOPE)) == 4
