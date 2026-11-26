"""One authorized transaction over the original policy tables and release owner."""
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field

from .configuration import TargetConfig, compile_config, from_snapshot, inherited_policy, owner_listings, target_bindings
from .contracts import Contract, canonical, digest, parse_snapshot
from .formal_publisher import CONSUMERS, store_identity
from .parser import PolicyError
from .service import PolicyService
from .store import clock

HASH = r"^[0-9a-f]{64}$"
CANDIDATE_VERSION = "wp.policy.fast-release.v1"


class Approval(Contract):
    contract: Literal["wp.policy.fast-release.v1"]
    operation: Literal["PUBLISH_L1", "ROLLBACK"]
    candidate_sha256: str = Field(pattern=HASH)
    environment: Literal["PRODUCTION", "ISOLATED_REHEARSAL"]
    scope: str
    store_identity: str = Field(pattern=HASH)
    approved_by: str
    approved_at: str
    approval_id: str


def _state(store, db, scope):
    head = db.execute("SELECT coalesce(max(version),0) FROM policy_bundles WHERE scope=?", (scope,)).fetchone()[0]
    releases = [store._read(row) for row in db.execute(
        "SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence", (scope,))]
    active = store._bundle(db, scope, releases[-1]["bundle_id"]) if releases else None
    if active:
        from .formal_publisher import verify_formal_release
        verify_formal_release(store, db, active, releases[-1])
    return head, releases, active


def _base(store, db, scope, bundle_id):
    row = db.execute("SELECT * FROM policy_bundles WHERE scope=? AND bundle_id=?", (scope, bundle_id)).fetchone()
    base = store._read(row)
    if base["snapshot"]["schema_version"] != "wp.policy.v2":
        raise PolicyError("APPROVED_MARKDOWN_POLICY_BASE_REQUIRED")
    bundle = store._bundle(db, scope, bundle_id)
    return parse_snapshot(bundle["snapshot"]), bytes(row["philosophy"]), bytes(row["policy"])


def _make_bundle(store, db, scope, inputs, request_id, head, at):
    base, ph, template = _base(store, db, scope, inputs["base_id"])
    config = TargetConfig.model_validate(inputs["config"])
    if inputs["config"] != config.model_dump(mode="json"):
        raise PolicyError("NONCANONICAL_POLICY_CONFIG")
    if inputs["inherited_policy"] != inherited_policy(base):
        raise PolicyError("L2_INHERITED_POLICY_CHANGE")
    markdown, snapshot = compile_config(base, ph, template, config, inputs["listings"], version=head+1,
                                        created_at=at, bindings=inputs["target_bindings"])
    bundle_id = "policy-" + digest(dict(scope=scope, version=head+1, request_id=request_id, input=inputs, at=at))[:24]
    value = dict(scope=scope, bundle_id=bundle_id, bundle_version=head+1, predecessor_version=head,
                 source_hashes=snapshot.source_hashes, source_files={"philosophy":"philosophy.md", "policy":"policy.md"},
                 snapshot=snapshot.model_dump(mode="json"), structured_sha256=digest(snapshot), created_at=at,
                 content_approved_on=snapshot.content_approved_on, content_status=snapshot.content_status,
                 deployment_status="NOT_DEPLOYED", effective_at=None, configuration=inputs)
    return value, ph, markdown


def verify_config_bundle(store, db, scope, bundle_id, row, value):
    expected, ph, markdown = _make_bundle(store, db, scope, value["configuration"],
        row["request_id"], row["version"]-1, value["created_at"])
    if (value != expected or row["request_sha"] != digest(expected)
            or value["bundle_id"] != bundle_id or row["philosophy"] != ph or row["policy"] != markdown):
        raise PolicyError("CONFIG_DERIVATIVE_MISMATCH")


