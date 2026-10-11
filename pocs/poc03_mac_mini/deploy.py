"""Mac Mini operations. Preparation is inert; launchctl requires --apply + host guard."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import plistlib
import pwd
import shutil
import socket
import subprocess
import sys
import zipfile

from common import check_revision, digest, host_guard, private_dir, private_json, require, run, safe_cli, write_json
from runner import PROOF_FIELDS

LABELS = {"mcp": "com.wealthpilot.readonly-mcp", "tunnel": "com.wealthpilot.secure-tunnel"}
TUNNEL_SHA = "a160820d45089b5253d8d671fe2a689bde0f73d78c135d1497a7b33f28a3ac62"


def audit(root):
    existing = Path(root).expanduser().absolute()
    while not existing.exists():
        existing = existing.parent
    disk = shutil.disk_usage(existing)
    result = {"schema": "poc03.host-audit.v1", "system": platform.system(),
              "macos": platform.mac_ver()[0], "python": platform.python_version(),
              "python_architecture": platform.machine(), "native_arm64": platform.machine() == "arm64",
              "storage_free_bytes": disk.free, "minimum_5gib_free": disk.free >= 5 * 1024 ** 3,
              "git_available": Path("/usr/bin/git").exists(), "ssh_client_available": Path("/usr/bin/ssh").exists(),
              "ssh_server_remote_reachability": "NOT_TESTED", "outbound_https": "NOT_TESTED",
              "filevault_cold_boot_unlock": "NOT_TESTED", "sleep_power_recovery": "NOT_TESTED",
              "mac_mini_hardware_identity": "OPERATOR_MUST_VERIFY",
              "time": datetime.now(timezone.utc).isoformat()}
    if platform.system() == "Darwin":
        # Read-only local observations. No SSH session or settings changes.
        for key, command in (("filevault", ["/usr/bin/fdesetup", "status"]),
                             ("remote_login", ["/usr/sbin/systemsetup", "-getremotelogin"]),
                             ("power", ["/usr/bin/pmset", "-g", "custom"])):
            observation = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if key == "filevault":
                result[key] = "on" if "FileVault is On" in observation.stdout else "off" if "FileVault is Off" in observation.stdout else "UNKNOWN"
            elif key == "remote_login":
                result[key] = "on" if "Remote Login: On" in observation.stdout else "off" if "Remote Login: Off" in observation.stdout else "UNKNOWN_REQUIRES_LOCAL_PERMISSION"
            else:
                result[key] = {name: [line.strip().split()[-1] for line in observation.stdout.splitlines() if line.strip().startswith(name + " ")]
                               for name in ("sleep", "disksleep", "autorestart")}
    return result


def layout(root):
    root = private_dir(root)
    for name in ("releases", "data", "backups", "credentials", "config", "logs", "run", "plists", "evidence"):
        private_dir(root / name)
    return root


def verify_package(package, revision):
    package = Path(package).resolve(strict=True)
    manifest = json.loads((package / "release.json").read_text())
    require(len(revision) == 40 and all(c in "0123456789abcdef" for c in revision), "FULL_GIT_SHA_REQUIRED")
    require(manifest.get("schema") == "poc03.release.v1", "PACKAGE_SCHEMA_INVALID")
    require(manifest["revision"] == revision, "PACKAGE_REVISION_MISMATCH")
    require(set(manifest["files"]) == {"source.bundle", "source.tar.gz", "tunnel-client-darwin-arm64.zip",
                                       "wheels-arm64.zip", "MAC_MINI_EXECUTION.md"}, "PACKAGE_ASSET_MANIFEST_INCOMPLETE")
    for name, sha in manifest["files"].items():
        require(Path(name).name == name, "PACKAGE_FILENAME_INVALID")
        require(digest(package / name) == sha, "PACKAGE_FILE_DIGEST_MISMATCH")
    require(digest(package / "tunnel-client-darwin-arm64.zip") == TUNNEL_SHA, "OFFICIAL_TUNNEL_DIGEST_MISMATCH")
    return manifest


def install_release(root, package, revision, python):
    manifest = verify_package(package, revision)
    require(run([python, "-c", "import platform;print(platform.machine(),platform.python_version_tuple()[:2])"]).stdout.strip() == "arm64 ('3', '11')", "RELEASE_PYTHON_NOT_NATIVE_311")
    require(shutil.disk_usage(root).free >= 5 * 1024 ** 3, "INSUFFICIENT_FREE_DISK_SPACE")
    release = root / "releases" / revision
    require(not release.exists(), "RELEASE_ALREADY_EXISTS")
    release.mkdir(mode=0o700)
    run(["/usr/bin/git", "clone", "--no-hardlinks", str(Path(package) / "source.bundle"), str(release / "code")])
    run(["/usr/bin/git", "checkout", "--detach", revision], cwd=release / "code")
    check_revision(release / "code", revision)
    assets = release / "tunnel"; assets.mkdir(mode=0o700)
    with zipfile.ZipFile(Path(package) / "tunnel-client-darwin-arm64.zip") as archive:
        # Copy only the validated official binary, never a cloudflared runtime.
        names = [n for n in archive.namelist() if Path(n).name == "tunnel-client" and not n.endswith("/")]
        require(len(names) == 1, "TUNNEL_BINARY_NOT_UNIQUE")
        (assets / "tunnel-client").write_bytes(archive.read(names[0]))
    os.chmod(assets / "tunnel-client", 0o700)
    wheels = release / "wheels"; wheels.mkdir(mode=0o700)
    with zipfile.ZipFile(Path(package) / "wheels-arm64.zip") as archive:
        for name in archive.namelist():
            require(Path(name).name == name and name.endswith(".whl"), "WHEEL_ARCHIVE_INVALID")
            (wheels / name).write_bytes(archive.read(name))
    for role in ("mcp", "business"):
        environment = release / "envs" / role
        run([python, "-m", "venv", environment])
        lock = release / "code/pocs/poc03_mac_mini/locks" / (role + ".txt")
        run([environment / "bin/python", "-m", "pip", "install", "--no-index", "--find-links", wheels,
             "--require-hashes", "--only-binary=:all:", "-r", lock])
        run([environment / "bin/python", "-m", "pip", "check"])
    write_json(release / "installed.json", {"revision": revision, "package_verified": True,
                                            "dependencies_hash_checked": True, "source_main": manifest["source_main"]})
    return release


def configure(root, revision, expected_hostname, wealth_input, tunnel_id_file):
    release = root / "releases" / revision
    check_revision(release / "code", revision)
    user = pwd.getpwuid(os.getuid())
    require(user.pw_uid != 0, "CONFIGURE_AS_NONROOT_SERVICE_USER")
    require(not (root / "config/runtime.json").exists(), "EXISTING_RUNTIME_CONFIGURATION_REFUSED")
    wealth = private_json(wealth_input)
    require(set(wealth) <= {"portfolio_id", "tiger_cash"} and type(wealth["portfolio_id"]) is int and wealth["portfolio_id"] > 0, "WEALTH_INPUT_INVALID")
    wealth["database_path"] = str(root / "data/wealthpilot.db")
    tiger = wealth.get("tiger_cash", {})
    if tiger:
        require(set(tiger) == {"TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"} and all(type(v) is str and v for v in tiger.values()), "TIGER_CONFIGURATION_INVALID")
        key = Path(tiger["TIGER_PRIVATE_KEY_PATH"]).resolve(strict=True)
        require(key.is_relative_to(root / "credentials") and key.stat().st_mode & 0o077 == 0, "TIGER_KEY_MUST_BE_PRIVATE_PRODUCTION_FILE")
    tunnel_id_file = Path(tunnel_id_file)
    require(tunnel_id_file.stat().st_mode & 0o077 == 0, "TUNNEL_ID_FILE_MUST_BE_PRIVATE")
    tunnel_id = tunnel_id_file.read_text().strip()
    require(0 < len(tunnel_id) <= 200 and all(c.isalnum() or c in "-_" for c in tunnel_id), "TUNNEL_ID_INVALID")
    write_json(root / "config/wealth.json", wealth)
    write_json(root / "config/tunnel.json", {"config_version": 1,
        "control_plane": {"base_url": "https://api.openai.com", "tunnel_id": tunnel_id,
                          "api_key": "file:" + str(root / "credentials/runtime-key")},
        "health": {"listen_addr": "127.0.0.1:8789"}, "admin_ui": {"open_browser": False},
        "log": {"level": "warn", "format": "json"},
        "mcp": {"server_urls": [{"channel": "main", "url": "http://127.0.0.1:8788/mcp"}]}})
    runtime = {"revision": revision, "mode": "synthetic", "expected_hostname": expected_hostname,
               "user": user.pw_name, "user_home": user.pw_dir}
    write_json(root / "config/runtime.json", runtime)
    select_release(root, revision)
    return runtime


def select_release(root, revision):
    require((root / "releases" / revision / "installed.json").is_file(), "RELEASE_NOT_INSTALLED")
    candidate = root / "current.next"
    require(not candidate.exists() and not candidate.is_symlink(), "CURRENT_CANDIDATE_EXISTS")
    candidate.symlink_to(root / "releases" / revision)
    os.replace(candidate, root / "current")


def render_plists(root, runtime):
    require(runtime["user"] != "root", "ROOT_SERVICE_USER_REFUSED")
    result = {}
    for role, label in LABELS.items():
        # System-domain daemon runs as the chosen user, independent of GUI login.
        result[role] = {"Label": label, "UserName": runtime["user"],
            "ProgramArguments": [str(root / "current/envs/mcp/bin/python"),
                                  str(root / "current/code/pocs/poc03_mac_mini/runner.py"),
                                  "--root", str(root), "--role", role],
            "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 10, "ExitTimeOut": 20, "ProcessType": "Background", "Umask": 0o077,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}
    return result


def service_action(root, action, apply=False, daemon_dir=Path("/Library/LaunchDaemons"), execute=run):
    runtime = private_json(root / "config/runtime.json")
    plists = render_plists(root, runtime)
    commands = []
    if action in ("stop", "restart", "install"):
        commands += [["/bin/launchctl", "bootout", "system/" + LABELS[role]] for role in ("tunnel", "mcp")]
    if action in ("install", "start", "restart"):
        commands += [["/bin/launchctl", "bootstrap", "system", str(daemon_dir / (LABELS[role] + ".plist"))] for role in ("mcp", "tunnel")]
    if not apply:
        for role, contents in plists.items():
            (root / "plists" / (LABELS[role] + ".plist")).write_bytes(plistlib.dumps(contents))
        return {"apply": False, "planned_commands": commands}
    require(os.geteuid() == 0, "ROOT_REQUIRED_FOR_SYSTEM_LAUNCHD")
    # Check existing jobs before stopping/replacing anything.
    for role, expected in plists.items():
        path = daemon_dir / (LABELS[role] + ".plist")
        if path.exists():
            require(not path.is_symlink(), "EXISTING_DAEMON_SYMLINK_REFUSED")
            old = plistlib.loads(path.read_bytes())
            require(old.get("WorkingDirectory") == str(root) and old.get("ProgramArguments") == expected["ProgramArguments"]
                    and old.get("UserName") == runtime["user"], "EXISTING_DAEMON_BELONGS_TO_OTHER_DEPLOYMENT")
        else:
            try:
                execute(["/bin/launchctl", "print", "system/" + LABELS[role]])
            except subprocess.CalledProcessError:
                pass
            else:
                raise ValueError("UNKNOWN_EXISTING_LOADED_JOB_REFUSED")
    # bootout failure is acceptable only if the job is actually absent.
    for command in commands[:2] if action in ("stop", "restart", "install") else []:
        try:
            execute(command)
        except subprocess.CalledProcessError:
            try:
                execute(["/bin/launchctl", "print", command[-1]])
            except subprocess.CalledProcessError:
                continue
            raise ValueError("LAUNCHD_JOB_STOP_FAILED")
    if action == "install":
        for role, contents in plists.items():
            path = daemon_dir / (LABELS[role] + ".plist")
            path.write_bytes(plistlib.dumps(contents)); os.chmod(path, 0o644); os.chown(path, 0, 0)
    if action in ("install", "start", "restart"):
        starts = commands[-2:]
        try:
            for command in starts:
                execute(command)
        except Exception:
            # Leave no half-started ingress after a failed bootstrap.
            for role in ("tunnel", "mcp"):
                try: execute(["/bin/launchctl", "bootout", "system/" + LABELS[role]])
                except subprocess.CalledProcessError: pass
            raise
    return {"apply": True, "action": action}


def switch_mode_or_revision(root, revision, mode, apply):
    runtime = private_json(root / "config/runtime.json")
    if mode == "real":
        proof = private_json(root / "config/access-evidence.json")
        require(all(proof.get(k) is True for k in PROOF_FIELDS), "REAL_ACCESS_NOT_VERIFIED")
        require((root / "data/wealthpilot.db").is_file(), "PRODUCTION_DATABASE_MISSING")
    check_revision(root / "releases" / revision / "code", revision)
    if not apply:
        return {"apply": False, "revision": revision, "mode": mode}
    require(os.geteuid() == 0, "ROOT_REQUIRED_FOR_SWITCH")
    service_action(root, "stop", apply=True)
    write_json(root / "backups/previous-runtime.json", runtime)
    runtime.update(revision=revision, mode=mode)
    write_json(root / "config/runtime.json", runtime)
    # Root must retain readability for the nonroot daemon owner.
    user = pwd.getpwnam(runtime["user"])
    os.chown(root / "config/runtime.json", user.pw_uid, user.pw_gid)
    select_release(root, revision)
    service_action(root, "start", apply=True)
    return {"apply": True, "revision": revision, "mode": mode, "data_restored": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    p = commands.add_parser("prepare"); p.add_argument("--expected-hostname", required=True)
    p = commands.add_parser("install-release"); p.add_argument("--package", required=True); p.add_argument("--revision", required=True); p.add_argument("--python", required=True); p.add_argument("--expected-hostname", required=True)
    p = commands.add_parser("configure"); p.add_argument("--revision", required=True); p.add_argument("--wealth-input", required=True); p.add_argument("--tunnel-id-file", required=True); p.add_argument("--expected-hostname", required=True)
    p = commands.add_parser("save-key"); p.add_argument("--expected-hostname", required=True)
    p = commands.add_parser("services"); p.add_argument("action", choices=("install", "start", "stop", "restart")); p.add_argument("--apply", action="store_true"); p.add_argument("--expected-hostname", required=True)
    p = commands.add_parser("switch"); p.add_argument("--revision", required=True); p.add_argument("--mode", choices=("synthetic", "real"), required=True); p.add_argument("--apply", action="store_true"); p.add_argument("--expected-hostname", required=True)
    args = parser.parse_args()
    root = Path(args.root).expanduser().absolute()
    if args.command == "audit":
        print(json.dumps(audit(root))); return
    host_guard(args.expected_hostname)
    if args.command == "prepare":
        layout(root); result = {"prepared": True}
    elif args.command == "install-release":
        install_release(root, args.package, args.revision, args.python); result = {"installed": True}
    elif args.command == "configure":
        configure(root, args.revision, args.expected_hostname, args.wealth_input, args.tunnel_id_file); result = {"configured": True, "mode": "synthetic"}
    elif args.command == "save-key":
        secret = getpass.getpass("New production runtime key (hidden): ").strip()
        require(bool(secret), "EMPTY_KEY_REFUSED")
        with (root / "credentials/runtime-key").open("x") as output:
            os.chmod(output.name, 0o600); output.write(secret)
        result = {"new_key_saved": True}
    elif args.command == "services":
        result = service_action(root, args.action, args.apply)
    else:
        result = switch_mode_or_revision(root, args.revision, args.mode, args.apply)
    print(json.dumps({"ok": True, **result}))


if __name__ == "__main__":
    safe_cli(main)
