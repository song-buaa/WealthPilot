"""Canonical asset-classification contract and release regression matrix."""
from types import SimpleNamespace

import pytest

from backend.services.instruments.classification import (
    AssetClassificationEvidence,
    EconomicAssetClass,
    VehicleType,
    classify_instrument,
    economic_asset_class_cn,
)
from app.allocation.calculator import build_allocation_snapshot
from app.csv_importer import parse_positions_csv
from app.bank_screenshot import bank_positions_to_db
from app.platform_importers import _classification_fields as platform_classification_fields


@pytest.mark.parametrize(
    ("con_id", "isin", "subclass"),
    [
        (79000224, "IE00B3VWN179", "SHORT_TERM_TREASURY"),
        (354802220, "IE00BGSF1X88", "SHORT_TERM_TREASURY"),
        (79000139, "IE00B3VWN518", "GOVERNMENT_BOND"),
        (354532794, "IE00BGYWSV06", "SHORT_TERM_CORPORATE_BOND"),
    ],
)
def test_verified_fixed_income_etfs(con_id, isin, subclass):
    result = classify_instrument(AssetClassificationEvidence(
        broker="ibkr",
        broker_security_type="STK",
        stock_type="ETF",
        con_id=con_id,
        isin=isin,
    ))
    assert result.vehicle_type is VehicleType.ETF
    assert result.economic_asset_class is EconomicAssetClass.FIXED_INCOME
    assert result.economic_asset_subclass == subclass
    assert result.verification_status == "VERIFIED"


def test_stk_is_not_an_economic_asset_class():
    result = classify_instrument(AssetClassificationEvidence(
        broker="ibkr", broker_security_type="STK"
    ))
    assert result.vehicle_type is VehicleType.UNKNOWN
    assert result.economic_asset_class is EconomicAssetClass.UNKNOWN


def test_unknown_etf_fails_closed():
    result = classify_instrument(AssetClassificationEvidence(
        broker_security_type="STK", stock_type="ETF", long_name="Example ETF"
    ))
    assert result.vehicle_type is VehicleType.ETF
    assert result.economic_asset_class is EconomicAssetClass.UNKNOWN


def test_common_stock_is_equity():
    result = classify_instrument(AssetClassificationEvidence(
        broker_security_type="STK", stock_type="COMMON", long_name="APPLE INC"
    ))
    assert result.vehicle_type is VehicleType.COMMON_STOCK
    assert result.economic_asset_class is EconomicAssetClass.EQUITY


def test_verified_equity_etf_is_equity():
    result = classify_instrument(AssetClassificationEvidence(
        broker_security_type="STK", stock_type="ETF",
        con_id=756733, isin="US78462F1030",
    ))
    assert result.vehicle_type is VehicleType.ETF
    assert result.economic_asset_class is EconomicAssetClass.EQUITY


@pytest.mark.parametrize(
    ("security_type", "vehicle", "economic"),
    [
        ("BOND", VehicleType.BOND, EconomicAssetClass.FIXED_INCOME),
        ("CASH", VehicleType.CASH, EconomicAssetClass.CASH),
    ],
)
def test_deterministic_direct_instruments(security_type, vehicle, economic):
    result = classify_instrument(AssetClassificationEvidence(
        broker_security_type=security_type
    ))
    assert result.vehicle_type is vehicle
    assert result.economic_asset_class is economic


def test_same_verified_identity_is_broker_neutral():
    results = {
        classify_instrument(AssetClassificationEvidence(
            broker=broker,
            broker_security_type=sec_type,
            stock_type=stock_type,
            isin="IE00B3VWN179",
        )).economic_asset_class
        for broker, sec_type, stock_type in (
            ("ibkr", "STK", "ETF"),
            ("futu", "ETF", "ETF"),
            ("csv", "FUND", "FUND"),
        )
    }
    assert results == {EconomicAssetClass.FIXED_INCOME}


def test_downstream_prefers_canonical_economic_class_over_legacy_value():
    position = SimpleNamespace(
        asset_class="权益",
        economic_asset_class="FIXED_INCOME",
    )
    assert economic_asset_class_cn(position) == "固收"


def test_allocation_aggregate_moves_case1_etfs_out_of_equity():
    positions = [
        SimpleNamespace(
            name=name,
            asset_class="权益",  # stale compatibility value must lose.
            economic_asset_class="FIXED_INCOME",
            market_value_cny=value,
            segment="投资",
        )
        for name, value in (("CBU3", 100.0), ("IB01", 50.0))
    ]
    snapshot = build_allocation_snapshot(positions)
    assert snapshot.by_class["fixed"].amount == 150.0
    assert snapshot.by_class["equity"].amount == 0.0


def test_conflicting_stable_ids_fail_closed():
    result = classify_instrument(AssetClassificationEvidence(
        broker_security_type="STK",
        stock_type="ETF",
        con_id=79000224,
        isin="US78462F1030",
    ))
    assert result.economic_asset_class is EconomicAssetClass.UNKNOWN
    assert result.verification_status == "CONFLICT"


def test_generic_csv_user_classification_keeps_provenance():
    csv = (
        "平台,资产名称,代码,大类,头寸,市值（美元）,市值（港币）,市值(人民币),"
        "盈亏(原始货币),盈亏(元),盈亏%,segment\n"
        "手工账户,示例资产,EX,固收,1,,,100,0,0,0,投资\n"
    )
    positions, errors = parse_positions_csv(csv)
    assert errors == []
    assert positions[0]["economic_asset_class"] == "FIXED_INCOME"
    assert positions[0]["asset_class"] == "固收"
    assert positions[0]["classification_source"] == "USER_EXPLICIT_CSV"


def test_broker_csv_without_vehicle_metadata_fails_closed():
    fields = platform_classification_fields(
        broker="futu_csv",
        subsection="股票",
        raw_name="Example Security",
        currency="USD",
    )
    assert fields["vehicle_type"] == "UNKNOWN"
    assert fields["economic_asset_class"] == "UNKNOWN"
    assert fields["asset_class"] == "未分类"


def test_bank_fund_category_is_vehicle_not_equity_exposure():
    [position] = bank_positions_to_db({"基金": 100.0}, "建设银行")
    assert position["vehicle_type"] == "FUND"
    assert position["economic_asset_class"] == "UNKNOWN"
    assert position["asset_class"] == "未分类"
