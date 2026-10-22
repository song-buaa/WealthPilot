"""Private, versioned stage-target configuration; independent of trading rules.

Like app.discipline.config, a local file is the source of truth. Analysis only
reads it. The sole mutation entrypoint is the local operator CLI, never MCP or
an analysis request. Filesystem ownership is the trust boundary, not a client
boolean/digest. No default file is loaded without explicit server configuration.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from backend.services.allocation_scenarios import StageTargets

SCHEMA = "wealthpilot.stage-targets.v1"
ENV_PATH = "WEALTHPILOT_STAGE_TARGETS_PATH"


def _at(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("TARGET_TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


def _digest(content):
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _content(raw):
    """Reuse the existing target schema; discard request confirmation claims."""
    if not isinstance(raw, dict):
        raise ValueError("TARGET_CONTENT_INVALID")
    value = deepcopy(raw)
    start = value.pop("effective_from", None) or value.get("confirmed_at")
    value.pop("digest", None)
    value["confirmed_by_user"] = True  # legacy schema only, removed below
    value["confirmed_at"] = start
    target = StageTargets.model_validate(value).model_dump(mode="json")
    for issuer in target["issuers"]:
        phases = [issuer["target_weight"]] + issuer["phases"]
        if any(a <= b for a, b in zip(phases, phases[1:])):
            raise ValueError("TARGET_EXIT_PHASES_MUST_DECREASE")
    if (target["fixed_income_target"] is not None and target["fixed_income_ceiling"] is not None
            and target["fixed_income_target"] > target["fixed_income_ceiling"]):
        raise ValueError("TARGET_FIXED_INCOME_RANGE_INVALID")
    target["effective_from"] = target.pop("confirmed_at")
    target.pop("confirmed_by_user")
    return target


class StageTargetStore:
    """Small local configuration store, with immutable candidate contents."""
    def __init__(self, path):
        self.path = Path(path).expanduser().absolute()

    def _read(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return {"schema_version": SCHEMA, "records": []}
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
            raise ValueError("TARGET_STORE_OWNER_OR_MODE_INVALID")
        if info.st_size > 2_000_000:
            raise ValueError("TARGET_STORE_TOO_LARGE")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or not isinstance(data.get("records"), list):
            raise ValueError("TARGET_STORE_SCHEMA_INVALID")
        keys = set()
        for record in data["records"]:
            if not isinstance(record, dict):
                raise ValueError("TARGET_RECORD_INVALID")
            pid = record.get("portfolio_id")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("TARGET_PORTFOLIO_REQUIRED")
            content = _content(record["content"])
            key = (pid, content["version"])
            if key in keys or _digest(content) != record.get("digest"):
                raise ValueError("TARGET_STORE_CONTENT_OR_VERSION_INVALID")
            keys.add(key)
            if record.get("status") not in {"candidate", "active", "superseded", "inactive"}:
                raise ValueError("TARGET_STORE_STATUS_INVALID")
            confirmation = record.get("confirmation")
            if record["status"] != "candidate" and (not isinstance(confirmation, dict)
                    or confirmation.get("source") != "local_operator_cli"
                    or not confirmation.get("operator") or not confirmation.get("reference")
                    or confirmation.get("digest") != record["digest"]):
                raise ValueError("TARGET_STORE_CONFIRMATION_INVALID")
            if confirmation:
                _at(confirmation["confirmed_at"])
        return data

    def _mutate(self, change):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.close(fd)
            data = self._read()
            result = change(data)
            fd, temp = tempfile.mkstemp(prefix=".stage-targets-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            return deepcopy(result)
        finally:
            lock.unlink()

    def create_candidate(self, raw, *, portfolio_id, source, at=None):
        if not isinstance(portfolio_id, int) or isinstance(portfolio_id, bool) or portfolio_id <= 0:
            raise ValueError("TARGET_PORTFOLIO_REQUIRED")
        if not source or len(source) > 500:
            raise ValueError("TARGET_CANDIDATE_SOURCE_REQUIRED")
        content = _content(raw)
        record = dict(portfolio_id=portfolio_id, content=content, digest=_digest(content),
                      status="candidate", created_at=_at(at).isoformat(),
                      content_summary=dict(stage=content["stage"], issuer_ids=[i["issuer_id"] for i in content["issuers"]]),
                      candidate_source=source, confirmation=None)

        def change(data):
            if any(r["portfolio_id"] == portfolio_id and r["content"]["version"] == content["version"]
                   for r in data["records"]):
                raise ValueError("TARGET_VERSION_IMMUTABLE")
            data["records"].append(record)
            return record
        return self._mutate(change)

    def activate(self, version, *, portfolio_id, expected_digest, operator, reference,
                 explicit_confirmation=False, at=None):
        if explicit_confirmation is not True or not operator or not reference:
            raise ValueError("EXPLICIT_LOCAL_OPERATOR_CONFIRMATION_REQUIRED")
        now = _at(at)

        def change(data):
            record = next((r for r in data["records"] if r["portfolio_id"] == portfolio_id
                           and r["content"]["version"] == version), None)
            if not record or record["status"] != "candidate" or record["digest"] != expected_digest:
                raise ValueError("TARGET_CANDIDATE_OR_DIGEST_MISMATCH")
            if now >= _at(record["content"]["valid_until"]):
                raise ValueError("TARGET_CANDIDATE_EXPIRED")
            active = [r for r in data["records"] if r["portfolio_id"] == portfolio_id and r["status"] == "active"]
            if len(active) > 1 or (active and record["content"].get("supersedes") != active[0]["content"]["version"]):
                raise ValueError("TARGET_VERSION_LINEAGE_REQUIRED")
            for previous in active:
                previous["status"] = "superseded"
            record["status"] = "active"
            record["confirmation"] = dict(source="local_operator_cli", operator=operator,
                reference=reference, confirmed_at=now.isoformat(), digest=expected_digest)
            return record
        return self._mutate(change)

    def deactivate(self, version, *, portfolio_id, reason):
        if not reason:
            raise ValueError("TARGET_DEACTIVATION_REASON_REQUIRED")

        def change(data):
            record = next((r for r in data["records"] if r["portfolio_id"] == portfolio_id
                           and r["content"]["version"] == version and r["status"] == "active"), None)
            if not record:
                raise ValueError("ACTIVE_TARGET_NOT_FOUND")
            record["status"] = "inactive"
            record["deactivation_reason"] = reason
            return record
        return self._mutate(change)

    def resolve(self, portfolio_id, *, at=None):
        now = _at(at)
        active = [r for r in self._read()["records"]
                  if r["portfolio_id"] == portfolio_id and r["status"] == "active"]
        if len(active) > 1:
            raise ValueError("MULTIPLE_ACTIVE_TARGETS")
        if not active:
            return None
        record = active[0]
        content, confirmation = record["content"], record["confirmation"]
        if not (_at(content["effective_from"]) <= now < _at(content["valid_until"])
                and _at(confirmation["confirmed_at"]) <= now):
            return None
        result = deepcopy(content)
        result.update(confirmed_by_user=True, confirmed_at=confirmation["confirmed_at"],
                      digest=record["digest"], basis="server_stage_target_store",
                      confirmation_status="confirmed", status="active", portfolio_id=portfolio_id,
                      confirmation_source=confirmation["source"],
                      confirmation_reference=confirmation["reference"],
                      content_summary=deepcopy(record.get("content_summary", {})))
        return result

    def candidates(self, portfolio_id):
        return deepcopy([r for r in self._read()["records"]
                         if r["portfolio_id"] == portfolio_id and r["status"] == "candidate"])


def resolve_trusted_targets(portfolio_id, *, at=None):
    """Read-only server boundary. Invalid/missing configuration never grants L3."""
    path = os.environ.get(ENV_PATH)
    if not path or not isinstance(portfolio_id, int) or isinstance(portfolio_id, bool):
        return None
    return StageTargetStore(path).resolve(portfolio_id, at=at)


def read_target_context(portfolio_id, *, at=None):
    """Resolve authority and optional candidate; corruption is an explicit blocker."""
    result = dict(confirmed=None, candidate=None, issues=[])
    path = os.environ.get(ENV_PATH)
    if not path or not isinstance(portfolio_id, int) or isinstance(portfolio_id, bool):
        return result
    try:
        store = StageTargetStore(path)
        result["confirmed"] = store.resolve(portfolio_id, at=at)
        candidates = store.candidates(portfolio_id)
        now = _at(at)
        candidates = [r for r in candidates if _at(r["content"]["effective_from"]) <= now
                      < _at(r["content"]["valid_until"])]
        if candidates:
            record = max(candidates, key=lambda r: _at(r["created_at"]))
            result["candidate"] = {**record["content"], "digest": record["digest"],
                "basis": "server_candidate_unconfirmed", "confirmation_status": "unverified",
                "candidate_source": record["candidate_source"], "portfolio_id": portfolio_id}
    except (OSError, ValueError, KeyError, TypeError):
        result["confirmed"] = None
        result["candidate"] = None
        result["issues"].append("STAGE_TARGET_STORE_INVALID")
    return result
