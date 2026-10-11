"""Pull an exact main commit, require green CI, and reuse the verified installer.

Public GitHub/PyPI downloads only; no runtime key, database or account access.
Installation does not switch the active version or start launchd services.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from common import GIT, check_revision, digest, require, run, write_json
from build_release import build
from deploy import TUNNEL_SHA, install_release

REPOSITORY = "song-buaa/WealthPilot"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
CHECKS = ("Backend tests", "Frontend checks", "Offline M5", "MCP PoC tests")
TUNNEL_URL = "https://github.com/openai/tunnel-client/releases/download/v0.0.16/tunnel-client-v0.0.16-darwin-arm64.zip"


def get_json(url):
    request = Request(url, headers={"User-Agent": "WealthPilot-PoC03", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def ci_evidence(revision, fetch=get_json):
    require(len(revision) == 40 and all(c in "0123456789abcdef" for c in revision), "FULL_GIT_SHA_REQUIRED")
    latest = {}
    for page in range(1, 11):
        payload = fetch(f"https://api.github.com/repos/{REPOSITORY}/commits/{revision}/check-runs?per_page=100&page={page}&filter=all")
        for check in payload["check_runs"]:
            name = check.get("name")
            if name in CHECKS and check.get("app", {}).get("slug") == "github-actions" and check.get("head_sha") == revision:
                if name not in latest or check["id"] > latest[name]["id"]:
                    latest[name] = check
        if len(payload["check_runs"]) < 100:
            break
    else:
        raise ValueError("CI_CHECK_PAGINATION_LIMIT_REACHED")
    require(all(name in latest and latest[name]["status"] == "completed" and latest[name]["conclusion"] == "success"
                for name in CHECKS), "EXACT_COMMIT_CI_NOT_GREEN")
    return {"revision": revision, "checks": {name: {"id": latest[name]["id"], "url": latest[name]["html_url"],
                                                    "conclusion": "success"} for name in CHECKS}}


def download(url, destination, sha):
    parsed = urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in ("github.com", "files.pythonhosted.org")
            and parsed.username is None, "UNTRUSTED_ASSET_URL")
    destination = Path(destination)
    if destination.exists():
        require(digest(destination) == sha, "CACHED_ASSET_DIGEST_MISMATCH")
        return
    fd, name = tempfile.mkstemp(dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, urlopen(Request(url, headers={"User-Agent": "WealthPilot-PoC03"}), timeout=30) as response:
            shutil.copyfileobj(response, output)
        require(digest(name) == sha, "DOWNLOADED_ASSET_DIGEST_MISMATCH")
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def stage_assets(code, directory, fetch=get_json, transfer=download):
    directory = Path(directory); directory.mkdir(mode=0o700)
    wheels = directory / "wheels"; wheels.mkdir(mode=0o700)
    tools = Path(code) / "pocs/poc03_mac_mini"
    records = json.loads((tools / "evidence/arm-wheel-assets.json").read_text())["wheels"]
    for record in records:
        name, sha = record["filename"], record["sha256"]
        require(Path(name).name == name and name.endswith(".whl"), "ASSET_FILENAME_INVALID")
        target = wheels / name
        vendored = tools / "vendor" / name
        if vendored.is_file():
            require(name == "tigeropen-3.5.8-py3-none-any.whl" and digest(vendored) == sha, "VENDORED_WHEEL_DIGEST_MISMATCH")
            shutil.copyfile(vendored, target)
            continue
        project, version = name.split("-")[:2]
        payload = fetch(f"https://pypi.org/pypi/{project}/{version}/json")
        urls = [item for item in payload["urls"] if item["filename"] == name and item["digests"]["sha256"] == sha]
        require(len(urls) == 1, "PINNED_PYPI_WHEEL_UNAVAILABLE")
        transfer(urls[0]["url"], target, sha)
        require(digest(target) == sha, "STAGED_WHEEL_DIGEST_MISMATCH")
    tunnel = directory / "tunnel-client-darwin-arm64.zip"
    transfer(TUNNEL_URL, tunnel, TUNNEL_SHA)
    require(digest(tunnel) == TUNNEL_SHA, "OFFICIAL_TUNNEL_DIGEST_MISMATCH")
    return wheels, tunnel


def install_github(root, revision, python):
    evidence = ci_evidence(revision)  # refuse missing/pending/failed checks before local install.
    with tempfile.TemporaryDirectory(prefix="github-install-", dir=root / "run") as temporary:
        staging = Path(temporary)
        code = staging / "code"
        run([GIT, "clone", "--no-checkout", "--branch", "main", REMOTE, code])
        run([GIT, "merge-base", "--is-ancestor", revision, "origin/main"], cwd=code)
        run([GIT, "checkout", "--detach", revision], cwd=code)
        check_revision(code, revision)
        wheels, tunnel = stage_assets(code, staging / "assets")
        # Reuse Stage 1 package validation/installation in a temporary directory.
        # This is not a long-lived offline release or a MacBook transfer.
        build(staging / "package", wheels, tunnel, code=code)
        release = install_release(root, staging / "package", revision, python)
        write_json(release / "github-install.json", {"source": REMOTE, "main_ancestor": True, "ci": evidence})
    return release