def _approval(approval, signature, key_hex, expected, *, at):
    auth = Approval.model_validate(approval)
    checked = {key: getattr(auth, key) for key in expected}
    if checked != expected or not auth.approved_by.strip() or not auth.approval_id.strip():
        raise PolicyError("APPROVAL_TARGET_MISMATCH")
    if clock(auth.approved_at) > clock(at):
        raise PolicyError("APPROVAL_CLOCK_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)).verify(bytes.fromhex(signature),
            canonical(auth).encode())
    except (InvalidSignature, ValueError) as exc:
        raise PolicyError("SIGNED_USER_APPROVAL_REQUIRED") from exc
    return auth


def _reference(bundle):
    return dict(scope=bundle["scope"], bundle_id=bundle["bundle_id"], bundle_version=bundle["bundle_version"],
                bundle_sha256=digest(bundle), structured_sha256=bundle["structured_sha256"])


def _before(head, releases, active):
    return dict(head=head, release_sequence=len(releases), active=_reference(active) if active else None)


def _sealed_digest(candidate):
    return digest(candidate)


def fast_store_identity(store):
    info = store.path.stat()
    return digest({"policy_store": store_identity(store), "device": info.st_dev, "inode": info.st_ino})


def _identity_keys(candidate):
    return {k: candidate[k] for k in ("environment", "scope", "store_identity")}


def preview(publisher, scope, config, listings, *, code_sha, rollback_to=None):
    """Read a single SQLite snapshot and return frozen artifacts in memory."""
    store = publisher.store
    at = publisher.now()
    if not isinstance(code_sha, str) or len(code_sha) != 40 or any(c not in "0123456789abcdef" for c in code_sha):
        raise PolicyError("INSTALLED_CODE_SHA_REQUIRED")
    with store.connection() as db:
        head, releases, active = _state(store, db, scope)
        if not active or any(row["environment"] != publisher.environment or row.get("synthetic")
                             for row in releases):
            raise PolicyError("FORMAL_PREDECESSOR_REQUIRED")
        candidate = dict(contract=CANDIDATE_VERSION, environment=publisher.environment, scope=scope,
                         store_identity=fast_store_identity(store), before=_before(head, releases, active),
                         code_sha=code_sha, created_at=at)
        if rollback_to:
            if rollback_to == active["bundle_id"] or not any(r["bundle_id"] == rollback_to for r in releases):
                raise PolicyError("ROLLBACK_REQUIRES_PREVIOUSLY_RELEASED_VERSION")
            bundle = store._bundle(db, scope, rollback_to)
            candidate |= dict(operation="ROLLBACK", target=_reference(bundle))
            return candidate, None
        normalized = TargetConfig.model_validate(config)
        base_id = active.get("configuration", {}).get("base_id", active["bundle_id"])
        old = from_snapshot(parse_snapshot(active["snapshot"]))
        changes = semantic_diff(old, normalized)
        admitted = owner_listings(listings, clock(at).date(), verify_owner=True)
        base, _, _ = _base(store, db, scope, base_id)
        inputs = dict(base_id=base_id, config=normalized.model_dump(mode="json"),
                      inherited_policy=inherited_policy(base), target_bindings=target_bindings(base, normalized, admitted),
                      listings=[admitted[key].model_dump(mode="json") for key in sorted(admitted)])
        candidate |= dict(operation="PUBLISH_L1", inputs=inputs, change_level="L1", changes=changes)
        request_id = "config-" + digest(candidate)
        bundle, ph, markdown = _make_bundle(store, db, scope, inputs, request_id, head, at)
        candidate |= dict(request_id=request_id, target=_reference(bundle), source_hashes=bundle["source_hashes"],
                          config_sha256=digest(inputs), source_files=bundle["source_files"])
        return candidate, {"policy.md":markdown, "philosophy.md":ph}


def semantic_diff(old, new):
    changes = {}
    for key in ("neutral", "etf_internal", "direct_issuer_targets", "unallocated"):
        before, after = (src.model_dump(mode="json")[key] for src in (old, new))
        if before != after:
            changes[key] = {"before": before, "after": after}
    return changes


