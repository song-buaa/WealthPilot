"""GitHub deployment guards with fake API/assets and disposable local Git only."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import github_release as release

SHA = "a" * 40


def checks():
    return [{"id": i, "name": name, "app": {"slug": "github-actions"}, "head_sha": SHA,
             "status": "completed", "conclusion": "success", "html_url": "https://github.com/fixture/check/" + str(i)}
            for i, name in enumerate(release.CHECKS, 1)]


def test_exact_sha_requires_all_four_successful_checks():
    evidence = release.ci_evidence(SHA, lambda _: {"check_runs": checks()})
    assert set(evidence["checks"]) == set(release.CHECKS)
    with pytest.raises(ValueError, match="CI_NOT_GREEN"):
        release.ci_evidence(SHA, lambda _: {"check_runs": checks()[:-1]})


def test_newer_failed_rerun_not_hidden_by_old_success():
    runs = checks(); failed = dict(runs[0], id=100, conclusion="failure")
    with pytest.raises(ValueError, match="CI_NOT_GREEN"):
        release.ci_evidence(SHA, lambda _: {"check_runs": runs + [failed]})


def test_other_app_or_revision_cannot_supply_ci_pass():
    for field, value in (("app", {"slug": "fixture"}), ("head_sha", "b" * 40), ("status", "in_progress")):
        runs = checks(); runs[0][field] = value
        with pytest.raises(ValueError, match="CI_NOT_GREEN"):
            release.ci_evidence(SHA, lambda _: {"check_runs": runs})


def test_short_sha_and_branch_ref_refused():
    for revision in ("main", "a" * 7, "../outside"):
        with pytest.raises(ValueError, match="FULL_GIT_SHA"):
            release.ci_evidence(revision, lambda _: pytest.fail("no API call"))


def test_bad_cached_asset_refused_without_network(tmp_path):
    target = tmp_path / "asset"; target.write_bytes(b"keep")
    with pytest.raises(ValueError, match="CACHED_ASSET"):
        release.download("https://files.pythonhosted.org/fixture", target, "a" * 64)
    assert target.read_bytes() == b"keep"


def test_token_url_and_non_https_refused(tmp_path):
    for url in ("http://github.com/file", "https://token@github.com/file", "https://example.invalid/file"):
        with pytest.raises(ValueError, match="UNTRUSTED_ASSET_URL"):
            release.download(url, tmp_path / "asset", "a" * 64)


def test_vendored_tiger_wheel_matches_stage1_asset_digest():
    tools = Path(__file__).resolve().parent
    wheel = tools / "vendor/tigeropen-3.5.8-py3-none-any.whl"
    records = json.loads((tools / "evidence/arm-wheel-assets.json").read_text())["wheels"]
    expected = next(r["sha256"] for r in records if r["filename"] == wheel.name)
    assert common.digest(wheel) == expected


def test_unmerged_git_commit_refused_before_assets_or_installer(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    common.run([common.GIT, "init", "-b", "main", source])
    def commit(value):
        (source / "fixture").write_text(value)
        common.run([common.GIT, "add", "fixture"], cwd=source)
        common.run([common.GIT, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", value], cwd=source)
        return common.run([common.GIT, "rev-parse", "HEAD"], cwd=source).stdout.strip()
    commit("base"); common.run([common.GIT, "switch", "-c", "unmerged"], cwd=source)
    unmerged = commit("candidate"); common.run([common.GIT, "switch", "main"], cwd=source)
    root = tmp_path / "prod"; (root / "run").mkdir(parents=True)
    monkeypatch.setattr(release, "ci_evidence", lambda sha: {"revision": sha})
    def local_git(argv, **kwargs):
        return common.run([str(source) if str(arg) == release.REMOTE else arg for arg in argv], **kwargs)
    monkeypatch.setattr(release, "run", local_git)
    monkeypatch.setattr(release, "stage_assets", lambda *a: pytest.fail("unmerged asset download"))
    monkeypatch.setattr(release, "install_release", lambda *a: pytest.fail("unmerged install"))
    with pytest.raises(subprocess.CalledProcessError):
        release.install_github(root, unmerged, sys.executable)
    assert not list((root / "run").iterdir())


def test_verified_install_keeps_active_version_data_and_key(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    common.run([common.GIT, "init", "-b", "main", source])
    (source / "fixture").write_text("code")
    common.run([common.GIT, "add", "fixture"], cwd=source)
    common.run([common.GIT, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "code"], cwd=source)
    revision = common.run([common.GIT, "rev-parse", "HEAD"], cwd=source).stdout.strip()
    root = tmp_path / "prod"; (root / "run").mkdir(parents=True)
    for name in ("data", "runtime-key", "current"):
        (root / name).write_text("preserved fixture")
    monkeypatch.setattr(release, "ci_evidence", lambda sha: {"revision": sha, "checks": {}})
    def local_git(argv, **kwargs):
        return common.run([str(source) if str(arg) == release.REMOTE else arg for arg in argv], **kwargs)
    monkeypatch.setattr(release, "run", local_git)
    monkeypatch.setattr(release, "stage_assets", lambda *a: ("fixture-wheels", "fixture-tunnel"))
    def build(package, wheels, tunnel, code):
        common.check_revision(code, revision); package.mkdir()
    monkeypatch.setattr(release, "build", build)
    installed = root / "releases" / revision; installed.mkdir(parents=True)
    monkeypatch.setattr(release, "install_release", lambda *a: installed)
    assert release.install_github(root, revision, sys.executable) == installed
    assert (installed / "github-install.json").is_file()
    assert all((root / name).read_text() == "preserved fixture" for name in ("data", "runtime-key", "current"))
    assert not list((root / "run").iterdir())
