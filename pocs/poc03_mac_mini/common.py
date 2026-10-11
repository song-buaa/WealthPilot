"""Shared local-file checks. No credentials or financial values in diagnostics."""
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git") or "/usr/bin/git"


def require(condition, code):
    if not condition:
        raise ValueError(code)


def private_dir(path):
    path = Path(path).expanduser().absolute()
    require(not path.resolve().is_relative_to(ROOT), "PRIVATE_FILES_OUTSIDE_CODE_REQUIRED")
    require(not path.is_symlink(), "PRIVATE_DIRECTORY_SYMLINK_REFUSED")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(path.stat().st_mode & 0o077 == 0, "PRIVATE_DIRECTORY_MODE_0700_REQUIRED")
    return path


def private_json(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "PRIVATE_FILE_REQUIRED")
    require(path.stat().st_mode & 0o077 == 0, "PRIVATE_FILE_MODE_0600_REQUIRED")
    return json.loads(path.read_text())


def write_json(path, value):
    path = Path(path)
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def digest(path):
    with Path(path).open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def run(argv, **kwargs):
    # Captured failures are never echoed: dependency/config output can be private.
    return subprocess.run([str(x) for x in argv], capture_output=True,
                          text=True, check=True, **kwargs)


def host_guard(expected_hostname):
    require(platform.system() == "Darwin", "MACOS_REQUIRED")
    require(platform.machine() == "arm64", "NATIVE_ARM64_PYTHON_REQUIRED")
    require(int(platform.mac_ver()[0].split(".")[0]) >= 13, "MACOS_13_OR_NEWER_REQUIRED")
    require(platform.python_version_tuple()[:2] == ("3", "11"), "PYTHON_311_REQUIRED")
    require(socket.gethostname() == expected_hostname, "HOSTNAME_CONFIRMATION_MISMATCH")


def check_revision(code, expected):
    require(len(expected) == 40 and all(c in "0123456789abcdef" for c in expected), "FULL_GIT_SHA_REQUIRED")
    # sudo switch reads the nonroot user's checkout. Trust this explicit local
    # checkout for these read-only calls only; do not change global Git config.
    git = [GIT, "-c", "safe.directory=" + str(Path(code).resolve())]
    require(run(git + ["rev-parse", "HEAD"], cwd=code).stdout.strip() == expected, "REVISION_MISMATCH")
    run(git + ["diff", "--exit-code", "HEAD"], cwd=code)
    require(not run(git + ["ls-files", "--others", "--exclude-standard"], cwd=code).stdout.strip(), "UNTRACKED_DEPLOYMENT_FILES_REFUSED")


def safe_cli(main):
    try:
        main()
    except Exception as error:
        # Only our uppercase codes; never provider errors, JSON or subprocess output.
        code = str(error) if type(error) is ValueError and str(error).replace("_", "").isalnum() and str(error).isupper() else "OPERATION_FAILED"
        print(json.dumps({"ok": False, "error": code}))
        raise SystemExit(1)