def _verify_receipts(receipts, candidate, phase, reference=None):
    if set(receipts) != CONSUMERS:
        raise PolicyError("CONSUMER_SET_INCOMPLETE")
    for name, value in receipts.items():
        if (value.get("consumer") != name or value.get("phase") != phase
                or value.get("code_sha") != candidate["code_sha"]
                or not value.get("runtime_id")
                or value.get("policy_reference") != (reference or candidate["target"])
                or (candidate["environment"] == "PRODUCTION" and phase == "CANDIDATE" and name in {"web","mcp"}
                    and value.get("running_reference") != candidate["before"]["active"])):
            raise PolicyError("CONSUMER_REFERENCE_OR_RUNTIME_MISMATCH")


def verify_config_release(store, db, bundle, release):
    proof = release["policy_config_proof"]
    candidate = proof["candidate"]
    if (candidate["contract"] != CANDIDATE_VERSION or candidate["target"] != _reference(bundle)
            or _identity_keys(candidate) != {k: release[k] for k in ("scope", "environment", "store_identity")}
            or fast_store_identity(store) != candidate["store_identity"]
            or release["action"] != ("ROLLBACK" if candidate["operation"] == "ROLLBACK" else "RELEASE")
            or release["recorded_at"] != release["effective_at"] or release["deployed_at"] != release["recorded_at"]
            or release["synthetic"] is not False
            or release["predecessor_sequence"] != candidate["before"]["release_sequence"]
            or release["request_sha256"] != _sealed_digest(candidate)
            or release["bundle_sha256"] != digest(bundle)):
        raise PolicyError("CONFIG_RELEASE_PROOF_INVALID")
    auth = _approval(proof["approval"], proof["signature"], proof["key"], _identity_keys(candidate) | {
        "operation":candidate["operation"], "candidate_sha256":_sealed_digest(candidate)}, at=release["recorded_at"])
    if clock(auth.approved_at) < clock(candidate["created_at"]) or auth.approval_id != release["release_approval_ref"]:
        raise PolicyError("APPROVAL_CLOCK_OR_REF_INVALID")
    _verify_receipts(proof["candidate_checks"], candidate, "CANDIDATE")
    if proof["backup"].get("status") != "VERIFIED_CONSISTENT_BACKUP" or not proof["backup"].get("sha256"):
        raise PolicyError("CONFIG_RELEASE_BACKUP_PROOF_INVALID")
    earlier = [store._read(r) for r in db.execute(
        "SELECT * FROM policy_releases WHERE scope=? AND sequence<=? ORDER BY sequence",
        (bundle["scope"], release["predecessor_sequence"]))]
    before = candidate["before"]["active"]
    if (len(earlier) != release["predecessor_sequence"] or not earlier or not before
            or earlier[-1]["bundle_sha256"] != before["bundle_sha256"]
            or earlier[-1]["bundle_id"] != before["bundle_id"]
            or clock(candidate["created_at"]) < clock(earlier[-1]["recorded_at"])
            or any(r["environment"] != release["environment"] or r["synthetic"] for r in earlier)):
        raise PolicyError("CONFIG_RELEASE_PREDECESSOR_MISMATCH")
    events = [store._read(r) for r in db.execute(
        "SELECT * FROM policy_events WHERE scope=? AND bundle_id=? AND state='APPROVED'",
        (bundle["scope"],bundle["bundle_id"]))]
    if release["action"] == "ROLLBACK":
        if not any(r["bundle_id"] == bundle["bundle_id"] and r["bundle_sha256"] == digest(bundle) for r in earlier[:-1]):
            raise PolicyError("ROLLBACK_REQUIRES_PREVIOUSLY_RELEASED_VERSION")
    elif not any(event.get("authorization") == {
        "environment": release["environment"], "candidate_sha256": _sealed_digest(candidate),
        "approval_sha256": digest(auth)} and event["approval_ref"] == auth.approval_id
        and event["recorded_at"] == release["recorded_at"] for event in events):
        raise PolicyError("CONFIG_STRUCTURED_APPROVAL_REQUIRED")


def _matching_release(candidate, releases):
    return next((r for r in releases if r.get("request_sha256") == _sealed_digest(candidate)
                 and r.get("policy_config_proof")), None)


