"""Read-only unified policy context; intentionally not wired into current callers."""
from __future__ import annotations

import sqlite3

from .contracts import PolicyReference
from .parser import PolicyError
from .store import PolicyStore, clock, utc_now


class PolicyService:
    def __init__(self, store: PolicyStore | None = None):
        self.store = store

    def get_active_policy_context(self, scope, *, as_of=None, expected_bundle_sha=None):
        """No implicit init, no cache, defaults, reindex, broker or provider access."""
        try:
            cutoff = clock(as_of or utc_now())
            if not self.store:
                return {"status": "NOT_ACTIVE", "reason": "NO_CONFIGURED_POLICY_STORE", "policy_reference": None}
            with self.store.connection() as db:
                rows = db.execute("SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence DESC", (scope,)).fetchall()
                eligible = []
                for row in rows:
                    release = self.store._read(row)
                    if release.get("scope") != scope or release.get("bundle_id") != row["bundle_id"]:
                        raise PolicyError("RELEASE_SCOPE_MISMATCH")
                    for key in ("recorded_at", "effective_at", "deployed_at"):
                        clock(release[key])
                    if clock(release["effective_at"]) <= cutoff and clock(release["recorded_at"]) <= cutoff:
                        eligible.append(release)
                if not eligible:
                    return {"status": "NOT_ACTIVE", "reason": "NO_EFFECTIVE_APPROVED_RELEASE", "policy_reference": None}
                release = eligible[0]
                value = self.store._bundle(db, scope, release["bundle_id"])
                approvals = [self.store._read(r) for r in db.execute("SELECT * FROM policy_events WHERE scope=? AND bundle_id=?", (scope, value["bundle_id"]))]
                if not any(e["state"] == "APPROVED" and e["approval_ref"] and clock(e["recorded_at"]) <= clock(release["effective_at"]) for e in approvals):
                    raise PolicyError("STRUCTURED_RELEASE_APPROVAL_MISSING")
                if (not release.get("release_approval_ref") or release.get("action") not in {"RELEASE", "ROLLBACK"}
                        or clock(release["deployed_at"]) > clock(release["effective_at"])
                        or clock(value["created_at"]) > clock(release["effective_at"])):
                    raise PolicyError("RELEASE_EVIDENCE_INCOMPLETE")
                from .contracts import digest
                bundle_sha = digest(value)
                if release.get("bundle_sha256") != bundle_sha or (expected_bundle_sha and expected_bundle_sha != bundle_sha):
                    raise PolicyError("POLICY_VERSION_MISMATCH_OR_STALE_REFERENCE")
                ref = PolicyReference(bundle_id=value["bundle_id"], bundle_version=value["bundle_version"],
                    bundle_sha256=bundle_sha, structured_sha256=value["structured_sha256"], scope=scope)
                return {"status": "ACTIVE", "policy_reference": ref.model_dump(), "source_files": value["source_files"],
                        "source_hashes": value["source_hashes"], "snapshot": value["snapshot"],
                        "effective_at": release["effective_at"], "release": release,
                        "data_quality": "SOURCE_HASHES_AND_DETERMINISTIC_DERIVATIVE_VERIFIED",
                        "limitations": ["POLICY_IS_NOT_TRADE_AUTHORIZATION", "PORTFOLIO_DATA_NOT_EVALUATED", "P1_NOT_WIRED_TO_CURRENT_CALLERS"]}
        except (PolicyError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            return {"status": "UNAVAILABLE", "reason": str(exc), "policy_reference": None}
