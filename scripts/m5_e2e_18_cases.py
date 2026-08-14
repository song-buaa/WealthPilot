"""WealthPilot M5: deterministic offline gate and explicit live evaluation.

Default usage (merge gate):
    python scripts/m5_e2e_18_cases.py

Manual live evaluation (never a deterministic merge gate):
    M5_ALLOW_LIVE_PROVIDER=1 WEALTHPILOT_OPENAI_API_KEY=... \
        python scripts/m5_e2e_18_cases.py --mode live

The coordinator launches an isolated child environment with a temporary SQLite
database.  Offline mode installs frozen external fixtures and a public-network
socket guard while the real FastAPI/SSE + PEER + Skills + Reviewer path runs.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "eval_cases" / "cases"
DEFAULT_FIXTURE = ROOT / "eval_cases" / "m5_offline_fixtures.json"
TRACKED_REPORT = ROOT / "docs" / "m5_e2e_report.md"
DEFAULT_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "wealthpilot-m5"
DEFAULT_REPORT = DEFAULT_ARTIFACT_DIR / "m5_e2e_report.md"

_BASE_ENV_KEYS = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE")
_LIVE_PROVIDER_KEYS = (
    "WEALTHPILOT_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "PERPLEXITY_API_KEY",
    "YINGMI_API_KEY",
    "YINGMI_MCP_URL",
    "AV_API_KEY_1",
    "AV_API_KEY_2",
    "AV_API_KEY_3",
    "AV_API_KEY_4",
    "AV_API_KEY_5",
)


class M5GateError(RuntimeError):
    pass


class NetworkGuard:
    """Block non-loopback socket connections and make attempts observable."""

    def __init__(self):
        self.public_attempts: list[str] = []
        self._socket_connect = None
        self._create_connection = None

    @staticmethod
    def is_loopback(host: object) -> bool:
        if not isinstance(host, str):
            return False
        normalized = host.strip("[]").lower()
        if normalized == "localhost":
            return True
        try:
            return ipaddress.ip_address(normalized).is_loopback
        except ValueError:
            return False

    def _check(self, address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if not self.is_loopback(host):
            label = str(host)
            self.public_attempts.append(label)
            raise M5GateError(f"offline network blocked: {label}")

    def __enter__(self):
        self._socket_connect = socket.socket.connect
        self._create_connection = socket.create_connection
        guard = self

        def guarded_connect(sock, address):
            guard._check(address)
            return guard._socket_connect(sock, address)

        def guarded_create_connection(address, *args, **kwargs):
            guard._check(address)
            return guard._create_connection(address, *args, **kwargs)

        socket.socket.connect = guarded_connect
        socket.create_connection = guarded_create_connection
        return self

    def __exit__(self, *_exc):
        socket.socket.connect = self._socket_connect
        socket.create_connection = self._create_connection


def build_child_env(mode: str, db_path: Path, parent_env: dict[str, str] | None = None) -> dict[str, str]:
    """Construct an allowlist environment; never clone the caller environment."""
    source = parent_env if parent_env is not None else os.environ
    env = {key: source[key] for key in _BASE_ENV_KEYS if source.get(key)}
    env.update({
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "M5_MODE": mode,
        "WEALTHPILOT_DB_PATH": str(db_path),
        "PUBLIC_DEMO_MODE": "true",
        "DEMO_ACCESS_PASSWORD": "m5-isolated-demo",
        "DEMO_ALLOW_MARKET_DATA": "false" if mode == "offline" else "true",
        "BROKER_MODE": "mock",
        "IBKR_READ_ONLY_MODE": "true",
        "ENABLE_IBKR_LIVE_TRADING": "false",
        "ENABLE_TIGER_LIVE_TRADING": "false",
        "FUTU_READ_ONLY_MODE": "true",
        "AV_DEV_MOCK": "1" if mode == "offline" else "0",
        "TZ": "UTC",
    })
    if mode == "live":
        for key in _LIVE_PROVIDER_KEYS:
            if source.get(key):
                env[key] = source[key]
    return env


def validate_live_authorization(env: dict[str, str] | None = None) -> None:
    source = env if env is not None else os.environ
    if source.get("M5_ALLOW_LIVE_PROVIDER") != "1":
        raise M5GateError("live mode refused: set M5_ALLOW_LIVE_PROVIDER=1 explicitly")
    if not source.get("WEALTHPILOT_OPENAI_API_KEY"):
        raise M5GateError("live mode refused: WEALTHPILOT_OPENAI_API_KEY is required")


@contextmanager
def isolated_run_dir():
    """Temporary DB/result lifetime, including cleanup on exceptions."""
    with tempfile.TemporaryDirectory(prefix="wealthpilot-m5-") as tmp:
        yield Path(tmp)


def load_cases() -> list[dict[str, Any]]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not case.get("case_id") or not case.get("input", {}).get("user_query"):
            raise M5GateError(f"invalid M5 case contract: {path}")
        cases.append(case)
    if len(cases) != 18:
        raise M5GateError(f"M5 requires exactly 18 case contracts, found {len(cases)}")
    return cases


def collect_sse(response) -> dict[str, Any]:
    collected = {
        "intent": {}, "stages": [], "full_text": "", "candidates": [],
        "done": {}, "validator": {}, "error": {}, "validator_warning": {},
        "has_exception": False,
    }
    current_event = None
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if current_event == "intent":
            collected["intent"] = data
        elif current_event == "stage":
            collected["stages"].append(data.get("stage", ""))
        elif current_event == "text":
            collected["full_text"] += data.get("delta", "")
        elif current_event == "candidates":
            collected["candidates"] = data.get("items", [])
        elif current_event == "done":
            collected["done"] = data
            collected["validator"] = data.get("validator", {})
        elif current_event == "validator_warning":
            collected["validator_warning"] = data
        elif current_event == "error":
            collected["error"] = data
            collected["has_exception"] = True
    return collected


def evaluate_case(case: dict[str, Any], collected: dict[str, Any], fixture_case=None) -> dict[str, Any]:
    from scripts.m3_eval_harness import eval_l1, eval_l2, eval_l3

    expected = case.get("expected", {})
    l1 = eval_l1(expected.get("L1", {}), collected)
    l2 = eval_l2(expected.get("L2", {}), collected)
    l3 = eval_l3(expected.get("L3", {}), collected)

    route = (fixture_case or {}).get("planner", {}).get("route")
    validator_required = route == "portfolio"
    validator_passed = bool(collected["validator"].get("passed")) if collected["validator"] else False
    validation_ok = not validator_required or validator_passed
    completed = bool(collected["done"]) and not collected["has_exception"]
    overall = l1["pass"] and l2["pass"] and l3["pass"] and validation_ok and completed

    return {
        "case_id": case["case_id"],
        "category": case.get("category", ""),
        "question": case["input"]["user_query"],
        "overall_pass": overall,
        "L1": l1,
        "L2": l2,
        "L3": l3,
        "reviewer_validation": {
            "required": validator_required,
            "present": bool(collected["validator"]),
            "passed": validator_passed if collected["validator"] else None,
        },
        "raw": {
            "intent": collected["intent"],
            "stages": collected["stages"],
            "candidates_count": len(collected["candidates"]),
            "done_conclusion": collected["done"].get("conclusion_level", ""),
            "text_length": len(collected["full_text"]),
            "error": collected["error"],
        },
    }


def _install_checkpoint_isolation() -> None:
    """Keep the module-level LangGraph checkpoint database in memory."""
    import sqlite3

    original_connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        if str(database).endswith("checkpoints.db"):
            database = ":memory:"
        return original_connect(database, *args, **kwargs)

    sqlite3.connect = isolated_connect


def run_worker(mode: str, fixture_path: Path, result_path: Path) -> int:
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    _install_checkpoint_isolation()

    guard_context = NetworkGuard() if mode == "offline" else None
    guard = guard_context.__enter__() if guard_context else None
    try:
        fixture_store = None
        provider = None
        cases = load_cases()
        if mode == "offline":
            from scripts.m5_offline_provider import OfflineFixtureStore

            # Validate the complete frozen boundary before importing the app.
            # A missing fixture must fail closed before any provider can exist.
            fixture_store = OfflineFixtureStore(fixture_path)
            yaml_ids = {case["case_id"] for case in cases}
            if fixture_store.case_ids() != yaml_ids:
                missing = sorted(yaml_ids - fixture_store.case_ids())
                extra = sorted(fixture_store.case_ids() - yaml_ids)
                raise M5GateError(f"fixture/case mismatch: missing={missing}, extra={extra}")

        from fastapi.testclient import TestClient
        from backend.main import app

        if mode == "offline":
            from scripts.m5_offline_provider import install_offline_provider

            provider = install_offline_provider(fixture_store)

        results = []
        with TestClient(app) as client:
            for index, case in enumerate(cases, 1):
                query = case["input"]["user_query"]
                conversation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wealthpilot-m5:{case['case_id']}"))
                print(f"[{index:02d}/18] {case['case_id']} {query[:36]}", flush=True)
                try:
                    with client.stream(
                        "POST",
                        "/api/decision/chat",
                        json={
                            "message": query,
                            "conversation_id": conversation_id,
                            "portfolio_id": case["input"].get("portfolio_id", 1),
                        },
                        headers={"Accept": "text/event-stream", "X-Demo-Password": "m5-isolated-demo"},
                    ) as response:
                        if response.status_code != 200:
                            raise M5GateError(f"Decision API returned HTTP {response.status_code}")
                        collected = collect_sse(response)
                except Exception as exc:
                    collected = {
                        "intent": {}, "stages": [], "full_text": "", "candidates": [],
                        "done": {}, "validator": {}, "error": {"message": str(exc)},
                        "validator_warning": {}, "has_exception": True,
                    }
                fixture_case = fixture_store.case_for_query(query) if fixture_store else None
                evaluated = evaluate_case(case, collected, fixture_case)
                results.append(evaluated)
                print("  PASS" if evaluated["overall_pass"] else "  FAIL", flush=True)

        payload = {
            "schema_version": "1.0",
            "mode": mode,
            "frozen_timestamp": fixture_store.frozen_timestamp if fixture_store else None,
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest() if fixture_store else None,
            "provider": "offline_fixture" if provider else "live_provider",
            "provider_calls": [call.__dict__ for call in provider.calls] if provider else [],
            "public_network_attempts": len(guard.public_attempts) if guard else None,
            "results": results,
            "passed": sum(item["overall_pass"] for item in results),
            "failed": sum(not item["overall_pass"] for item in results),
        }
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if payload["failed"] == 0 and (guard is None or not guard.public_attempts) else 1
    finally:
        if guard_context:
            guard_context.__exit__(None, None, None)


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# M5 Offline Deterministic Gate Report",
        "",
        f"- Mode: `{payload['mode']}`",
        f"- Provider: `{payload['provider']}`",
        f"- Result: `{payload['passed']}/18 passed, {payload['failed']} failed`",
        f"- Public network attempts: `{payload['public_network_attempts']}`",
        f"- Frozen timestamp: `{payload.get('frozen_timestamp') or 'live'}`",
        f"- Fixture SHA-256: `{payload.get('fixture_sha256') or 'N/A'}`",
        "",
        "| Case | Category | L1 | L2 | L3 | Reviewer | Result |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in payload["results"]:
        reviewer = item["reviewer_validation"]
        reviewer_mark = "PASS" if (not reviewer["required"] or reviewer["passed"]) else "FAIL"
        lines.append(
            f"| {item['case_id']} | {item['category']} | "
            f"{'PASS' if item['L1']['pass'] else 'FAIL'} | "
            f"{'PASS' if item['L2']['pass'] else 'FAIL'} | "
            f"{'PASS' if item['L3']['pass'] else 'FAIL'} | {reviewer_mark} | "
            f"{'PASS' if item['overall_pass'] else 'FAIL'} |"
        )
    lines.append("")
    return "\n".join(lines)


def select_report_path(update_report: bool) -> Path:
    """Tracked history is writable only behind an explicit CLI flag."""
    return TRACKED_REPORT if update_report else DEFAULT_REPORT


def run_coordinator(mode: str, fixture_path: Path, update_report: bool) -> int:
    if mode == "live":
        validate_live_authorization()
    with isolated_run_dir() as run_dir:
        db_path = run_dir / "m5_eval.db"
        result_path = run_dir / "result.json"
        child_env = build_child_env(mode, db_path)
        command = [
            sys.executable, str(Path(__file__).resolve()), "--worker", "--mode", mode,
            "--fixture", str(fixture_path), "--result-path", str(result_path),
        ]
        completed = subprocess.run(
            command, cwd=ROOT, env=child_env, text=True, timeout=1200,
        )
        if not result_path.is_file():
            raise M5GateError(f"M5 worker failed before producing results (exit={completed.returncode})")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        report_path = select_report_path(update_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(payload), encoding="utf-8")
        print(f"M5 {mode}: {payload['passed']}/18 passed, {payload['failed']} failed")
        print(f"provider={payload['provider']} public_network_attempts={payload['public_network_attempts']}")
        print(f"report={report_path}")
        return completed.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WealthPilot M5 18-case gate")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--update-report", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--result-path", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.worker:
            if not args.result_path:
                raise M5GateError("worker requires --result-path")
            return run_worker(args.mode, args.fixture.resolve(), args.result_path.resolve())
        return run_coordinator(args.mode, args.fixture.resolve(), args.update_report)
    except (M5GateError, subprocess.TimeoutExpired) as exc:
        print(f"M5 refused/failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