def _compare_live(publisher, candidate):
    with publisher.store.connection() as db:
        head, releases, active = _state(publisher.store, db, candidate["scope"])
    prior = _matching_release(candidate, releases)
    if not prior and _before(head, releases, active) != candidate["before"]:
        raise PolicyError("STALE_POLICY_CANDIDATE")
    return prior


def publish(publisher, candidate, approval, signature, trust, *, check_candidates, read_consumers, backup):
    """Precheck then append DRAFT/VALIDATED/APPROVED/RELEASE in one CAS transaction."""
    store = publisher.store
    if publisher.environment == "PRODUCTION":
        # Do not let a direct Python caller substitute a trust root or callbacks.
        from .config_cli import _backup, _probes, production_trust
        trust = production_trust(store, candidate["scope"])
        check_candidates = lambda c,b: _probes(trust, "CANDIDATE", c, b)
        read_consumers = lambda c: _probes(trust, "ACTIVE", c)
        backup = lambda c: _backup(store, trust["backup_dir"], c)
    code_sha = trust["code_sha"]
    if (_identity_keys(candidate) != dict(scope=trust["scope"], environment=publisher.environment,
                store_identity=fast_store_identity(store)) or candidate["code_sha"] != code_sha
            or candidate["contract"] != CANDIDATE_VERSION
            or trust["environment"] != publisher.environment
            or trust["store_identity"] != fast_store_identity(store)
            or candidate["operation"] not in {"PUBLISH_L1", "ROLLBACK"}):
        raise PolicyError("FAST_RELEASE_INSTALLATION_BINDING_MISMATCH")
    now = publisher.now()
    key = trust["approval_key"]
    auth = _approval(approval, signature, key, _identity_keys(candidate) | {
        "operation":candidate["operation"], "candidate_sha256":_sealed_digest(candidate)}, at=now)
    if auth.approved_by != trust["approved_by"] or clock(auth.approved_at) < clock(candidate["created_at"]):
        raise PolicyError("APPROVAL_SUBJECT_OR_CLOCK_INVALID")
    prior = _compare_live(publisher, candidate)
    if not prior:
        scope = candidate["scope"]
        effective_date = clock(now).date()
        if candidate["operation"] == "PUBLISH_L1":
            admitted = owner_listings(trust["listings"], effective_date, verify_owner=True)
            if candidate["inputs"]["listings"] != [admitted[k].model_dump(mode="json") for k in sorted(admitted)]:
                raise PolicyError("LISTING_OWNER_DATA_CHANGED")
            with store.connection() as db:
                inputs = candidate["inputs"]
                base, _, _ = _base(store, db, scope, inputs["base_id"])
                if inputs["target_bindings"] != target_bindings(base, TargetConfig.model_validate(inputs["config"]), admitted):
                    raise PolicyError("TARGET_OWNER_BINDING_CHANGED")
                bundle, _, _ = _make_bundle(store, db, scope, candidate["inputs"], candidate["request_id"],
                                             candidate["before"]["head"], candidate["created_at"])
        else:
            bundle = store.read_bundle(scope, candidate["target"]["bundle_id"])
        if candidate["target"] != _reference(bundle):
            raise PolicyError("FROZEN_CONFIG_CANDIDATE_MISMATCH")
        evidence = check_candidates(candidate, bundle)
        _verify_receipts(evidence, candidate, "CANDIDATE")
        backup_receipt = backup(candidate)
        if backup_receipt.get("status") != "VERIFIED_CONSISTENT_BACKUP" or not backup_receipt.get("sha256"):
            raise PolicyError("POLICY_RELEASE_BACKUP_REQUIRED")
        with store.connection(write=True) as db:
            head, releases, active = _state(store, db, scope)
            prior = _matching_release(candidate, releases)
            if not prior:
                if _before(head, releases, active) != candidate["before"]:
                    raise PolicyError("STALE_POLICY_CANDIDATE")
                prior = _commit(publisher, db, candidate, auth, key, signature, evidence, backup_receipt)
    if prior["policy_config_proof"]["approval"] != auth.model_dump() or prior["policy_config_proof"]["signature"] != signature:
        raise PolicyError("APPROVAL_REPLAY_MISMATCH")
    try:
        current = PolicyService(store).get_active_policy_context(candidate["scope"])
        if current.get("policy_reference") != candidate["target"]:
            return dict(status="ACTIVATION_HISTORY_RECORDED_CURRENT_REFERENCE_CHANGED", release=prior)
        receipts = read_consumers(candidate)
        _verify_receipts(receipts, candidate, "ACTIVE")
        if PolicyService(store).get_active_policy_context(candidate["scope"]).get("policy_reference") != candidate["target"]:
            raise PolicyError("ACTIVE_CHANGED_DURING_READBACK")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        from re import fullmatch
        code = str(exc)
        reason = code if isinstance(exc, PolicyError) and fullmatch("[A-Z0-9_]+", code) else "CONSUMER_PROBE_UNAVAILABLE"
        return dict(status="ACTIVATED_READBACK_PENDING", release=prior, reason=reason)
    return dict(status="ACTIVE_READBACK_PASS", release=prior, consumers=receipts)


