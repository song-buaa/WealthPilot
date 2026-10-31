"""Operator migration on synthetic WAL owners, no service or external calls."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from app.shared_sqlite import verify, writer_connection
from backend.services.investment_policy import schema_install as install
from backend.services.investment_policy.colocated import CoLocatedPolicyStore
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.service import PolicyService
from tests.test_policy_colocated import bind_contract


@pytest.fixture
def prepared(tmp_path):
    tmp_path.chmod(0o700)
    database, e1 = tmp_path/"business.db", tmp_path/"e1.db"
    contract = tmp_path/"contract.json"
    with closing(sqlite3.connect(database)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("CREATE TABLE portfolios(id INTEGER PRIMARY KEY); INSERT INTO portfolios VALUES(7);"
                         "CREATE TABLE orders(id INTEGER PRIMARY KEY,payload TEXT);"
                         "INSERT INTO orders VALUES(1,'SYNTHETIC_ORDER_NO_EXECUTION');")
    with closing(sqlite3.connect(e1)) as db:
        db.executescript("CREATE TABLE thesis_versions(id INTEGER,payload TEXT);"
                         "INSERT INTO thesis_versions VALUES(1,'TEST_ONLY_IMMUTABLE');")
    database.chmod(0o600); e1.chmod(0o600)
    old = bind_contract(database, contract)
    folder = Path(__file__).resolve().parents[1]/"pocs/poc03_mac_mini"
    sys.path.insert(0, str(folder))
    import snapshot
    backups = tmp_path/"backups"; backups.mkdir(mode=0o700)
    b, e = backups/"business.db", backups/"e1.db"
    manifest, em = snapshot.backup(database, b), snapshot.backup(e1, e)
    plan = install.make_plan(database=database, contract_path=contract, backup=b, manifest=manifest,
        e1_database=e1, e1_backup=e, e1_manifest=em, code_sha="a"*40)
    now = datetime.now(timezone.utc)
    auth = dict(schema="wp.policy-schema-authorization.v1", operation="INSTALL_POLICY_SCHEMA",
        decision="APPROVED", environment="ISOLATED_REHEARSAL", human_confirmation=True,
        approval_id="TEST_ONLY", approved_by="SYNTHETIC_OPERATOR", user_authorization_reference="TEST_ONLY",
        approved_at=now.isoformat(), expires_at=(now+timedelta(hours=1)).isoformat(),
        plan_sha256=install.digest(plan), quiescence=dict(all_database_clients_stopped=True,
        active_peer_workers=0, components=sorted(install.COMPONENTS),
        stop_receipt_sha256="b"*64, stopped_pids=[]))
    receipts = tmp_path/"receipts"; receipts.mkdir(mode=0o700)
    return dict(plan=plan, authorization=auth, receipt_directory=receipts), old


def test_readonly_plan_then_exact_ddl_owner_reconnect_and_no_active(prepared, monkeypatch):
    args, old = prepared; p = args["plan"]
    dbpath, cp = p["database"]["path"], p["contract_path"]
    assert install.read_audit(dbpath) == p["before"]
    stale = CoLocatedPolicyStore(dbpath, cp)
    result = install.execute(**args)
    assert result["policy_active"] is False and result["policy_rows"] == 0
    assert len(result["objects"]) == 12
    current = verify(dbpath, cp, integrity=True)
    assert current == {**old, "schema_sha256": p["after_schema_sha256"]}
    with pytest.raises(PolicyError, match="CONTRACT_CHANGED"):
        with stale.connection(): pass
    # Original Web/business owner accepts new contract; the old contract fails.
    with closing(writer_connection(dbpath, cp)) as db:
        assert db.execute("SELECT payload FROM orders").fetchone()[0] == "SYNTHETIC_ORDER_NO_EXECUTION"
    oldpath = Path(cp).parent/"old.json"; install.write_json(oldpath, old)
    with pytest.raises(ValueError, match="SCHEMA_MISMATCH"): verify(dbpath, oldpath)
    store = CoLocatedPolicyStore(dbpath, cp)
    assert PolicyService(store).get_active_policy_context("portfolio:7")["status"] == "NOT_ACTIVE"
    with pytest.raises(PolicyError):
        with store.connection(write=True) as db: db.execute("DELETE FROM orders")
    assert install.execute(**args) == result  # same operation does not add rows/objects
    assert install.read_audit(p["e1_database"]["path"]) == p["e1_before"]


def test_mid_ddl_failure_rolls_back_and_explicit_retry(prepared, monkeypatch):
    args, _ = prepared; p = args["plan"]
    original = install._ddl
    def fail(db, required):
        # Fail after real first table DDL in the same transaction.
        db.execute(next(iter(required.values())))
        raise RuntimeError("INJECTED_DDL_FAILURE")
    monkeypatch.setattr(install, "_ddl", fail)
    with pytest.raises(RuntimeError): install.execute(**args)
    assert install.read_audit(p["database"]["path"]) == p["before"]
    assert install.load_contract(p["contract_path"]) == p["contract"]
    monkeypatch.setattr(install, "_ddl", original)
    with pytest.raises(PolicyError, match="RECOVERY_REQUIRED"): install.execute(**args)
    authorize_recovery(args)
    assert install.execute(**args, recover=True)["policy_rows"] == 0


def test_postcommit_contract_failure_fail_closed_and_forward_resume(prepared, monkeypatch):
    args, _ = prepared; p = args["plan"]; original = install.write_json
    def fail(path, value, **kw):
        if str(path) == p["contract_path"]: raise OSError("INJECTED_CONTRACT_FAILURE")
        original(path, value, **kw)
    monkeypatch.setattr(install, "write_json", fail)
    with pytest.raises(OSError): install.execute(**args)
    with pytest.raises(ValueError, match="SCHEMA_MISMATCH"):
        verify(p["database"]["path"], p["contract_path"])
    monkeypatch.setattr(install, "write_json", original)
    authorize_recovery(args)
    assert install.execute(**args, recover=True)["status"] == "POLICY_SCHEMA_INSTALLED_NOT_ACTIVE"
    verify(p["database"]["path"], p["contract_path"])


def authorize_recovery(args):
    journal = install.read_json(Path(args["receipt_directory"])/"prepared.json")
    args["authorization"] = {**args["authorization"], "operation": "RESUME_POLICY_SCHEMA",
        "prepared_sha256": install.digest(journal)}


def test_final_receipt_failure_resumes_already_updated_contract(prepared, monkeypatch):
    args, _ = prepared; original = install.write_json
    def fail(path, value, **kw):
        if Path(path).name == "completed.json": raise OSError("FINAL_RECEIPT_FAILURE")
        original(path, value, **kw)
    monkeypatch.setattr(install, "write_json", fail)
    with pytest.raises(OSError): install.execute(**args)
    verify(args["plan"]["database"]["path"], args["plan"]["contract_path"])
    monkeypatch.setattr(install, "write_json", original)
    authorize_recovery(args)
    assert install.execute(**args, recover=True)["policy_rows"] == 0


def test_expired_installation_can_only_resume_with_new_specific_approval(prepared, monkeypatch):
    args, _ = prepared
    start = datetime.now(timezone.utc)
    original = install.write_json
    def fail(path, value, **kw):
        if str(path) == args["plan"]["contract_path"]: raise OSError("AFTER_COMMIT")
        original(path, value, **kw)
    monkeypatch.setattr(install, "write_json", fail)
    with pytest.raises(OSError): install.execute(**args)
    monkeypatch.setattr(install, "write_json", original)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None): return start+timedelta(days=2)
    monkeypatch.setattr(install, "datetime", Later)
    with pytest.raises(PolicyError): install.execute(**args, recover=True)
    authorize_recovery(args)
    args["authorization"].update(approved_at=Later.now().isoformat(),
        expires_at=(Later.now()+timedelta(hours=1)).isoformat(), approval_id="NEW_RECOVERY_ONLY")
    bad = dict(args["authorization"], prepared_sha256="0"*64)
    with pytest.raises(PolicyError, match="RECOVERY_AUTHORIZATION"):
        install.execute(**{**args, "authorization": bad}, recover=True)
    assert install.execute(**args, recover=True)["policy_rows"] == 0


@pytest.mark.parametrize("change", ["human", "plan", "running", "expiry", "identity", "ddl"])
def test_auth_identity_and_clock_fail_closed(prepared, change):
    args, _ = prepared; p = args["plan"]; auth = args["authorization"]
    if change == "human": auth["human_confirmation"] = False
    elif change == "plan": auth["plan_sha256"] = "0"*64
    elif change == "running": auth["quiescence"]["stopped_pids"] = [os.getpid()]
    elif change == "expiry": auth["expires_at"] = "2000-01-01T00:00:00Z"
    elif change == "identity":
        p["host"] = "OTHER_HOST"; auth["plan_sha256"] = install.digest(p)
    else:
        p["required_objects"]["evil"] = "DELETE FROM orders"; auth["plan_sha256"] = install.digest(p)
    with pytest.raises(PolicyError): install.execute(**args)
    assert install.read_audit(p["database"]["path"]) == p["before"]


@pytest.mark.parametrize("target", ["business", "e1", "schema", "contract", "backup"])
def test_change_since_plan_blocks_without_overwriting_legal_history(prepared, target):
    args, _ = prepared; p = args["plan"]
    if target == "backup":
        Path(p["backups"][0]["path"]).write_bytes(b"changed")
    elif target == "contract":
        changed = dict(p["contract"], portfolio_id=8)
        install.write_json(p["contract_path"], changed, replace=True)
    else:
        path = p["e1_database" if target == "e1" else "database"]["path"]
        with closing(sqlite3.connect(path)) as db:
            if target == "e1": db.execute("INSERT INTO thesis_versions VALUES(2,'TEST_ONLY_NEW')")
            elif target == "schema": db.execute("CREATE TABLE concurrent_legal_table(x)")
            else: db.execute("INSERT INTO orders VALUES(2,'LEGAL_NEW_HISTORY')")
            db.commit()
    with pytest.raises((PolicyError, ValueError)): install.execute(**args)
    assert not Path(args["receipt_directory"], "completed.json").exists()


def test_new_plan_after_partial_or_complete_schema_refused(prepared):
    args, _ = prepared; p = args["plan"]
    with closing(sqlite3.connect(p["database"]["path"])) as db:
        db.execute("CREATE TABLE policy_bundles(wrong TEXT)")
    with pytest.raises(ValueError, match="SCHEMA_MISMATCH"):
        install.make_plan(database=p["database"]["path"], contract_path=p["contract_path"],
            backup=p["backups"][0]["path"], manifest=p["backups"][0]["manifest"],
            e1_database=p["e1_database"]["path"], e1_backup=p["backups"][1]["path"],
            e1_manifest=p["backups"][1]["manifest"], code_sha="a"*40)


def test_backup_freshness_and_foreign_keys(prepared):
    args, _ = prepared; item = args["plan"]["backups"][0]
    item["manifest"]["created_at"] = "2000-01-01T00:00:00Z"
    args["authorization"]["plan_sha256"] = install.digest(args["plan"])
    with pytest.raises(PolicyError, match="FRESH_BACKUP"): install.execute(**args)
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("CREATE TABLE p(id PRIMARY KEY); CREATE TABLE c(x REFERENCES p(id)); INSERT INTO c VALUES(7);")
        with pytest.raises(PolicyError, match="FK_FAILED"): install.audit(db)


def test_minimal_cli_plan_apply_recover_protocol(prepared):
    import subprocess
    args, _ = prepared; folder = args["receipt_directory"].parent
    planfile, authfile = folder/"plan.json", folder/"auth.json"
    install.write_json(planfile, args["plan"]); install.write_json(authfile, args["authorization"])
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[1]/"scripts/install_policy_schema.py"),
        "apply", "--plan", str(planfile), "--authorization", str(authfile),
        "--confirm-authorization-sha256", install.file_sha(authfile),
        "--receipt-directory", str(args["receipt_directory"])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)["policy_active"] is False
    again = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert again.returncode == 0 and json.loads(again.stdout) == json.loads(result.stdout)
    command[command.index(install.file_sha(authfile))] = "0"*64
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 1
