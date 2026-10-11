"""launchd child wrapper: fixed entry points, bounded metadata-only lifecycle log."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess

from common import check_revision, digest, host_guard, private_json, require, safe_cli

PROOF_FIELDS = ("access_scope_confirmed", "synthetic_web_pass", "anonymous_denied",
                "no_public_listener", "independent_production_credential", "minimal_permissions_verified",
                "migration_verified", "native_business_smoke_pass")


def production_key_digest(root):
    key = Path(root) / "credentials/runtime-key"
    require(key.is_file() and not key.is_symlink() and key.stat().st_mode & 0o077 == 0,
            "PRIVATE_PRODUCTION_KEY_REQUIRED")
    require(bool(key.read_bytes().strip()), "EMPTY_KEY_REFUSED")
    return digest(key)


def verify_real_access(root, proof):
    # An operator exception never changes the observed least-privilege fact.
    require(all(proof.get(k) is True for k in PROOF_FIELDS if k != "minimal_permissions_verified"),
            "REAL_ACCESS_NOT_VERIFIED")
    if proof.get("minimal_permissions_verified") is True:
        return "minimal_permissions_verified"
    exception = proof.get("personal_runtime_key_exception", {})
    require(proof.get("minimal_permissions_verified") is False and isinstance(exception, dict),
            "REAL_ACCESS_NOT_VERIFIED")
    expected = {"scope": "personal-production-runtime-key", "ordinary_key": True,
                "single_member_organization": True, "personal_workspace": True,
                "broad_permissions_risk_accepted_by_owner": True}
    require(all(type(exception.get(k)) is type(v) and exception[k] == v for k, v in expected.items())
            and isinstance(exception.get("accepted_at"), str) and bool(exception["accepted_at"]),
            "REAL_ACCESS_NOT_VERIFIED")
    require(exception.get("credential_sha256") == production_key_digest(root),
            "RUNTIME_KEY_EXCEPTION_CREDENTIAL_MISMATCH")
    return "broad_permissions_owner_exception"


def child_command(root, role):
    root = Path(root).resolve()
    config = private_json(root / "config/runtime.json")
    host_guard(config["expected_hostname"])
    revision = config["revision"]
    release = root / "releases" / revision
    code = release / "code"
    require((root / "current").resolve() == release, "CURRENT_RELEASE_MISMATCH")
    check_revision(code, revision)
    if role == "mcp":
        args = [release / "envs/mcp/bin/python", code / "pocs/poc02_wealth_mcp/server.py",
                "--business-python", release / "envs/business/bin/python",
                "--port", "8788", "--events", root / "run/completions.jsonl"]
        if config["mode"] == "real":
            proof = private_json(root / "config/access-evidence.json")
            config["credential_permission_status"] = verify_real_access(root, proof)
            wealth = private_json(root / "config/wealth.json")
            require(Path(wealth["database_path"]).resolve() == (root / "data/wealthpilot.db").resolve(), "PRODUCTION_DATABASE_PATH_MISMATCH")
            reference = private_json(root / "data/migration-reference.json")
            require(digest(wealth["database_path"]) == reference["database_sha256"], "MIGRATION_DATABASE_DIGEST_MISMATCH")
            require(bool(wealth.get("tiger_cash")) == reference["cash_reference"]["configured"],
                    "CASH_CONFIGURATION_DIFFERS_FROM_MIGRATION")
            args += ["--real-config", root / "config/wealth.json",
                     "--access-evidence", root / "config/access-evidence.json"]
        else:
            require(config["mode"] == "synthetic", "INVALID_MODE")
    else:
        require(role == "tunnel", "INVALID_ROLE")
        production_key_digest(root)
        args = [release / "tunnel/tunnel-client", "run", "--config", root / "config/tunnel.json",
                "--log.file", "", "--log.level", "warn", "--admin-ui.log-buffer-events", "1"]
    return [str(x) for x in args], config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--role", choices=("mcp", "tunnel"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    log = logging.getLogger("poc03-lifecycle")
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(Path(args.root) / "logs" / (args.role + ".lifecycle.jsonl"),
                                 maxBytes=1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    log.addHandler(handler)
    try:
        command, config = child_command(args.root, args.role)
        # No shell, dotenv, inherited credentials/proxies or raw child logs.
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": config["user_home"],
                       "LANG": "en_US.UTF-8", "PYTHON_DOTENV_DISABLED": "1",
                       "PYTHONDONTWRITEBYTECODE": "1", "IBKR_READ_ONLY_MODE": "true",
                       "ENABLE_IBKR_LIVE_TRADING": "false", "BROKER_MODE": "mock"}
        child = subprocess.Popen(command, cwd=Path(args.root) / "current/code", env=environment,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log.info(json.dumps({"event": "child_started", "role": args.role, "pid": child.pid,
                             "credential_permission_status": config.get("credential_permission_status", "not_applicable")}))
        def stop(signum, frame):
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        status = child.wait()
        log.info(json.dumps({"event": "child_exited", "role": args.role, "returncode": status}))
        raise SystemExit(status if status >= 0 else 1)
    except Exception:
        log.error(json.dumps({"event": "start_refused", "role": args.role}))
        raise


if __name__ == "__main__":
    safe_cli(main)
