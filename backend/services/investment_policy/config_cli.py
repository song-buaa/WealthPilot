"""Trusted local Policy Configuration CLI. Never an HTTP or MCP writer."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import stat
import sqlite3
import subprocess

from .colocated import configured_store
from .configuration import from_snapshot
from .contracts import canonical, digest, parse_snapshot
from .fast_release import _candidate_content, _verify_receipts, fast_store_identity, preview, preview_content, publish
from .formal_publisher import CONSUMERS, PolicyPublisher
from .parser import PolicyError
from .publish_cli import private_artifact
from .service import PolicyService

PRODUCTION_TRUST = Path("/private/etc/wealthpilot/policy-fast-release.json")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique)


def production_trust(store, scope):
    from .colocated import CoLocatedPolicyStore

    if not isinstance(store, CoLocatedPolicyStore):
        raise PolicyError("PRODUCTION_SHARED_STORE_REQUIRED")
    for path in (PRODUCTION_TRUST, *PRODUCTION_TRUST.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PolicyError("PROTECTED_PRODUCTION_TRUST_REQUIRED")
    if not PRODUCTION_TRUST.is_file():
        raise PolicyError("PROTECTED_PRODUCTION_TRUST_REQUIRED")
    trust = _json(PRODUCTION_TRUST.read_bytes())
    _check_trust(trust, store, scope, "PRODUCTION")
    return trust


def _check_trust(trust, store, scope, environment):
    if (trust["scope"] != scope or trust["environment"] != environment
            or trust["store_identity"] != fast_store_identity(store)):
        raise PolicyError("TRUST_CONTEXT_MISMATCH")


def _trust(args, store):
    if args.environment == "PRODUCTION":
        if args.rehearsal_trust:
            raise PolicyError("PRODUCTION_PROTECTED_TRUST_REQUIRED")
        return production_trust(store, args.scope)
    if not args.rehearsal_trust:
        raise PolicyError("ISOLATED_TRUST_REQUIRED")
    trust = _json(private_artifact(args.rehearsal_trust))
    _check_trust(trust, store, args.scope, args.environment)
    return trust


def _write(path, payload):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PolicyError("PRIVATE_OUTPUT_REQUIRED")
    par = path.parent.stat()
    if par.st_uid != os.getuid() or stat.S_IMODE(par.st_mode) != 0o700:
        raise PolicyError("PRIVATE_OUTPUT_REQUIRED")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def _probes(trust, phase, candidate, bundle=None):
    commands = trust["probes"][phase]
    if (set(commands) != CONSUMERS or any(not isinstance(cmd, list) or not cmd
            or not isinstance(cmd[0], str) or not Path(cmd[0]).is_absolute()
            or any(not isinstance(arg, str) for arg in cmd) for cmd in commands.values())
            or len({tuple(cmd) for cmd in commands.values()}) != len(CONSUMERS)):
        raise PolicyError("DISTINCT_INSTALLED_CONSUMER_PROBES_REQUIRED")
    request = dict(phase=phase, scope=candidate["scope"])
    if bundle is not None:
        request["candidate_bundle"] = bundle
    results = {}
    for name, argv in sorted(commands.items()):
        try:
            proc = subprocess.run(argv, input=canonical(request).encode(), capture_output=True,
                                  timeout=15, check=True)
            results[name] = _json(proc.stdout)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise PolicyError("INSTALLED_CONSUMER_PROBE_UNAVAILABLE") from exc
    return results


def _backup(store, directory, candidate):
    from pocs.poc03_mac_mini import snapshot

    path = Path(directory).absolute() / ("pre-policy-" + digest(candidate)[:24] + ".sqlite")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise PolicyError("PRIVATE_BACKUP_REQUIRED")
    if fast_store_identity(store) != candidate["store_identity"]:
        raise PolicyError("BACKUP_SOURCE_IDENTITY_CHANGED")
    if not path.exists():
        try:
            snapshot.backup(store.path, path)
        except (FileExistsError, OSError, ValueError):
            if not path.is_file() or not Path(str(path) + ".manifest.json").is_file():
                raise
    manifest = _json(private_artifact(str(path) + ".manifest.json"))
    snapshot.verify(path, manifest)
    with closing(snapshot.read_connection(path)) as db:
        db.row_factory = sqlite3.Row
        scope = candidate["scope"]
        head = db.execute("SELECT coalesce(max(version),0) FROM policy_bundles WHERE scope=?", (scope,)).fetchone()[0]
        release_row = db.execute("SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence DESC LIMIT 1", (scope,)).fetchone()
        release = store._read(release_row)
        active = candidate["before"]["active"]
        bundle = store._read(db.execute("SELECT * FROM policy_bundles WHERE scope=? AND bundle_id=?",
                                       (scope, active["bundle_id"])).fetchone())
        if (head != candidate["before"]["head"] or release_row["sequence"] != candidate["before"]["release_sequence"]
                or release["bundle_id"] != active["bundle_id"] or release["bundle_sha256"] != active["bundle_sha256"]
                or digest(bundle) != active["bundle_sha256"]
                or fast_store_identity(store) != candidate["store_identity"]):
            raise PolicyError("BACKUP_PREDECESSOR_MISMATCH")
    return {"status": "VERIFIED_CONSISTENT_BACKUP", "sha256": manifest["sha256"]}


def _operator_approval(trust, candidate, reference):
    """The protected operator checks the real PO record before signing."""
    argv = trust.get("approval_command")
    if (not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv)
            or not Path(argv[0]).is_absolute()):
        raise PolicyError("TRUSTED_APPROVAL_OPERATOR_NOT_CONFIGURED")
    request = {key:candidate[key] for key in ("contract","operation","environment","scope","store_identity")}
    request |= {"candidate_sha256":digest(candidate), "approval_id":reference}
    try:
        process = subprocess.run(argv, input=canonical(request).encode(), capture_output=True,
                                 check=True, timeout=30)
        response = _json(process.stdout)
        approval, signature = response["approval"], response["signature"]
        if approval["approval_id"] != reference:
            raise ValueError("approval reference")
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyError("TRUSTED_APPROVAL_OPERATOR_REJECTED") from exc
    return approval, signature



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--shared-contract")
    parser.add_argument("--environment", required=True, choices=["PRODUCTION", "ISOLATED_REHEARSAL"])
    parser.add_argument("--scope", required=True)
    parser.add_argument("--rehearsal-trust")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--config-out")
    export = commands.add_parser("export-content")
    export.add_argument("--out", required=True)
    preview_cmd = commands.add_parser("preview")
    selection = preview_cmd.add_mutually_exclusive_group(required=True)
    selection.add_argument("--config")
    selection.add_argument("--philosophy")
    preview_cmd.add_argument("--policy")
    selection.add_argument("--rollback-to")
    preview_cmd.add_argument("--out", required=True)
    for name in ("publish", "rollback"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--candidate", required=True)
        auth = cmd.add_mutually_exclusive_group(required=True)
        auth.add_argument("--approval")
        auth.add_argument("--confirmed-ref")
        cmd.add_argument("--signature")
    args = parser.parse_args(argv)

    store = configured_store(args.db, shared_contract=args.shared_contract)
    publisher = PolicyPublisher(store, environment=args.environment)
    if args.command == "status":
        active = PolicyService(store).get_active_policy_context(args.scope)
        if args.config_out:
            if active["status"] != "ACTIVE":
                raise PolicyError("ACTIVE_POLICY_REQUIRED")
            config = from_snapshot(parse_snapshot(active["snapshot"]))
            _write(args.config_out, canonical(config).encode())
        releases = store.release_history(args.scope)
        result = {"status": active["status"], "policy_reference": active["policy_reference"],
                  "lifecycle": publisher.lifecycle(args.scope), "release_sequence": len(releases)}
        proof = releases[-1].get("policy_config_proof") if releases else None
        if proof and active["status"] == "ACTIVE":
            try:
                trust = _trust(args, store)
                receipts = _probes(trust, "ACTIVE", proof["candidate"])
                _verify_receipts(receipts, proof["candidate"], "ACTIVE", active["policy_reference"])
                if PolicyService(store).get_active_policy_context(args.scope).get("policy_reference") != active["policy_reference"]:
                    raise PolicyError("ACTIVE_CHANGED_DURING_READBACK")
                result["readback"] = "ACTIVE_READBACK_PASS"
                result["consumers"] = receipts
            except (OSError, ValueError, KeyError):
                result["readback"] = "ACTIVATED_READBACK_PENDING"
    elif args.command == "export-content":
        from .external_content import content_only
        active = PolicyService(store).get_active_policy_context(args.scope)
        if active["status"] != "ACTIVE":
            raise PolicyError("ACTIVE_POLICY_REQUIRED")
        with store.connection() as db:
            row = db.execute("SELECT philosophy, policy FROM policy_bundles WHERE scope=? AND bundle_id=?",
                (args.scope, active["policy_reference"]["bundle_id"])).fetchone()
        for role in ("philosophy", "policy"):
            _write(f"{args.out}.{role}.md", content_only(bytes(row[role])))
        result = {"status":"CONTENT_EXPORTED", "from":active["policy_reference"]}
    else:
        trust = _trust(args, store)
        if args.command == "preview":
            if bool(args.philosophy) != bool(args.policy):
                raise PolicyError("TWO_FROZEN_CONTENT_SOURCES_REQUIRED")
            if args.philosophy:
                sources = {"philosophy.md":private_artifact(args.philosophy), "policy.md":private_artifact(args.policy)}
                candidate = preview_content(publisher, args.scope, tuple(sources.values()), code_sha=trust["code_sha"])
            else:
                config = _json(private_artifact(args.config)) if args.config else None
                candidate, sources = preview(publisher, args.scope, config, trust["listings"],
                                             code_sha=trust["code_sha"], rollback_to=args.rollback_to)
            if args.philosophy:
                with store.connection() as db:
                    bundle = _candidate_content(store, db, candidate, tuple(sources.values()))
                _verify_receipts(_probes(trust, "CANDIDATE", candidate, bundle), candidate, "CANDIDATE")
            output = Path(args.out)
            for name, data in (sources or {}).items():
                _write(output.with_name(f"{output.name}.{name}"), data)
            _write(output, canonical(candidate).encode())
            result = {"status": "PREVIEW_ONLY", "candidate_sha256": digest(candidate),
                      "target": candidate["target"], "operation": candidate["operation"],
                      "source_hashes":candidate.get("source_hashes"), "changes":candidate.get("changes"),
                      "before": candidate["before"], "code_sha": candidate["code_sha"]}
        else:
            candidate = _json(private_artifact(args.candidate))
            allowed = {"ROLLBACK"} if args.command == "rollback" else {"PUBLISH_L1", "PUBLISH_L2"}
            if candidate["operation"] not in allowed:
                raise PolicyError("EXPLICIT_OPERATION_MISMATCH")
            if args.confirmed_ref:
                if args.signature:
                    raise PolicyError("AMBIGUOUS_APPROVAL_INPUT")
                approval, signature = _operator_approval(trust, candidate, args.confirmed_ref)
            else:
                if not args.signature:
                    raise PolicyError("SIGNED_USER_APPROVAL_REQUIRED")
                approval = _json(private_artifact(args.approval))
                signature = private_artifact(args.signature).hex()
            sources = None
            if candidate["operation"] == "PUBLISH_L2":
                sources = tuple(private_artifact(f"{args.candidate}.{role}.md") for role in ("philosophy","policy"))
            receipt = publish(publisher, candidate, approval, signature, trust,
                check_candidates=lambda c,b: _probes(trust, "CANDIDATE", c, b),
                read_consumers=lambda c: _probes(trust, "ACTIVE", c),
                backup=lambda c: _backup(store, trust["backup_dir"], c), sources=sources)
            release = receipt["release"]
            result = {"status":receipt["status"], "bundle_id":release["bundle_id"],
                      "recorded_at":release["recorded_at"], "approval_ref":release["release_approval_ref"],
                      "approved_at":release["policy_config_proof"]["approval"]["approved_at"],
                      "release_sequence":release["predecessor_sequence"]+1, "target":candidate["target"],
                      "source_hashes":candidate.get("source_hashes"),
                      "candidate_sha256":release["request_sha256"],
                      "reason":receipt.get("reason"), "consumers":receipt.get("consumers", {})}
    print(canonical(result | {"execution_authorized": False}))
    return 2 if result.get("status") == "ACTIVATED_READBACK_PENDING" else 0


if __name__ == "__main__":
    raise SystemExit(main())
