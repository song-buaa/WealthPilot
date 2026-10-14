"""Build/configure/control only the independent Web job. Never changes MCP jobs."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import uuid

from common import check_revision, digest, host_guard, private_json, require, run, safe_cli, write_json

LABEL = "com.wealthpilot.restricted-web"


def verify_dist(release):
    release = Path(release)
    proof = private_json(release / "web-assets.json")
    directory = release / "web-dist"
    require(proof["revision"] == release.name and "index.html" in proof["files"], "WEB_DIST_REVISION_MISMATCH")
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
    require(actual == set(proof["files"]), "WEB_DIST_FILES_MISMATCH")
    for name, sha in proof["files"].items():
        path = directory / name
        require(path.resolve().is_relative_to(directory.resolve()) and not path.is_symlink()
                and digest(path) == sha, "WEB_DIST_DIGEST_MISMATCH")


def build_web(release):
    require(os.geteuid() != 0, "BUILD_AS_NONROOT_SERVICE_USER")
    release = Path(release).resolve(strict=True)
    code = release / "code"
    check_revision(code, release.name)
    require(not (release / "web-dist").exists(), "WEB_DIST_ALREADY_EXISTS")
    require(run(["node", "-p", "process.versions.node.split('.')[0]"]).stdout.strip() == "22", "NODE22_REQUIRED")
    require(run(["node", "-p", "process.arch"]).stdout.strip() == "arm64", "NATIVE_ARM_NODE_REQUIRED")
    environment = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=code / "frontend", env=environment)
    run(["npm", "run", "build"], cwd=code / "frontend", env=environment)
    check_revision(code, release.name)
    shutil.copytree(code / "frontend/dist", release / "web-dist")
    files = {str(p.relative_to(release / "web-dist")): digest(p)
             for p in (release / "web-dist").rglob("*") if p.is_file()}
    write_json(release / "web-assets.json", {"revision": release.name, "files": files, "node_major": 22})
    verify_dist(release)


def configure(root, revision, enabled=(), gateways_verified=False):
    root = Path(root)
    check_revision(root / "releases" / revision / "code", revision)
    require("web" in private_json(root / "releases" / revision / "installed.json").get("environments", []),
            "WEB_ENVIRONMENT_NOT_INSTALLED")
    verify_dist(root / "releases" / revision)
    require(len(set(enabled)) == len(enabled) and set(enabled) <= {"tiger", "snowball", "guojin"}, "INVALID_ENABLED_BROKERS")
    require(not enabled or gateways_verified, "DEVICE_GATEWAY_VERIFICATION_REQUIRED")
    require(not (root / "config/web.json").exists(), "EXISTING_WEB_CONFIGURATION_REFUSED")
    require(os.geteuid() != 0, "CONFIGURE_AS_NONROOT_SERVICE_USER")
    write_json(root / "config/web.json", {"revision": revision, "enabled_brokers": list(enabled)})


def render_plist(root):
    root = Path(root)
    runtime = private_json(root / "config/runtime.json")
    web = private_json(root / "config/web.json")
    release = root / "releases" / web["revision"]
    require(runtime["user"] != "root", "ROOT_SERVICE_USER_REFUSED")
    return {"Label": LABEL, "UserName": runtime["user"], "WorkingDirectory": str(root),
            "ProgramArguments": [str(release / "envs/web/bin/python"),
                                 str(release / "code/pocs/poc03_mac_mini/web_runner.py"), "--root", str(root)],
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10, "ExitTimeOut": 45,
            "Umask": 0o077, "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def select_version(root, revision, services_stopped=False, apply=False):
    root = Path(root)
    release = root / "releases" / revision
    check_revision(release / "code", revision)
    require("web" in private_json(release / "installed.json").get("environments", []), "WEB_ENVIRONMENT_NOT_INSTALLED")
    verify_dist(release)
    web = private_json(root / "config/web.json")
    if apply:
        require(services_stopped, "STOP_WEB_BEFORE_VERSION_SWITCH")
        require(os.geteuid() != 0, "SELECT_AS_NONROOT_SERVICE_USER")
        write_json(root / "backups" / ("web-config-" + uuid.uuid4().hex + ".json"), web)
        web["revision"] = revision
        write_json(root / "config/web.json", web)
    return {"apply": apply, "revision": revision, "data_restored": False}


def set_brokers(root, enabled, gateways_verified=False, services_stopped=False, apply=False):
    root = Path(root)
    require(len(set(enabled)) == len(enabled) and set(enabled) <= {"tiger", "snowball", "guojin"}, "INVALID_ENABLED_BROKERS")
    require(not enabled or gateways_verified, "DEVICE_GATEWAY_VERIFICATION_REQUIRED")
    web = private_json(root / "config/web.json")
    if apply:
        require(services_stopped, "STOP_WEB_BEFORE_BROKER_CHANGE")
        require(os.geteuid() != 0, "CONFIGURE_AS_NONROOT_SERVICE_USER")
        write_json(root / "backups" / ("web-config-" + uuid.uuid4().hex + ".json"), web)
        web["enabled_brokers"] = list(enabled)
        write_json(root / "config/web.json", web)
    return {"apply": apply, "enabled_brokers": list(enabled), "sync_started": False}


def service_action(root, action, apply=False, daemon_dir=Path("/Library/LaunchDaemons"), execute=run):
    root = Path(root)
    contents = render_plist(root)
    path = daemon_dir / (LABEL + ".plist")
    if not apply:
        (root / "plists" / path.name).write_bytes(plistlib.dumps(contents))
        return {"apply": False, "label": LABEL, "action": action}
    require(os.geteuid() == 0, "ROOT_REQUIRED_FOR_SYSTEM_LAUNCHD")
    if path.exists():
        require(not path.is_symlink(), "WEB_DAEMON_SYMLINK_REFUSED")
        previous = plistlib.loads(path.read_bytes())
        require(previous.get("WorkingDirectory") == str(root) and previous.get("UserName") == contents["UserName"]
                and previous.get("Label") == LABEL, "WEB_DAEMON_BELONGS_TO_OTHER_DEPLOYMENT")
        args = previous.get("ProgramArguments", [])
        require(len(args) == 4 and args[-2:] == ["--root", str(root)] and
                Path(args[0]).is_relative_to(root / "releases") and
                Path(args[1]).is_relative_to(root / "releases"), "WEB_DAEMON_ENTRY_INVALID")
        if action == "start":
            require(args == contents["ProgramArguments"], "WEB_REINSTALL_PLIST_FOR_NEW_REVISION")
    else:
        try:
            execute(["/bin/launchctl", "print", "system/" + LABEL])
        except subprocess.CalledProcessError:
            pass
        else:
            raise ValueError("UNKNOWN_EXISTING_WEB_JOB_REFUSED")
    if action in ("stop", "restart", "install"):
        try:
            execute(["/bin/launchctl", "bootout", "system/" + LABEL])
        except subprocess.CalledProcessError:
            try:
                execute(["/bin/launchctl", "print", "system/" + LABEL])
            except subprocess.CalledProcessError:
                pass
            else:
                raise ValueError("WEB_JOB_STOP_FAILED")
    if action in ("install", "restart"):
        path.write_bytes(plistlib.dumps(contents)); os.chmod(path, 0o644); os.chown(path, 0, 0)
    if action in ("start", "install", "restart"):
        try:
            execute(["/bin/launchctl", "bootstrap", "system", str(path)])
        except Exception:
            try:
                execute(["/bin/launchctl", "bootout", "system/" + LABEL])
            except subprocess.CalledProcessError:
                pass
            raise
    return {"apply": True, "label": LABEL, "action": action}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-hostname", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("build"); p.add_argument("--revision", required=True)
    p = commands.add_parser("configure"); p.add_argument("--revision", required=True)
    p.add_argument("--enabled-brokers", default=""); p.add_argument("--gateways-verified", action="store_true")
    p = commands.add_parser("services"); p.add_argument("action", choices=("install", "start", "stop", "restart")); p.add_argument("--apply", action="store_true")
    p = commands.add_parser("select"); p.add_argument("--revision", required=True)
    p.add_argument("--apply", action="store_true"); p.add_argument("--services-stopped", action="store_true")
    p = commands.add_parser("brokers"); p.add_argument("--enabled-brokers", default="")
    p.add_argument("--gateways-verified", action="store_true"); p.add_argument("--apply", action="store_true")
    p.add_argument("--services-stopped", action="store_true")
    args = parser.parse_args()
    host_guard(args.expected_hostname)
    root = Path(args.root).expanduser().resolve(strict=True)
    result = {"ok": True, "web_start_requested": False}
    if args.command == "build":
        build_web(root / "releases" / args.revision)
    elif args.command == "configure":
        configure(root, args.revision, [b for b in args.enabled_brokers.split(",") if b], args.gateways_verified)
    elif args.command == "select":
        result.update(select_version(root, args.revision, args.services_stopped, args.apply))
    elif args.command == "brokers":
        result.update(set_brokers(root, [b for b in args.enabled_brokers.split(",") if b],
                                 args.gateways_verified, args.services_stopped, args.apply))
    else:
        result.update(service_action(root, args.action, args.apply))
        result["web_start_requested"] = args.apply and args.action in ("install", "start", "restart")
    print(json.dumps(result))


if __name__ == "__main__":
    safe_cli(main)
