from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.m5_e2e_18_cases import (
    DEFAULT_FIXTURE,
    ROOT,
    TRACKED_REPORT,
    M5GateError,
    NetworkGuard,
    build_child_env,
    isolated_run_dir,
    parse_args,
    select_report_path,
    validate_live_authorization,
)
from scripts.m5_offline_provider import (
    OfflineFixtureError,
    OfflineFixtureStore,
    OfflineOpenAIProvider,
)


FAKE_SECRET = "fake-but-detectable-secret"


def test_default_mode_is_offline():
    assert parse_args([]).mode == "offline"


def test_offline_environment_is_allowlisted_and_ignores_inherited_secrets(tmp_path):
    inherited = {
        "PATH": "/usr/bin",
        "WEALTHPILOT_OPENAI_API_KEY": FAKE_SECRET,
        "OPENAI_API_KEY": FAKE_SECRET,
        "ALPHA_VANTAGE_API_KEY": FAKE_SECRET,
        "AV_API_KEY_4": FAKE_SECRET,
        "PERPLEXITY_API_KEY": FAKE_SECRET,
        "YINGMI_API_KEY": FAKE_SECRET,
        "TIGER_PRIVATE_KEY": FAKE_SECRET,
        "FUTU_OPEND_HOST": "public.example",
        "IBKR_ACCOUNT": FAKE_SECRET,
        "SNOWBALL_TOKEN": FAKE_SECRET,
        "GUOJIN_GATEWAY_SECRET": FAKE_SECRET,
    }
    child = build_child_env("offline", tmp_path / "m5.db", inherited)

    assert FAKE_SECRET not in json.dumps(child)
    assert not any(key in child for key in inherited if key not in {"PATH"})
    assert child["PUBLIC_DEMO_MODE"] == "true"
    assert child["DEMO_ALLOW_MARKET_DATA"] == "false"
    assert child["BROKER_MODE"] == "mock"
    assert child["IBKR_READ_ONLY_MODE"] == "true"
    assert child["ENABLE_IBKR_LIVE_TRADING"] == "false"


def test_offline_network_guard_blocks_public_without_dns():
    guard = NetworkGuard()
    with pytest.raises(M5GateError, match="offline network blocked"):
        guard._check(("api.openai.com", 443))
    assert guard.public_attempts == ["api.openai.com"]
    assert NetworkGuard.is_loopback("127.0.0.1")
    assert NetworkGuard.is_loopback("::1")
    assert NetworkGuard.is_loopback("localhost")
    assert not NetworkGuard.is_loopback("api.openai.com")


def test_offline_provider_is_deterministic_and_selected():
    fixtures = OfflineFixtureStore(DEFAULT_FIXTURE)
    provider = OfflineOpenAIProvider(fixtures)
    query = "稳健型投资者应该怎么理解股债的仓位比例？"
    messages = [
        {"role": "system", "content": "你是一个投资意图识别系统。"},
        {"role": "user", "content": query},
    ]

    first = provider.create(messages=messages).choices[0].message.content
    second = provider.create(messages=messages).choices[0].message.content

    assert first == second
    assert json.loads(first)["primary_intent"] == "Education"
    assert [call.stage for call in provider.calls] == ["intent", "intent"]


def test_offline_missing_fixture_fails_closed(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(OfflineFixtureError, match="fixture missing"):
        OfflineFixtureStore(missing)


def test_offline_unknown_query_never_falls_back_live():
    provider = OfflineOpenAIProvider(OfflineFixtureStore(DEFAULT_FIXTURE))
    with pytest.raises(OfflineFixtureError, match="matched 0 cases"):
        provider.create(messages=[{"role": "user", "content": "fixture 中不存在的问题"}])


def test_live_requires_second_explicit_authorization():
    with pytest.raises(M5GateError, match="M5_ALLOW_LIVE_PROVIDER=1"):
        validate_live_authorization({"WEALTHPILOT_OPENAI_API_KEY": FAKE_SECRET})


def test_live_requires_explicit_openai_key():
    with pytest.raises(M5GateError, match="WEALTHPILOT_OPENAI_API_KEY"):
        validate_live_authorization({"M5_ALLOW_LIVE_PROVIDER": "1"})


def test_default_report_is_outside_repo_and_tracked_report_is_explicit():
    default_path = select_report_path(False)
    assert not default_path.is_relative_to(ROOT)
    assert default_path != TRACKED_REPORT
    assert select_report_path(True) == TRACKED_REPORT


def test_isolated_run_dir_cleanup_after_success():
    with isolated_run_dir() as run_dir:
        marker = run_dir / "m5.db"
        marker.write_text("temporary", encoding="utf-8")
        saved_dir = run_dir
    assert not saved_dir.exists()


def test_isolated_run_dir_cleanup_after_failure():
    saved_dir: Path | None = None
    with pytest.raises(RuntimeError, match="forced failure"):
        with isolated_run_dir() as run_dir:
            saved_dir = run_dir
            (run_dir / "m5.db").write_text("temporary", encoding="utf-8")
            raise RuntimeError("forced failure")
    assert saved_dir is not None
    assert not saved_dir.exists()