def _commit(publisher, db, candidate, auth, key, signature, evidence, backup_receipt):
    store = publisher.store
    scope = candidate["scope"]
    at = publisher.now()
    head = candidate["before"]["head"]
    sealed = _sealed_digest(candidate)
    if candidate["operation"] == "PUBLISH_L1":
        request_id = candidate["request_id"]
        owner_listings(candidate["inputs"]["listings"], clock(at).date(), verify_owner=True)
        bundle, ph, policy = _make_bundle(store, db, scope, candidate["inputs"], request_id, head, candidate["created_at"])
        if (candidate["target"] != _reference(bundle) or bundle["source_hashes"] != candidate["source_hashes"]
                or candidate["config_sha256"] != digest(candidate["inputs"])
                or candidate["change_level"] != "L1"):
            raise PolicyError("FROZEN_CONFIG_CANDIDATE_MISMATCH")
        predecessor = store._bundle(db, scope, candidate["before"]["active"]["bundle_id"])
        base = predecessor.get("configuration", {}).get("base_id", predecessor["bundle_id"])
        if (base != candidate["inputs"]["base_id"] or candidate["changes"] != semantic_diff(
                from_snapshot(parse_snapshot(predecessor["snapshot"])), TargetConfig.model_validate(candidate["inputs"]["config"]))):
            raise PolicyError("CANDIDATE_SEMANTIC_DIFF_INVALID")
        db.execute("INSERT INTO policy_bundles VALUES(?,?,?,?,?,?,?,?,?)",
            (scope, head+1, bundle["bundle_id"], request_id, digest(bundle), canonical(bundle), digest(bundle), ph, policy))
        store._append(db, scope, bundle["bundle_id"], "DRAFT", request_id, candidate["created_at"], None)
        store._append(db, scope, bundle["bundle_id"], "VALIDATED", request_id+"-validated", at, None)
        store._append(db, scope, bundle["bundle_id"], "APPROVED", request_id+"-approved", at, auth.approval_id,
            authorization=dict(environment=publisher.environment, candidate_sha256=sealed, approval_sha256=digest(auth)))
    else:
        bundle = store._bundle(db, scope, candidate["target"]["bundle_id"])
    release = dict(scope=scope, bundle_id=bundle["bundle_id"], bundle_sha256=digest(bundle),
        action="ROLLBACK" if candidate["operation"] == "ROLLBACK" else "RELEASE", recorded_at=at, effective_at=at,
        deployed_at=at, release_approval_ref=auth.approval_id, synthetic=False, environment=publisher.environment,
        predecessor_sequence=candidate["before"]["release_sequence"], request_id="release-"+sealed,
        request_sha256=sealed, store_identity=fast_store_identity(store), policy_config_proof=dict(
            candidate=candidate, approval=auth.model_dump(), key=key, signature=signature,
            candidate_checks=evidence, backup=backup_receipt))
    verify_config_release(store, db, bundle, release)
    db.execute("INSERT INTO policy_releases VALUES(?,?,?,?,?)", (scope, release["predecessor_sequence"]+1,
        bundle["bundle_id"], canonical(release), digest(release)))
    return release
