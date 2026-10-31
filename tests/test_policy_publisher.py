"""Formal publishing on fictional policy bytes and isolated databases only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from tests.test_investment_policy import staged, AT, LATER, PHILOSOPHY, POLICY  # noqa: F401
from backend.services.investment_policy.contracts import canonical, digest
from backend.services.investment_policy.formal_publisher import (
    Authorization, Installation, PolicyPublisher, CONSUMERS, store_identity,
)
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import clock


def approval(store, bundle, operation="APPROVE_STRUCTURED", sequence=0, active=None, **changes):
    return dict(decision="APPROVED", operation=operation, environment="ISOLATED_REHEARSAL",
        approval_id=f"TEST-HUMAN-{bundle['bundle_version']}-{operation}", approved_by="SYNTHETIC_OPERATOR",
        approved_at=LATER, human_confirmation=True, scope=bundle["scope"], store_identity=store_identity(store),
        bundle_id=bundle["bundle_id"], bundle_sha256=digest(bundle), structured_sha256=bundle["structured_sha256"],
        installation_sha256=None if operation=="APPROVE_STRUCTURED" else digest(installation(bundle)),
        expected_sequence=sequence, expected_active_bundle_sha256=active, **changes)


def installation(bundle, **changes):
    value=dict(receipt_id="SYNTHETIC-INSTALL", environment="ISOLATED_REHEARSAL", installed_at=LATER,
        code_sha="a"*40, scope=bundle["scope"], bundle_sha256=digest(bundle),
        consumer_bundle_hashes={k:digest(bundle) for k in CONSUMERS})
    value.update(changes)
    return value


@pytest.fixture
def validated(staged):
    store,args,bundle=staged
    store.transition(scope=bundle["scope"], bundle_id=bundle["bundle_id"], state="VALIDATED", expected_state="DRAFT",
                     request_id="validate", at=AT)
    return store,args,bundle,PolicyPublisher(store,environment="ISOLATED_REHEARSAL",now=lambda:LATER)


@pytest.fixture
def formal_portfolio(tmp_path,monkeypatch):
    from backend.services.investment_policy.store import PolicyStore,utc_now
    tmp_path.chmod(0o700)
    paths={}
    for role,raw in [('philosophy',PHILOSOPHY),('policy',POLICY)]:
        paths[role]=tmp_path/(role+'.md');paths[role].write_bytes(raw.replace(b'2030-12-01',b'2020-01-01'))
    store=PolicyStore(tmp_path/'formal.sqlite');store.init_candidate()
    b=store.stage(scope='portfolio:7',version=1,philosophy_path=paths['philosophy'],policy_path=paths['policy'],
        expected_hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()},
        request_id='formal-stage',expected_head_version=0,created_at=utc_now())
    store.transition(scope=b['scope'],bundle_id=b['bundle_id'],state='VALIDATED',expected_state='DRAFT',request_id='formal-validation',at=utc_now())
    publisher=PolicyPublisher(store,environment='ISOLATED_REHEARSAL')
    auth=approval(store,b);auth['approved_at']=utc_now()
    publisher.approve(authorization=auth,request_id='formal-approval')
    install=installation(b,installed_at=utc_now())
    release=approval(store,b,'ACTIVATE');release.update(approved_at=utc_now(),installation_sha256=digest(install))
    publisher.release(authorization=release,installation=install,request_id='formal-release')
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH',str(store.path));monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE',b['scope'])
    return store,b,tmp_path


def activate(validated):
    store,args,bundle,publisher=validated
    publisher.approve(authorization=approval(store,bundle),request_id="approve-1")
    request=dict(authorization=approval(store,bundle,"ACTIVATE"), installation=installation(bundle),request_id="release-1")
    return publisher.release(**request),request


def test_content_approval_and_validated_do_not_activate(validated):
    store,args,bundle,publisher=validated
    assert PolicyService(store).get_active_policy_context(bundle["scope"],as_of=LATER)["status"]=="NOT_ACTIVE"
    with pytest.raises(PolicyError,match="FORMAL_STRUCTURED_APPROVAL"):
        publisher.release(authorization=approval(store,bundle,"ACTIVATE"),installation=installation(bundle),request_id="no-human")
    assert store.release_history(bundle["scope"])==[]


def test_real_workflow_idempotence_original_history_and_restart(validated):
    store,args,bundle,publisher=validated
    old=store.read_bundle(bundle["scope"],bundle["bundle_id"])
    receipt,request=activate(validated)
    assert not receipt["synthetic"] and receipt["environment"]=="ISOLATED_REHEARSAL"
    assert publisher.release(**request)==receipt
    assert publisher.approve(authorization=approval(store,bundle),request_id="approve-1")["state"]=="APPROVED"
    assert len(store.release_history(bundle["scope"]))==1
    assert store.read_bundle(bundle["scope"],bundle["bundle_id"])==old
    from backend.services.investment_policy.store import PolicyStore
    reopened=PolicyService(PolicyStore(store.path)).get_active_policy_context(bundle["scope"],as_of=LATER)
    assert reopened["status"]=="ACTIVE" and reopened["policy_reference"]["bundle_sha256"]==digest(bundle)
    assert publisher.lifecycle(bundle["scope"])["bundles"][bundle["bundle_id"]]=="ACTIVE"
    altered=deepcopy(request);altered["installation"]["receipt_id"]="changed"
    with pytest.raises(PolicyError,match="REQUEST_ID_CONFLICT"):publisher.release(**altered)


@pytest.mark.parametrize("field,value",[("bundle_sha256","0"*64),("structured_sha256","0"*64),
    ("scope","OTHER"),("store_identity","0"*64),("operation","ROLLBACK"),
    ("environment","PRODUCTION"),("approved_at","2099-01-01T00:00:00Z")])
def test_wrong_approval_target_or_clock_cannot_approve(validated,field,value):
    store,args,bundle,publisher=validated
    auth=approval(store,bundle);auth[field]=value
    with pytest.raises((PolicyError,ValidationError)):publisher.approve(authorization=auth,request_id="bad")
    assert store.history(bundle["scope"])[-1]["state"]=="VALIDATED"


def test_unconfirmed_or_missing_identity_and_bad_clock(validated):
    store,args,bundle,publisher=validated
    for changes in [dict(human_confirmation=False),dict(approved_by=""),dict(approved_at="2031-01-02"),dict(decision="PENDING")]:
        auth=approval(store,bundle);auth.update(changes)
        with pytest.raises((ValidationError,PolicyError)):publisher.approve(authorization=auth,request_id="bad")


def test_generic_legacy_approval_reference_not_formal_authorization(validated):
    store,args,bundle,publisher=validated
    store.transition(scope=bundle["scope"],bundle_id=bundle["bundle_id"],state="APPROVED",expected_state="VALIDATED",
                     request_id="string-only",at=LATER,approval_ref="JUST_A_STRING")
    with pytest.raises(PolicyError,match="FORMAL_STRUCTURED_APPROVAL"):
        publisher.release(authorization=approval(store,bundle,"ACTIVATE"),installation=installation(bundle),request_id="bad-release")


def test_installation_proof_and_versions_required(validated):
    store,args,bundle,publisher=validated
    publisher.approve(authorization=approval(store,bundle),request_id="approve")
    for changed in [dict(scope="OTHER"),dict(environment="PRODUCTION"),dict(installed_at="2099-01-01T00:00:00Z")]:
        with pytest.raises((PolicyError,ValidationError)):
            publisher.release(authorization=approval(store,bundle,"ACTIVATE"),installation=installation(bundle,**changed),request_id="bad")
    with pytest.raises(ValidationError):Installation.model_validate(installation(bundle,consumer_bundle_hashes={"web":digest(bundle)}))
    assert store.release_history(bundle["scope"])==[]


def test_concurrent_cas_one_winner_and_failed_transaction_leaves_no_receipt(validated):
    store,args,bundle,publisher=validated
    publisher.approve(authorization=approval(store,bundle),request_id="approve")
    def run(index):
        try:return publisher.release(authorization=approval(store,bundle,"ACTIVATE"),installation=installation(bundle),request_id=f"race-{index}")["action"]
        except PolicyError as exc:return str(exc)
    with ThreadPoolExecutor() as pool:outcomes=list(pool.map(run,range(2)))
    assert sorted(outcomes)==["RELEASE","RELEASE_CAS_CONFLICT"]
    assert len(store.release_history(bundle["scope"]))==1


def test_authorized_rollback_supersession_and_pit(validated):
    store,args,b1,publisher=validated
    first,_=activate(validated)
    b2=store.stage(**{**args,"version":2,"expected_head_version":1,"request_id":"stage-2"})
    store.transition(scope=b2["scope"],bundle_id=b2["bundle_id"],state="VALIDATED",expected_state="DRAFT",request_id="validate-2",at=AT)
    publisher.approve(authorization=approval(store,b2),request_id="approve-2")
    publisher.now=lambda:(clock(LATER)+timedelta(seconds=1)).isoformat()
    second=publisher.release(authorization=approval(store,b2,"ACTIVATE",1,digest(b1)),installation=installation(b2),request_id="release-2")
    before_rollback=store.history(b1["scope"])
    assert publisher.lifecycle(b1["scope"])["bundles"][b1["bundle_id"]]=="SUPERSEDED"
    with pytest.raises(PolicyError,match="ACTIVE_VERSION_CAS"):
        publisher.release(authorization=approval(store,b1,"ROLLBACK",2,"0"*64),installation=installation(b1),request_id="bad-rollback")
    publisher.now=lambda:(clock(LATER)+timedelta(seconds=2)).isoformat()
    final=publisher.release(authorization=approval(store,b1,"ROLLBACK",2,digest(b2)),installation=installation(b1),request_id="rollback")
    assert final["action"]=="ROLLBACK" and len(store.release_history(b1["scope"]))==3
    assert store.history(b1["scope"])==before_rollback
    service=PolicyService(store)
    assert service.get_active_policy_context(b1["scope"],as_of=LATER)["release"]==first
    assert service.get_active_policy_context(b1["scope"],as_of=second["recorded_at"])["release"]==second
    assert service.get_active_policy_context(b1["scope"],as_of=final["recorded_at"])["release"]==final
    assert publisher.lifecycle(b1["scope"])["rolled_back_releases"]==["rollback"]
    assert publisher.lifecycle(b1["scope"])["bundles"][b2["bundle_id"]]=="ROLLED_BACK"


def test_never_released_target_cannot_be_rollback(validated):
    store,args,bundle,publisher=validated
    publisher.approve(authorization=approval(store,bundle),request_id="approve")
    with pytest.raises(PolicyError,match="PREVIOUSLY_RELEASED"):
        publisher.release(authorization=approval(store,bundle,"ROLLBACK"),installation=installation(bundle),request_id="bad")


def test_frozen_sources_are_rederived_not_changed_files_or_forged_json(validated):
    store,args,bundle,publisher=validated
    Path(args["policy_path"]).write_bytes(b"MUTATED EDITING FILE")
    activate(validated)
    assert store.read_bundle(bundle["scope"],bundle["bundle_id"])==bundle
    with pytest.raises(PolicyError,match="SOURCE_SHA256_MISMATCH"):
        store.stage(**{**args,"version":2,"expected_head_version":1,"request_id":"bad-stage"})
    with pytest.raises(sqlite3.IntegrityError,match="IMMUTABLE"):
        with store.connection(write=True) as db:db.execute("UPDATE policy_releases SET payload='{}'")


def test_cli_requires_private_confirmed_artifact_and_no_private_source_output(validated,capsys):
    store,args,bundle,publisher=validated
    from backend.services.investment_policy.publish_cli import main
    auth=approval(store,bundle);p=store.path.parent/"human-approval.json";raw=canonical(auth).encode();p.write_bytes(raw);p.chmod(0o600)
    opts=["--db",str(store.path),"--environment","ISOLATED_REHEARSAL","approve","--authorization",str(p),"--request-id","cli-approve"]
    with pytest.raises(PolicyError,match="CONFIRMATION"):
        main(opts+["--confirm-authorization-sha256","0"*64])
    # CLI real clock is before fictional approval; use the same injected test clock.
    import backend.services.investment_policy.publish_cli as cli
    original=cli.PolicyPublisher
    try:
        cli.PolicyPublisher=lambda store,environment:original(store,environment=environment,now=lambda:LATER)
        main(opts+["--confirm-authorization-sha256",hashlib.sha256(raw).hexdigest()])
    finally:cli.PolicyPublisher=original
    output=capsys.readouterr().out
    assert "APPROVED" in output and str(store.path) not in output and "snapshot" not in output


def test_formal_receipt_keeps_original_consumer_qualification_unknown(validated,monkeypatch):
    store,args,bundle,publisher=validated
    activate(validated)
    monkeypatch.setenv("WEALTHPILOT_POLICY_DB_PATH",str(store.path))
    monkeypatch.setenv("WEALTHPILOT_POLICY_SCOPE",bundle["scope"])
    # This fixture scope is intentionally not a portfolio; cannot gain another scope.
    from backend.services.investment_policy.runtime import resolve
    assert resolve(7)["mode"]=="UNAVAILABLE"


def test_formal_release_real_original_consumers_and_old_cache_isolation(formal_portfolio,monkeypatch):
    from app import state
    from backend.api.discipline import get_policy_context
    from backend.services.investment_policy.runtime import context,bind,PolicySession,sanitize_loaded
    from backend.services import discipline_service,allocation_service,philosophy_service
    from backend.services.investment_policy.consumers import finalize_output
    from backend.graph.tools import execute_retrieve_principles
    from decision_engine.data_loader import LoadedData,UserProfile,InvestmentRules
    from types import SimpleNamespace
    monkeypatch.setattr(state,'portfolio_id',7)
    with bind(PolicySession(7)):
        expected=context(7)['policy_reference']
        assert get_policy_context()['policy_reference']==expected
        assert discipline_service.get_rules_config()['policy_reference']==expected
        assert philosophy_service.get_philosophy()['policy_reference']==expected
        assert sum(t.mid_ratio for t in allocation_service.get_targets())==1
        for consumer in CONSUMERS:assert context(7,consumer=consumer)['policy_reference']==expected
        loaded=LoadedData(profile=UserProfile(),positions=[],target_position=None,rules=InvestmentRules(.4,.8,.2,1.35),full_discipline_rules={'old_cached_limits':True})
        assert sanitize_loaded(loaded,7).full_discipline_rules=={}
        assert execute_retrieve_principles('old rules')['chunks']==[]
        out=SimpleNamespace(chat_answer='建议买入100股',structured_payload={},actionable=True)
        finalize_output(SimpleNamespace(portfolio_id=7),SimpleNamespace(loaded_data=None),out)
        assert out.structured_payload['policy_reference']==expected
        assert out.structured_payload['policy_precision']=='CANNOT_QUANTIFY'
        assert '100股' not in out.chat_answer


def test_isolated_approval_history_cannot_be_promoted_to_production(validated):
    store,args,b1,publisher=validated
    publisher.approve(authorization=approval(store,b1),request_id='isolated-approval')
    b2=store.stage(**{**args,'version':2,'expected_head_version':1,'request_id':'stage-2'})
    store.transition(scope=b2['scope'],bundle_id=b2['bundle_id'],state='VALIDATED',expected_state='DRAFT',request_id='validate-2',at=AT)
    prod=PolicyPublisher(store,environment='PRODUCTION',now=lambda:LATER)
    auth=approval(store,b2);auth['environment']='PRODUCTION'
    with pytest.raises(PolicyError,match='MIXED_PUBLISH_ENVIRONMENTS'):
        prod.approve(authorization=auth,request_id='attempted-promotion')
    assert store.release_history(b1['scope'])==[]
