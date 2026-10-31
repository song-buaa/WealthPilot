"""Explicit operator publishing, not a Web/MCP writer or a deployment mechanism.

Approval artifacts are operator attestations, not cryptographic identity proof.
Only the trusted local operator may supply them; content approval is insufficient.
The caller must independently obtain the applicable human permission. Tests use
ISOLATED_REHEARSAL and never operate a production store.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, canonical, digest
from .parser import PolicyError
from .store import clock, utc_now

HASH = r"^[0-9a-f]{64}$"
CONSUMERS = {"web", "backend", "peer", "allocation", "discipline", "mcp"}


class Authorization(Contract):
    decision: Literal["APPROVED"]
    operation: Literal["APPROVE_STRUCTURED", "ACTIVATE", "ROLLBACK"]
    environment: Literal["ISOLATED_REHEARSAL", "PRODUCTION"]
    approval_id: str = Field(min_length=1, max_length=128)
    approved_by: str = Field(min_length=1, max_length=128)
    approved_at: str
    human_confirmation: Literal[True]
    scope: str = Field(min_length=1)
    store_identity: str = Field(pattern=HASH)
    bundle_id: str = Field(min_length=1)
    bundle_sha256: str = Field(pattern=HASH)
    structured_sha256: str = Field(pattern=HASH)
    expected_sequence: int = Field(ge=0)
    expected_active_bundle_sha256: str | None = Field(default=None, pattern=HASH)
    installation_sha256: str | None = Field(default=None, pattern=HASH)

    @model_validator(mode="after")
    def clocks(self):
        clock(self.approved_at)
        if not self.approved_by.strip() or not self.approval_id.strip() or not self.scope.strip():
            raise ValueError("EXPLICIT_HUMAN_APPROVAL_REQUIRED")
        if self.operation != "APPROVE_STRUCTURED" and not self.installation_sha256:
            raise ValueError("APPROVED_INSTALLATION_RECEIPT_REQUIRED")
        return self


class Installation(Contract):
    receipt_id: str = Field(min_length=1)
    environment: Literal["ISOLATED_REHEARSAL", "PRODUCTION"]
    installed_at: str
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    scope: str
    bundle_sha256: str = Field(pattern=HASH)
    consumer_bundle_hashes: dict[str, str]

    @model_validator(mode="after")
    def consumers(self):
        clock(self.installed_at)
        if (set(self.consumer_bundle_hashes) != CONSUMERS
                or any(v != self.bundle_sha256 for v in self.consumer_bundle_hashes.values())):
            raise ValueError("ALL_CONSUMERS_MUST_BIND_SAME_BUNDLE")
        return self


def store_identity(store):
    """Opaque target binding; never disclose a private filesystem path."""
    if hasattr(store, "publisher_identity"):
        return store.publisher_identity()
    return digest({"policy_store": str(store.path)})


def _bound(auth, store, bundle, operation, now):
    if (auth.operation != operation or auth.scope != bundle["scope"]
            or auth.store_identity != store_identity(store)
            or auth.bundle_id != bundle["bundle_id"] or auth.bundle_sha256 != digest(bundle)
            or auth.structured_sha256 != bundle["structured_sha256"]):
        raise PolicyError("AUTHORIZATION_TARGET_MISMATCH")
    if not clock(bundle["created_at"]) <= clock(auth.approved_at) <= clock(now):
        raise PolicyError("AUTHORIZATION_CLOCK_INVALID")


def verify_formal_release(store, db, bundle, release):
    """Read-side proof check; legacy synthetic receipts keep their original path."""
    if release.get("synthetic") is True:
        source = db.execute("SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?",
                            (bundle["scope"], bundle["bundle_id"])).fetchone()
        if not all(bytes(source[k]).startswith(b"# SYNTHETIC ONLY") for k in ("philosophy", "policy")):
            raise PolicyError("REAL_SOURCE_CANNOT_USE_SYNTHETIC_RELEASE")
        return
    auth = Authorization.model_validate(release["authorization"])
    install = Installation.model_validate(release["installation"])
    _bound(auth, store, bundle, "ROLLBACK" if release["action"] == "ROLLBACK" else "ACTIVATE", release["recorded_at"])
    if (auth.expected_sequence != release["predecessor_sequence"]
            or install.scope != bundle["scope"] or install.bundle_sha256 != digest(bundle)
            or install.environment != auth.environment
            or release.get("environment") != auth.environment or release.get("synthetic") is not False
            or auth.installation_sha256 != digest(install)
            or release.get("request_sha256") != digest(dict(authorization=auth.model_dump(), installation=install.model_dump()))
            or clock(install.installed_at) > clock(release["effective_at"])
            or release["deployed_at"] != install.installed_at
            or release["effective_at"] != release["recorded_at"]
            or release["release_approval_ref"] != auth.approval_id):
        raise PolicyError("FORMAL_RELEASE_PROOF_INVALID")
    predecessors = db.execute("SELECT * FROM policy_releases WHERE scope=? AND sequence<=? ORDER BY sequence",
                              (bundle["scope"], auth.expected_sequence)).fetchall()
    if ([r["sequence"] for r in predecessors] != list(range(1, auth.expected_sequence+1))
            or auth.expected_active_bundle_sha256 != (store._read(predecessors[-1])["bundle_sha256"] if predecessors else None)):
        raise PolicyError("FORMAL_RELEASE_PREDECESSOR_MISMATCH")
    if any(store._read(r).get("environment") != auth.environment or store._read(r).get("synthetic") is not False for r in predecessors):
        raise PolicyError("MIXED_PUBLISH_ENVIRONMENTS_FORBIDDEN")
    if release["action"] == "ROLLBACK" and not any(
            store._read(r).get("synthetic") is False and r["bundle_id"] == bundle["bundle_id"]
            for r in predecessors[:-1]):
        raise PolicyError("ROLLBACK_REQUIRES_PREVIOUSLY_RELEASED_VERSION")
    approvals = [store._read(r) for r in db.execute(
        "SELECT * FROM policy_events WHERE scope=? AND bundle_id=? AND state='APPROVED'",
        (bundle["scope"], bundle["bundle_id"]))]
    valid = False
    for item in approvals:
        if "authorization" not in item:
            continue
        structured = Authorization.model_validate(item["authorization"])
        _bound(structured, store, bundle, "APPROVE_STRUCTURED", item["recorded_at"])
        if (structured.environment == auth.environment
                and clock(item["recorded_at"]) <= clock(auth.approved_at)
                and item["approval_ref"] == structured.approval_id):
            valid = True
    if not valid:
        raise PolicyError("FORMAL_STRUCTURED_APPROVAL_REQUIRED")


class PolicyPublisher:
    def __init__(self, store, *, environment, now=utc_now):
        if environment not in {"ISOLATED_REHEARSAL", "PRODUCTION"}:
            raise PolicyError("EXPLICIT_PUBLISH_ENVIRONMENT_REQUIRED")
        self.store, self.environment, self.now = store, environment, now

    def approve(self, *, authorization, request_id):
        auth = Authorization.model_validate(authorization)
        self.store._identity(auth.scope, request_id)
        at = self.now(); clock(at)
        if auth.environment != self.environment:
            raise PolicyError("AUTHORIZATION_ENVIRONMENT_MISMATCH")
        with self.store.connection(write=True) as db:
            bundle = self.store._bundle(db, auth.scope, auth.bundle_id)
            _bound(auth, self.store, bundle, "APPROVE_STRUCTURED", at)
            authorizations = [self.store._read(r).get("authorization") for r in db.execute(
                "SELECT * FROM policy_events WHERE scope=?", (auth.scope,))]
            if any(a and a.get("environment") != self.environment for a in authorizations):
                raise PolicyError("MIXED_PUBLISH_ENVIRONMENTS_FORBIDDEN")
            if self.environment == "PRODUCTION":
                raw = db.execute("SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?", (auth.scope,auth.bundle_id)).fetchone()
                if any(bytes(raw[k]).startswith(b"# SYNTHETIC ONLY") for k in ("philosophy","policy")):
                    raise PolicyError("SYNTHETIC_SOURCE_NOT_PRODUCTION_POLICY")
            prior = db.execute("SELECT * FROM policy_events WHERE scope=? AND request_id=?", (auth.scope, request_id)).fetchone()
            if prior:
                value = self.store._read(prior)
                if value.get("authorization") != auth.model_dump() or value["state"] != "APPROVED":
                    raise PolicyError("REQUEST_ID_CONFLICT")
                return value
            current = self.store._read(db.execute(
                "SELECT * FROM policy_events WHERE scope=? AND bundle_id=? ORDER BY sequence DESC LIMIT 1",
                (auth.scope, auth.bundle_id)).fetchone())
            if current["state"] != "VALIDATED" or clock(current["recorded_at"]) > clock(auth.approved_at):
                raise PolicyError("VALIDATED_BEFORE_STRUCTURED_APPROVAL_REQUIRED")
            return self.store._append(db, auth.scope, auth.bundle_id, "APPROVED", request_id, at,
                                      auth.approval_id, authorization=auth.model_dump())

    def release(self, *, authorization, installation, request_id):
        auth = Authorization.model_validate(authorization)
        install = Installation.model_validate(installation)
        self.store._identity(auth.scope, request_id)
        if auth.environment != self.environment or auth.operation not in {"ACTIVATE", "ROLLBACK"}:
            raise PolicyError("EXPLICIT_ACTIVATION_OR_ROLLBACK_AUTHORIZATION_REQUIRED")
        request_sha = digest(dict(authorization=auth.model_dump(), installation=install.model_dump()))
        at = self.now(); clock(at)
        with self.store.connection(write=True) as db:
            bundle = self.store._bundle(db, auth.scope, auth.bundle_id)
            _bound(auth, self.store, bundle, auth.operation, at)
            releases = [self.store._read(r) for r in db.execute(
                "SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence", (auth.scope,))]
            prior = next((r for r in releases if r.get("request_id") == request_id), None)
            if prior:
                if prior.get("request_sha256") != request_sha:
                    raise PolicyError("REQUEST_ID_CONFLICT")
                verify_formal_release(self.store, db, bundle, prior)
                return prior
            if len(releases) != auth.expected_sequence:
                raise PolicyError("RELEASE_CAS_CONFLICT")
            previous = releases[-1] if releases else None
            if (auth.expected_active_bundle_sha256 != (previous["bundle_sha256"] if previous else None)
                    or (previous and clock(at) < clock(previous["recorded_at"]))):
                raise PolicyError("ACTIVE_VERSION_CAS_CONFLICT")
            if auth.operation == "ROLLBACK" and not any(
                    r["bundle_id"] == auth.bundle_id and r["bundle_sha256"] == digest(bundle)
                    and r.get("synthetic") is False for r in releases[:-1]):
                raise PolicyError("ROLLBACK_REQUIRES_PREVIOUSLY_RELEASED_VERSION")
            payload = dict(scope=auth.scope, bundle_id=auth.bundle_id, bundle_sha256=digest(bundle),
                request_id=request_id, request_sha256=request_sha,
                action="ROLLBACK" if auth.operation == "ROLLBACK" else "RELEASE", recorded_at=at, effective_at=at,
                deployed_at=install.installed_at, release_approval_ref=auth.approval_id, synthetic=False,
                environment=self.environment, predecessor_sequence=auth.expected_sequence,
                authorization=auth.model_dump(), installation=install.model_dump())
            verify_formal_release(self.store, db, bundle, payload)
            db.execute("INSERT INTO policy_releases VALUES(?,?,?,?,?)",
                (auth.scope, auth.expected_sequence+1, auth.bundle_id, canonical(payload), digest(payload)))
            return payload

    def lifecycle(self, scope):
        """State projection only. Never rewrite an old Bundle or approval."""
        events = self.store.history(scope)
        releases = self.store.release_history(scope)
        states = {e["bundle_id"]: e["state"] for e in events}
        for release in releases:
            for key in states:
                if states[key] == "ACTIVE":
                    states[key] = "ROLLED_BACK" if release["action"] == "ROLLBACK" else "SUPERSEDED"
            states[release["bundle_id"]] = "ACTIVE"
        return {"bundles": states, "latest_action": releases[-1]["action"] if releases else None,
                "rolled_back_releases": [r["request_id"] for r in releases if r["action"] == "ROLLBACK"],
                "execution_authorized": False}
