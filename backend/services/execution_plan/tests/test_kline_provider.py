"""v3.14 K 线 provider 的离线降级契约。"""
from unittest.mock import Mock, patch

import pandas as pd

from backend.services.execution_plan.factors import build_factor_snapshot
from backend.services.execution_plan.kline_provider import (
    AVKlineProvider,
    KlineProvider,
    KlineProviderRegistry,
    KlineResult,
    SeedKlineProvider,
    build_kline_registry,
)


def _bars() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2025-01-02"], "open": [100.0], "high": [101.0],
        "low": [99.0], "close": [100.5], "volume": [1000],
    })


class _UnavailableProvider(KlineProvider):
    name = "broker"

    def get_kline(self, *args, **kwargs):
        self._unavailable("Demo 未注册券商连接")
        return None


class _DelayedProvider(KlineProvider):
    name = "av"

    def get_kline(self, *args, **kwargs):
        return KlineResult(_bars(), self.name, delayed_minutes=15)


def test_demo_market_gate_short_circuits_before_urlopen(monkeypatch):
    """Demo 禁用行情时，AV 不得发生任何网络调用。"""
    monkeypatch.setattr(
        "backend.services.execution_plan.kline_provider._get_demo_config",
        lambda: (True, False),
    )
    urlopen = Mock(side_effect=AssertionError("不应访问网络"))
    with patch("urllib.request.urlopen", urlopen):
        result = AVKlineProvider().get_kline("AAPL:US", "US")

    assert result is None
    assert urlopen.call_count == 0


def test_registry_preserves_provider_failure_reason_and_final_source():
    registry = KlineProviderRegistry([_UnavailableProvider(), _DelayedProvider()])

    result, degraded = registry.resolve("AAPL:US", "US")

    assert result is not None
    assert result.source == "av"
    assert result.delayed_minutes == 15
    assert degraded == ["broker"]
    assert registry.last_degraded_reasons == ["broker 不可用：Demo 未注册券商连接"]


def test_factor_meta_carries_fallback_source_delay_and_reason():
    registry = KlineProviderRegistry([_UnavailableProvider(), _DelayedProvider()])

    snapshot = build_factor_snapshot("AAPL:US", "US", kline_registry=registry)

    meta = snapshot.data_source_meta
    assert meta["kline_source"] == "av"
    assert meta["delayed_minutes"] == 15
    assert "kline_provider:broker" in meta["degraded_fields"]
    assert "Demo 未注册券商连接" in meta["degraded_reason"]


def test_seed_fixture_is_static_and_supports_52_week_calculation():
    result = SeedKlineProvider().get_kline("AAPL:US", "US")

    assert result is not None
    assert result.source == "seed"
    assert len(result.bars) == 260
    assert result.latest_price_time == "2025-12-26"
    assert float(result.bars.iloc[-1]["close"]) == 242.35


def test_registry_only_registers_seed_for_demo_or_explicit_fixture(monkeypatch):
    monkeypatch.setattr(
        "backend.services.execution_plan.kline_provider._get_demo_config",
        lambda: (False, True),
    )
    assert [p.name for p in build_kline_registry()._providers] == ["broker", "av"]
    assert [p.name for p in build_kline_registry(include_seed=True)._providers] == ["broker", "av", "seed"]

    monkeypatch.setattr(
        "backend.services.execution_plan.kline_provider._get_demo_config",
        lambda: (True, False),
    )
    assert [p.name for p in build_kline_registry()._providers] == ["av", "seed"]
