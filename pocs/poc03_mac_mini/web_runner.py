"""Independent restricted Web job; no MCP/Tunnel identity or key inheritance."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from common import ROOT, check_revision, host_guard, private_json, require, safe_cli

BROKER_ENV = {"IBKR_HOST", "IBKR_PORT", "IBKR_CLIENT_ID", "IBKR_ACCOUNT",
              "GUOJIN_GATEWAY_URL", "GUOJIN_GATEWAY_SECRET"}


def settings(root):
    from runner import verify_real_access
    root = Path(root).resolve(strict=True)
    runtime = private_json(root / "config/runtime.json")
    host_guard(runtime["expected_hostname"])
    require(runtime["mode"] == "real" and runtime.get("database_mode") == "shared_wal",
            "WEB_REQUIRES_REAL_SHARED_DATABASE")
    verify_real_access(root, private_json(root / "config/access-evidence.json"))
    web = private_json(root / "config/web.json")
    release = root / "releases" / web["revision"]
    require(ROOT.resolve() == (release / "code").resolve(), "WEB_RUNNER_REVISION_MISMATCH")
    require("web" in private_json(release / "installed.json").get("environments", []), "WEB_ENVIRONMENT_NOT_INSTALLED")
    check_revision(release / "code", web["revision"])
    from web_deploy import verify_dist
    verify_dist(release)
    wealth = private_json(root / "config/wealth.json")
    require(Path(wealth["database_path"]) == root / "data/wealthpilot.db" and
            wealth.get("database_contract_path") == str(root / "config/database-contract.json"),
            "WEB_DATABASE_PATH_MISMATCH")
    tiger = wealth.get("tiger_cash", {})
    require(not tiger or set(tiger) == {"TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"}, "WEB_CASH_CONFIG_INVALID")
    extra = private_json(root / "config/web-brokers.json") if (root / "config/web-brokers.json").exists() else {}
    require(set(extra) <= BROKER_ENV and all(type(v) is str and v for v in extra.values()),
            "WEB_BROKER_ENV_INVALID")
    if "guojin" not in web["enabled_brokers"]:
        extra = {k:v for k,v in extra.items() if not k.startswith("GUOJIN_")}
    if "snowball" not in web["enabled_brokers"]:
        extra = {k:v for k,v in extra.items() if not k.startswith("IBKR_")}
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": runtime["user_home"], "LANG": "en_US.UTF-8",
           "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PUBLIC_DEMO_MODE": "false",
           "BROKER_MODE": "ibkr.readonly", "IBKR_READ_ONLY_MODE": "true", "TIGER_READ_ONLY_MODE": "true",
           "ENABLE_IBKR_LIVE_TRADING": "false", "ENABLE_TIGER_LIVE_TRADING": "false",
           "FUTU_READ_ONLY_MODE": "true", "WEALTHPILOT_DB_PATH": wealth["database_path"],
           "WEALTHPILOT_SQLITE_CONTRACT": wealth["database_contract_path"], **wealth.get("tiger_cash", {}), **extra}
    config = {**wealth, "enabled_brokers": web["enabled_brokers"], "revision": web["revision"],
              "lock_path": str(root / "run/restricted-web.lock")}
    return release, config, env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    lifecycle = logging.getLogger("restricted-web-lifecycle")
    lifecycle.propagate = False
    lifecycle.setLevel(logging.INFO)
    handler = RotatingFileHandler(Path(args.root) / "logs/web.lifecycle.jsonl", maxBytes=1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    lifecycle.addHandler(handler)
    try:
        release, config, environment = settings(args.root)
        os.environ.clear()
        os.environ.update(environment)
        logging.getLogger().handlers = [logging.NullHandler()]
        sys.path.insert(0, str(ROOT))
        from backend.restricted_web import create_app
        import uvicorn
        app = create_app(config, release / "web-dist")
        lifecycle.info('{"event":"web_starting"}')
        uvicorn.run(app, host="127.0.0.1", port=8790, workers=1,
                    proxy_headers=False, access_log=False, log_config=None)
        lifecycle.info('{"event":"web_exited"}')
    except Exception:
        lifecycle.error('{"event":"web_start_refused"}')
        raise


if __name__ == "__main__":
    safe_cli(main)
