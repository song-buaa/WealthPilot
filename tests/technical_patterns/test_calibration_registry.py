from __future__ import annotations

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationRegistry,
    DetectorParameterSet,
)


def _key(*, market: str = "US", asset_class: str = "EQUITY", version: str = "us-equity-v1") -> CalibrationKey:
    return CalibrationKey(
        market=market,
        economic_asset_class=asset_class,
        timeframe="1d",
        pattern_family="level_break",
        pattern_type="breakout",
        calibration_version=version,
    )


def test_registry_requires_exact_six_dimension_lookup_and_is_deterministic():
    parameter_set = DetectorParameterSet(
        key=_key(),
        values=(("volume_ratio", 1.5), ("expiry_sessions", 5)),
        minimum_history_bars=50,
    )
    registry = CalibrationRegistry((parameter_set,))

    assert registry.resolve(_key()) == parameter_set
    assert parameter_set.parameter_set_id.startswith("cal_")
    assert parameter_set.values == (("expiry_sessions", 5), ("volume_ratio", 1.5))
    assert DetectorParameterSet(
        key=_key(),
        values=(("expiry_sessions", 5), ("volume_ratio", 1.5)),
        minimum_history_bars=50,
    ).parameters_hash == parameter_set.parameters_hash


@pytest.mark.parametrize(
    ("market", "asset_class", "version"),
    [("LSE", "EQUITY", "us-equity-v1"), ("US", "FIXED_INCOME", "us-equity-v1"), ("US", "EQUITY", "v2")],
)
def test_registry_missing_calibration_fails_closed(market: str, asset_class: str, version: str):
    registry = CalibrationRegistry(
        (DetectorParameterSet(_key(), (("threshold", 1.0),), 20),)
    )

    with pytest.raises(CalibrationNotConfigured, match="no exact detector calibration"):
        registry.resolve(_key(market=market, asset_class=asset_class, version=version))


def test_registry_never_falls_back_from_us_to_btc_or_crypto():
    crypto = DetectorParameterSet(
        _key(market="CRYPTO", asset_class="CRYPTO", version="btc-v1"),
        (("threshold", 2.0),),
        50,
    )
    registry = CalibrationRegistry((crypto,))

    with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
        registry.resolve(_key())


def test_parameter_lookup_has_no_hidden_default():
    parameter_set = DetectorParameterSet(_key(), (("threshold", 1.0),), 20)

    assert parameter_set.require("threshold") == 1.0
    with pytest.raises(CalibrationNotConfigured, match="no explicit parameter"):
        parameter_set.require("unconfigured")
