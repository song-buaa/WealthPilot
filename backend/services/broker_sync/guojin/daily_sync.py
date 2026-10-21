"""Guojin-only native batch producer. No scheduler, order API or asset math.

The private GUI profile is an observed read-only操作合同, not executable code.
The original parser and SyncExecutor remain the publication authorities.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, time, timedelta
import base64
import fcntl
import gzip
import json
import os
from pathlib import Path, PureWindowsPath
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

from services.broker_sync.guojin.native_exports import (
    FILES, ERRORS, load_batch, private_json, read_config, require, sha, stamp,
)

CODES = ERRORS | {"AUTO_DISABLED", "AUTO_BUSY", "AUTO_TIMEOUT", "AUTO_CONFIG_INVALID",
    "AUTO_GUI_UNAVAILABLE", "AUTO_SESSION_UNAVAILABLE", "AUTO_LOGIN_REQUIRED", "AUTO_ACCOUNT_MISMATCH",
    "AUTO_QUERY_UNCHANGED", "AUTO_COUNTER_120022", "AUTO_GUI_CONTRACT_UNVERIFIED",
    "AUTO_EVIDENCE_INVALID", "AUTO_SYNC_FAILED", "AUTO_CALENDAR_UNKNOWN", "AUTO_WINDOW_CLOSED"}
SHANGHAI = ZoneInfo("Asia/Shanghai")
PS_SCRIPT = Path(__file__).with_name("native_export.ps1")
SYNC_SCRIPT = Path(__file__).resolve().parents[3]/"scripts/guojin_sync_e2e.py"


def atomic_json(path, value):
    """Original ready contract: private same-filesystem replace + fsync."""
    require(not path.is_symlink(), "AUTO_CONFIG_INVALID")
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def single_run(root):
    # Kernel lock dies with process, including after reboot; never delete/recreate
    # the lock inode. The Windows named mutex independently fences orphaned GUI work.
    fd = os.open(root/"automation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(fd).st_uid == os.getuid() and os.fstat(fd).st_mode & 0o077 == 0,
                "AUTO_CONFIG_INVALID")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("AUTO_BUSY") from None
        yield
    finally:
        os.close(fd)


def bounded(argv, *, timeout, allow_failure=False):
    """No shell, credentials or raw stdout in diagnostics; kill our process group."""
    with subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True) as process:
        try:
            out, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError("AUTO_TIMEOUT") from None
        require(process.returncode == 0 or allow_failure, "AUTO_GUI_UNAVAILABLE")
        return out


def scheduled_day(now):
    # Reuse D1's maintained calendars. Unknown/out-of-range is never weekday fallback.
    try:
        import exchange_calendars as calendars
        day = now.astimezone(SHANGHAI).date().isoformat()
        return any(calendars.get_calendar(c).is_session(day) for c in ("XSHG", "XHKG"))
    except (ImportError, ValueError, TypeError):
        raise ValueError("AUTO_CALENDAR_UNKNOWN") from None


def automation_config(config):
    auto = config.get("automation", {})
    require(auto.get("enabled") is True, "AUTO_DISABLED")
    require(auto.get("schema") == "guojin.automation.v1", "AUTO_CONFIG_INVALID")
    require(isinstance(auto.get("vm"), str) and bool(auto["vm"].strip()), "AUTO_CONFIG_INVALID")
    root = Path(auto.get("shared_root", ""))
    require(root.is_absolute() and root.is_dir() and root.resolve() == root, "AUTO_CONFIG_INVALID")
    guest = PureWindowsPath(auto.get("guest_root", ""))
    require(guest.is_absolute() and ".." not in guest.parts, "AUTO_CONFIG_INVALID")
    profile = private_json(Path(auto.get("profile", "")))
    require(profile.get("schema") == "guojin.gui-profile.v1" and
            profile.get("verified_readonly") is True and profile.get("evidence_ref"),
            "AUTO_GUI_CONTRACT_UNVERIFIED")
    require(set(profile.get("markets", {})) == set(FILES), "AUTO_GUI_CONTRACT_UNVERIFIED")
    def nonempty(value):
        return isinstance(value, str) and 0 < len(value) <= 2048
    def selector(value, *, read=False, button=False):
        require(isinstance(value, dict) and nonempty(value.get("id")) and nonempty(value.get("class"))
            and (not read or nonempty(value.get("pattern")) and "(?<value>" in value["pattern"])
            and (not button or nonempty(value.get("name"))), "AUTO_GUI_CONTRACT_UNVERIFIED")
    require(all(nonempty(profile.get(k)) for k in ("window_pattern", "account_pattern", "save_title",
            "excel_prompt_title")) and "(?<account>" in profile["account_pattern"] and
            all(type(profile.get(k)) is int and 0 < profile[k] <= 10000 for k in ("width", "height")),
            "AUTO_GUI_CONTRACT_UNVERIFIED")
    selector(profile.get("filename"))
    for key in ("save_button", "excel_no"):
        selector(profile.get(key), button=True)
    for key, market in profile["markets"].items():
        require(nonempty(market.get("page_pattern")), "AUTO_GUI_CONTRACT_UNVERIFIED")
        for field in ("query", "rows", "success") + (("total",) if key == "stock" else ()):
            selector(market.get(field), read=True)
        for steps in (market.get("open"), market.get("export"), [market.get("refresh")]):
            require(isinstance(steps, list) and len(steps) <= 16, "AUTO_GUI_CONTRACT_UNVERIFIED")
            for step in steps:
                require(isinstance(step, dict) and nonempty(step.get("expect")) and
                    step.get("button", "left") in {"left", "right"} and
                    all(type(step.get(c)) is int and 0 <= step[c] < profile[limit]
                        for c, limit in (("x", "width"), ("y", "height"))), "AUTO_GUI_CONTRACT_UNVERIFIED")
        require(bool(market["export"]), "AUTO_GUI_CONTRACT_UNVERIFIED")
    return auto, profile


def export_windows(config, auto, profile, batch, now):
    """Exact original current-user launcher; no fallback login or guessed UI clicks."""
    executable = auto.get("prlctl", "/usr/local/bin/prlctl")
    status = bounded([executable, "status", auto["vm"]], timeout=15).decode("utf-8", errors="replace")
    # VM startup is explicit and bounded, never resets a running guest/client.
    if re.search(r"\b(suspended|paused|stopped)\b", status):
        verb = "start" if "stopped" in status else "resume"
        bounded([executable, verb, auto["vm"]], timeout=60)
    require("running" in bounded([executable, "status", auto["vm"]], timeout=15).decode(errors="replace"),
            "AUTO_SESSION_UNAVAILABLE")
    request = dict(batch=batch, account_id=config["account_id"], profile=profile,
        directory=str(PureWindowsPath(auto["guest_root"])/batch),
        deadline=(now.timestamp()+600))
    payload = base64.b64encode(json.dumps(request, ensure_ascii=False).encode()).decode()
    script = "$WPRequest='"+payload+"'\n"+PS_SCRIPT.read_text()
    compressed = base64.b64encode(gzip.compress(script.encode("utf-8"), mtime=0)).decode()
    # prlctl timeout alone doesn't cancel a guest process. A guest-side parent
    # owns ONLY this exporter child and kills it after the finite budget; never
    # kill xiadan or any shared trading process. Abandoned mutexes are recoverable.
    launcher = r'''
$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$out=[IO.Path]::GetTempFileName(); $err=[IO.Path]::GetTempFileName(); $child=$null
$taskfile=Join-Path ([IO.Path]::GetTempPath()) ('wp-guojin-'+[Guid]::NewGuid().ToString('N')+'.ps1')
try {
 $bytes=[Convert]::FromBase64String('COMPRESSED')
 $memory=New-Object IO.MemoryStream(,$bytes)
 $zip=New-Object IO.Compression.GzipStream($memory,[IO.Compression.CompressionMode]::Decompress)
 $reader=New-Object IO.StreamReader($zip,[Text.Encoding]::UTF8)
 try { $source=$reader.ReadToEnd() } finally { $reader.Dispose(); $zip.Dispose(); $memory.Dispose() }
 [IO.File]::WriteAllText($taskfile,$source,(New-Object Text.UTF8Encoding($true)))
 $child=Start-Process powershell.exe -ArgumentList @('-NoProfile','-NonInteractive','-File',('"'+$taskfile+'"')) -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
 if (-not $child.WaitForExit(610000)) {
  $child.Kill(); $child.WaitForExit()
  [Console]::WriteLine('{"status":"FAILED","reason_code":"AUTO_TIMEOUT"}')
 } else { [Console]::WriteLine([IO.File]::ReadAllText($out)) }
} finally {
 if ($child -and -not $child.HasExited) { $child.Kill(); $child.WaitForExit() }
 Remove-Item -LiteralPath $out,$err,$taskfile -Force -ErrorAction SilentlyContinue
}
'''.replace("COMPRESSED", compressed)
    command = base64.b64encode(launcher.encode("utf-16le")).decode()
    require(len(command) < 28000, "AUTO_GUI_CONTRACT_UNVERIFIED")  # Windows CreateProcess 32767-char ceiling
    result = bounded([executable, "exec", auto["vm"], "--current-user", "powershell.exe",
        "-NoProfile", "-NonInteractive", "-EncodedCommand",
        command], timeout=640)
    try:
        response = json.loads(result.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ValueError("AUTO_GUI_UNAVAILABLE") from None
    require(response.get("status") == "EXPORTED", response.get("reason_code")
            if response.get("reason_code") in CODES else "AUTO_GUI_UNAVAILABLE")
    return Path(auto["shared_root"])/batch


def seal_candidate(config, directory, source, started, finished):
    """Bind observed GUI evidence, not file mtime or parsed row counts, to originals."""
    require(source.is_dir() and source.resolve() == source, "AUTO_EVIDENCE_INVALID")
    observation_path = source/"observations.json"
    require(not observation_path.is_symlink() and observation_path.stat().st_size <= 1024*1024,
            "AUTO_EVIDENCE_INVALID")
    observed = json.loads(observation_path.read_text(encoding="utf-8-sig"))
    require(observed.get("schema") == "guojin.gui-observations.v1" and
            observed.get("batch") == directory.name and observed.get("status") == "EXPORTED",
            "AUTO_EVIDENCE_INVALID")
    require(set(observed.get("markets", {})) == set(FILES), "NATIVE_BATCH_INCOMPLETE")
    files = {}
    for key, name in FILES.items():
        item = observed["markets"][key]
        require(item.get("account_id") == config["account_id"] and item.get("complete") is True,
                "AUTO_ACCOUNT_MISMATCH")
        query, before = stamp(item["queried_at"]), stamp(item["query_before"])
        require(started-timedelta(seconds=1) <= query <= finished and before < query, "AUTO_QUERY_UNCHANGED")
        require(type(item.get("row_count")) is int and item["row_count"] >= 0, "AUTO_EVIDENCE_INVALID")
        require(stamp(item["exported_at"]) >= query and stamp(item["exported_at"]) <= finished,
                "AUTO_EVIDENCE_INVALID")
        for filename in (name, key+".png"):
            original = source/filename
            require(not original.is_symlink() and original.is_file() and
                    0 < original.stat().st_size <= 8*1024*1024, "NATIVE_BATCH_INCOMPLETE")
            data = original.read_bytes()
            expected = item["sha256"] if filename == name else item["evidence_sha256"]
            require(sha(data) == expected, "AUTO_EVIDENCE_INVALID")
            destination = directory/filename
            with destination.open("xb") as out:
                out.write(data)
            destination.chmod(0o600)
        files[key] = dict(account_id=item["account_id"], account_verified_in_gui=True,
            evidence_ref=key+".png#sha256="+item["evidence_sha256"], queried_at=item["queried_at"],
            row_count=item["row_count"], sha256=item["sha256"])
    atomic_json(directory/"observations.json", observed)
    atomic_json(directory/"batch.json", dict(schema="guojin.native-batch.v1",
        portfolio_id=config["portfolio_id"], account_id=config["account_id"],
        account_total_assets_cny=observed["account_total_assets_cny"], files=files))
    return load_batch(config, candidate=directory.name)


def advance_ready(config, batch, metadata):
    root = Path(config["inbox"])
    if (root/"ready.json").exists():
        previous = private_json(root/"ready.json").get("batch")
        require(isinstance(previous, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", previous),
                "NATIVE_BATCH_INVALID")
        old = private_json(root/previous/"batch.json")
        new = metadata["native_batch"]["queries"]
        # Stale existing ready may be >24h old; compare raw bound source times,
        # not its current eligibility. Never regress ready even before DB fence.
        require(previous == batch or all(stamp(new[k]) > stamp(old["files"][k]["queried_at"])
            for k in FILES), "NATIVE_BATCH_STALE")
    atomic_json(root/"ready.json", {"batch":batch})


def publish(config_path, database, contract):
    # The existing explicit CLI performs strict Digest/Owner/allowlist checks and
    # original Executor claim/fence. It never starts backend lifespan or other brokers.
    out = bounded([sys.executable, SYNC_SCRIPT, "--config", config_path, "--publish",
        "--database", database, "--contract", contract, "--reuse-published"], timeout=90, allow_failure=True)
    try:
        result = json.loads(out)
    except ValueError:
        raise ValueError("AUTO_SYNC_FAILED") from None
    require(result.get("published") is True, result.get("reason_code")
            if result.get("reason_code") in CODES else "AUTO_SYNC_FAILED")
    return result


def run(config_path, *, trigger="manual", database=None, contract=None, retry_batch=None,
        publish_assets=False, exporter=export_windows, publisher=publish, clock=lambda:datetime.now(timezone.utc)):
    require(trigger in {"manual", "cron"}, "AUTO_CONFIG_INVALID")
    require(not publish_assets or database is not None and contract is not None, "AUTO_CONFIG_INVALID")
    config = read_config(config_path)
    auto, profile = automation_config(config)
    root = Path(config["inbox"])
    with single_run(root):
        now = clock()
        local = now.astimezone(SHANGHAI)
        if trigger == "cron":
            if not time(17,30) <= local.time().replace(tzinfo=None) < time(18):
                return {"status":"SKIPPED", "reason_code":"AUTO_WINDOW_CLOSED"}
            if not scheduled_day(now):
                return {"status":"SKIPPED", "reason_code":"NON_TRADING_DAY"}
            state = private_json(root/"automation-status.json") if (root/"automation-status.json").exists() else {}
            if state.get("last_success_day") == local.date().isoformat():
                return {"status":"SKIPPED", "reason_code":"ALREADY_PUBLISHED_TODAY"}
        else:
            require(time(9) <= local.time().replace(tzinfo=None) < time(18), "AUTO_WINDOW_CLOSED")
        state_path = root/"automation-status.json"
        state = private_json(state_path) if state_path.exists() else {}
        if retry_batch is None and state.get("status") in {"RUNNING", "FAILED", "VALIDATED"} and state.get("batch"):
            # Restart during publication: recover only our already-sealed same-day
            # source, never mistake a half-written export for an eligible batch.
            old_batch = state["batch"]
            try:
                ready = private_json(root/"ready.json").get("batch") if (root/"ready.json").exists() else None
                require(state.get("status") == "VALIDATED" or state.get("stage") == "READY" or ready == old_batch,
                        "AUTO_EVIDENCE_INVALID")
                _, old_metadata = load_batch(config, candidate=old_batch)
                receipt = private_json(root/old_batch/"observations.json")
                if receipt.get("batch") == old_batch and all(stamp(t).astimezone(SHANGHAI).date() == local.date()
                        for t in old_metadata["queried_at"].values()):
                    retry_batch = old_batch
            except (ValueError, OSError, KeyError, TypeError):
                pass
        batch = retry_batch or "qnx_"+now.strftime("%Y%m%dT%H%M%S")+"_"+uuid.uuid4().hex[:12]
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch), "AUTO_CONFIG_INVALID")
        current = dict(status="RUNNING", started_at=now.isoformat(), trigger=trigger, batch=batch,
            last_success_day=state.get("last_success_day"), last_success_at=state.get("last_success_at"),
            attempt_id=uuid.uuid4().hex)
        atomic_json(state_path, current)
        try:
            directory = root/batch
            if retry_batch:
                _, metadata = load_batch(config, candidate=batch)
                # Retry sealed source from this producer only, never synthesize provenance.
                require(private_json(directory/"observations.json").get("batch") == batch,
                        "AUTO_EVIDENCE_INVALID")
            else:
                directory.mkdir(mode=0o700)
                atomic_json(directory/("run_"+current["attempt_id"]+".json"), current)
                source = exporter(config, auto, profile, batch, now)
                _, metadata = seal_candidate(config, directory, source, now, clock())
            if not publish_assets:
                current.update(status="VALIDATED", finished_at=clock().isoformat(),
                    native_batch_id=metadata["native_batch_id"], security_count=metadata["security_count"],
                    account_total_assets_cny=metadata["account_total_assets_cny"])
                atomic_json(state_path, current)
                atomic_json(directory/("run_"+current["attempt_id"]+".json"), current)
                return current  # no ready update or database access
            advance_ready(config, batch, metadata)
            current["stage"] = "READY"
            atomic_json(state_path, current)
            result = publisher(config_path, database, contract)
            current.update(status="SUCCESS", finished_at=clock().isoformat(),
                last_success_day=local.date().isoformat(), last_success_at=clock().isoformat(),
                native_batch_id=metadata["native_batch_id"], run_id=result.get("run_id"),
                security_count=metadata["security_count"], account_total_assets_cny=metadata["account_total_assets_cny"])
        except Exception as exc:
            current.update(status="FAILED", finished_at=clock().isoformat(),
                reason_code=str(exc) if str(exc) in CODES else "AUTO_EVIDENCE_INVALID")
        atomic_json(state_path, current)
        if directory.is_dir() and directory.resolve() == directory:
            atomic_json(directory/("run_"+current["attempt_id"]+".json"), current)
        return current
