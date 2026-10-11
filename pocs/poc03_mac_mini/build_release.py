"""Package only a clean committed Git revision plus pinned, verified dependency assets."""
import argparse
from pathlib import Path
import shutil
import subprocess
import zipfile
import json

from common import ROOT, check_revision, digest, require, run, safe_cli
from deploy import TUNNEL_SHA


def build(output, wheels, tunnel_zip):
    revision = run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    check_revision(ROOT, revision)
    output = Path(output).absolute()
    require(not output.resolve().is_relative_to(ROOT) and not output.exists(), "FRESH_EXTERNAL_PACKAGE_DIRECTORY_REQUIRED")
    require(digest(tunnel_zip) == TUNNEL_SHA, "OFFICIAL_TUNNEL_DIGEST_MISMATCH")
    output.mkdir(parents=True, mode=0o700)
    refs = ["HEAD"]
    try:
        refs.append(run(["/usr/bin/git", "symbolic-ref", "--quiet", "HEAD"], cwd=ROOT).stdout.strip())
    except subprocess.CalledProcessError:
        pass  # Rebuilding from a detached, verified deployment revision is valid.
    run(["/usr/bin/git", "bundle", "create", output / "source.bundle", *refs], cwd=ROOT)
    run(["/usr/bin/git", "bundle", "verify", output / "source.bundle"], cwd=ROOT)
    run(["/usr/bin/git", "archive", "--format=tar.gz", "--output=" + str(output / "source.tar.gz"), revision], cwd=ROOT)
    shutil.copyfile(tunnel_zip, output / "tunnel-client-darwin-arm64.zip")
    wheel_files = sorted(Path(wheels).glob("*.whl"))
    require(bool(wheel_files), "ARM_WHEELS_REQUIRED")
    with zipfile.ZipFile(output / "wheels-arm64.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for wheel in wheel_files:
            entry = zipfile.ZipInfo(wheel.name, (2026, 1, 1, 0, 0, 0)); entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, wheel.read_bytes())
    shutil.copyfile(ROOT / "docs/WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md", output / "MAC_MINI_EXECUTION.md")
    source_main = json.loads((ROOT / "pocs/poc03_mac_mini/evidence/baseline-inventory.json").read_text())["main"]
    manifest = {"schema": "poc03.release.v1", "revision": revision, "source_main": source_main,
                "stage": "PREPARATION_ONLY_NOT_PRODUCTION_ACCEPTED", "target": "macOS native arm64 CPython 3.11",
                "files": {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()},
                "contains_database_or_credentials": False,
                "credential_incident": "PoC02 FAIL; production uses an independently created key"}
    (output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(digest(p) + "  " + p.name + "\n" for p in sorted(output.iterdir()) if p.is_file()))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True); parser.add_argument("--wheels", required=True); parser.add_argument("--tunnel-zip", required=True)
    args = parser.parse_args()
    manifest = build(args.output, args.wheels, args.tunnel_zip)
    print(json.dumps({"ok": True, "revision": manifest["revision"], "database_and_credentials_included": False}))


if __name__ == "__main__":
    safe_cli(main)
