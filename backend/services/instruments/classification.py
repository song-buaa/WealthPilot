"""Single broker-neutral authority for WealthPilot asset classification.

Broker security type describes how an instrument trades.  Vehicle type
describes its legal wrapper.  Economic asset class describes its portfolio
exposure.  These facts are related but never interchangeable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from backend.services.instruments.verified_classifications import (
    find_verified_classification,
)


CLASSIFICATION_VERSION = "canonical-v1"


class VehicleType(StrEnum):
    COMMON_STOCK = "COMMON_STOCK"
    ETF = "ETF"
    BOND = "BOND"
    FUND = "FUND"
    REIT = "REIT"
    ETN = "ETN"
    CASH = "CASH"
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    WARRANT = "WARRANT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class EconomicAssetClass(StrEnum):
    EQUITY = "EQUITY"
    FIXED_INCOME = "FIXED_INCOME"
    CASH = "CASH"
    COMMODITY = "COMMODITY"
    MULTI_ASSET = "MULTI_ASSET"
    ALTERNATIVE = "ALTERNATIVE"
    DERIVATIVE = "DERIVATIVE"
    UNKNOWN = "UNKNOWN"


ECONOMIC_CLASS_TO_CN = {
    EconomicAssetClass.EQUITY: "权益",
    EconomicAssetClass.FIXED_INCOME: "固收",
    EconomicAssetClass.CASH: "货币",
    EconomicAssetClass.COMMODITY: "另类",
    EconomicAssetClass.MULTI_ASSET: "另类",
    EconomicAssetClass.ALTERNATIVE: "另类",
    EconomicAssetClass.DERIVATIVE: "衍生",
    EconomicAssetClass.UNKNOWN: "未分类",
}

CN_TO_ECONOMIC_CLASS = {
    "权益": EconomicAssetClass.EQUITY,
    "固收": EconomicAssetClass.FIXED_INCOME,
    "货币": EconomicAssetClass.CASH,
    "另类": EconomicAssetClass.ALTERNATIVE,
    "衍生": EconomicAssetClass.DERIVATIVE,
    "未分类": EconomicAssetClass.UNKNOWN,
}


@dataclass(frozen=True)
class AssetClassificationEvidence:
    broker: str | None = None
    broker_security_type: str | None = None
    stock_type: str | None = None
    vehicle_type_hint: str | None = None
    explicit_economic_asset_class: str | None = None
    explicit_source: str | None = None
    con_id: int | str | None = None
    isin: str | None = None
    long_name: str | None = None
    category: str | None = None
    subcategory: str | None = None
    industry: str | None = None
    exchange: str | None = None
    primary_exchange: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class AssetClassification:
    broker_security_type: str
    vehicle_type: VehicleType
    economic_asset_class: EconomicAssetClass
    economic_asset_subclass: str | None
    classification_source: str
    classification_confidence: str
    verification_status: str
    classification_version: str = CLASSIFICATION_VERSION

    @property
    def asset_class_cn(self) -> str:
        return ECONOMIC_CLASS_TO_CN[self.economic_asset_class]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["vehicle_type"] = self.vehicle_type.value
        result["economic_asset_class"] = self.economic_asset_class.value
        result["asset_class_cn"] = self.asset_class_cn
        return result


_VEHICLE_ALIASES = {
    "COMMON": VehicleType.COMMON_STOCK,
    "COMMON_STOCK": VehicleType.COMMON_STOCK,
    "EQUITY": VehicleType.COMMON_STOCK,
    "ETF": VehicleType.ETF,
    "BOND": VehicleType.BOND,
    "FUND": VehicleType.FUND,
    "MUTUALFUND": VehicleType.FUND,
    "REIT": VehicleType.REIT,
    "ETN": VehicleType.ETN,
    "CASH": VehicleType.CASH,
    "OPT": VehicleType.OPTION,
    "OPTION": VehicleType.OPTION,
    "FUT": VehicleType.FUTURE,
    "FUTURE": VehicleType.FUTURE,
    "WAR": VehicleType.WARRANT,
    "WARRANT": VehicleType.WARRANT,
    "OTHER": VehicleType.OTHER,
    "UNKNOWN": VehicleType.UNKNOWN,
}

_ECONOMIC_ALIASES = {
    **{item.value: item for item in EconomicAssetClass},
    "FIXED": EconomicAssetClass.FIXED_INCOME,
    "BOND": EconomicAssetClass.FIXED_INCOME,
    "MONETARY": EconomicAssetClass.CASH,
    "ALT": EconomicAssetClass.ALTERNATIVE,
    "DERIV": EconomicAssetClass.DERIVATIVE,
    **CN_TO_ECONOMIC_CLASS,
}

_FIXED_INCOME_HINTS = (
    "BOND", "TREAS", "TREASURY", "TRSY", "TRES", "GILT", "CREDIT",
    "FIXED INCOME", "CORPORATE 1-3", "债券", "国债", "美债", "短债",
    "纯债", "同业存单",
)
_EQUITY_HINTS = (
    "EQUITY", "COMMON STOCK", "S&P 500", "S&P500", "NASDAQ", "MSCI WORLD",
    "STOCK ETF", "股票ETF", "权益基金", "纳指", "标普", "沪深",
)
_COMMODITY_HINTS = ("COMMODITY", "GOLD", "SILVER", "OIL", "黄金", "原油", "大宗商品")
_MULTI_ASSET_HINTS = ("MULTI-ASSET", "MULTI ASSET", "BALANCED", "混合基金")
_REIT_HINTS = ("REIT", "REAL ESTATE INVESTMENT TRUST")


def _normalize_vehicle(value: str | None) -> VehicleType:
    return _VEHICLE_ALIASES.get(str(value or "").strip().upper(), VehicleType.UNKNOWN)


def _normalize_economic(value: str | None) -> EconomicAssetClass | None:
    return _ECONOMIC_ALIASES.get(str(value or "").strip().upper())


def _resolve_vehicle(evidence: AssetClassificationEvidence) -> VehicleType:
    stock_type = _normalize_vehicle(evidence.stock_type)
    if stock_type != VehicleType.UNKNOWN:
        return stock_type

    hinted = _normalize_vehicle(evidence.vehicle_type_hint)
    if hinted != VehicleType.UNKNOWN:
        return hinted

    security_type = str(evidence.broker_security_type or "").strip().upper()
    if security_type == "STK":
        # IBKR uses STK for both common stock and ETF.  Without stockType the
        # wrapper is unknown and must not be converted to equity.
        return VehicleType.UNKNOWN
    return _normalize_vehicle(security_type)


def _metadata_exposure(
    evidence: AssetClassificationEvidence,
) -> tuple[EconomicAssetClass | None, str | None]:
    text = " ".join(
        str(value or "")
        for value in (
            evidence.industry,
            evidence.category,
            evidence.subcategory,
            evidence.long_name,
        )
    ).upper()
    matches: list[tuple[EconomicAssetClass, str | None]] = []
    if any(hint.upper() in text for hint in _FIXED_INCOME_HINTS):
        subclass = "FIXED_INCOME_FUND"
        if any(hint in text for hint in ("TREAS", "TRSY", "TRES", "GILT", "国债", "美债")):
            subclass = "GOVERNMENT_BOND"
        elif "CREDIT" in text or "CORPORATE" in text:
            subclass = "CORPORATE_BOND"
        matches.append((EconomicAssetClass.FIXED_INCOME, subclass))
    if any(hint.upper() in text for hint in _EQUITY_HINTS):
        matches.append((EconomicAssetClass.EQUITY, "EQUITY_FUND"))
    if any(hint.upper() in text for hint in _COMMODITY_HINTS):
        matches.append((EconomicAssetClass.COMMODITY, "COMMODITY_FUND"))
    if any(hint.upper() in text for hint in _MULTI_ASSET_HINTS):
        matches.append((EconomicAssetClass.MULTI_ASSET, "MULTI_ASSET_FUND"))
    if any(hint.upper() in text for hint in _REIT_HINTS):
        matches.append((EconomicAssetClass.ALTERNATIVE, "REIT"))

    distinct = {item[0] for item in matches}
    return matches[0] if len(distinct) == 1 else (None, None)


def classify_instrument(evidence: AssetClassificationEvidence) -> AssetClassification:
    """Resolve canonical vehicle and economic exposure using fixed priority."""
    broker_security_type = str(evidence.broker_security_type or "").strip().upper()
    vehicle = _resolve_vehicle(evidence)

    verified, identity_conflict = find_verified_classification(
        con_id=evidence.con_id,
        isin=evidence.isin,
    )
    if identity_conflict:
        return AssetClassification(
            broker_security_type=broker_security_type,
            vehicle_type=vehicle,
            economic_asset_class=EconomicAssetClass.UNKNOWN,
            economic_asset_subclass=None,
            classification_source="IDENTITY_CONFLICT",
            classification_confidence="LOW",
            verification_status="CONFLICT",
        )
    if verified is not None:
        return AssetClassification(
            broker_security_type=broker_security_type,
            vehicle_type=_normalize_vehicle(verified.vehicle_type),
            economic_asset_class=EconomicAssetClass(verified.economic_asset_class),
            economic_asset_subclass=verified.economic_asset_subclass,
            classification_source=verified.verification_source,
            classification_confidence="HIGH",
            verification_status="VERIFIED",
        )

    explicit = _normalize_economic(evidence.explicit_economic_asset_class)
    if explicit is not None:
        return AssetClassification(
            broker_security_type=broker_security_type,
            vehicle_type=vehicle,
            economic_asset_class=explicit,
            economic_asset_subclass=None,
            classification_source=evidence.explicit_source or "USER_EXPLICIT",
            classification_confidence="HIGH",
            verification_status="EXPLICIT",
        )

    deterministic = {
        VehicleType.COMMON_STOCK: EconomicAssetClass.EQUITY,
        VehicleType.BOND: EconomicAssetClass.FIXED_INCOME,
        VehicleType.CASH: EconomicAssetClass.CASH,
        VehicleType.REIT: EconomicAssetClass.ALTERNATIVE,
        VehicleType.OPTION: EconomicAssetClass.DERIVATIVE,
        VehicleType.FUTURE: EconomicAssetClass.DERIVATIVE,
        VehicleType.WARRANT: EconomicAssetClass.DERIVATIVE,
    }.get(vehicle)
    if deterministic is not None:
        return AssetClassification(
            broker_security_type=broker_security_type,
            vehicle_type=vehicle,
            economic_asset_class=deterministic,
            economic_asset_subclass=(
                "DIRECT_BOND" if vehicle == VehicleType.BOND else None
            ),
            classification_source="BROKER_DETERMINISTIC_METADATA",
            classification_confidence="HIGH",
            verification_status="DETERMINISTIC",
        )

    if vehicle in {VehicleType.ETF, VehicleType.FUND, VehicleType.ETN, VehicleType.UNKNOWN}:
        exposure, subclass = _metadata_exposure(evidence)
        if exposure is not None:
            return AssetClassification(
                broker_security_type=broker_security_type,
                vehicle_type=vehicle,
                economic_asset_class=exposure,
                economic_asset_subclass=subclass,
                classification_source="EXPOSURE_METADATA",
                classification_confidence="MEDIUM",
                verification_status="DETERMINISTIC",
            )

    return AssetClassification(
        broker_security_type=broker_security_type,
        vehicle_type=vehicle,
        economic_asset_class=EconomicAssetClass.UNKNOWN,
        economic_asset_subclass=None,
        classification_source="UNRESOLVED",
        classification_confidence="LOW",
        verification_status="UNVERIFIED",
    )


def business_position_classification_fields(
    classification: AssetClassification,
    *,
    evidence: AssetClassificationEvidence | None = None,
) -> dict[str, Any]:
    """Serialize canonical classification for the business Position model."""
    evidence_payload = asdict(evidence) if evidence is not None else {}
    return {
        "asset_class": classification.asset_class_cn,
        "broker_security_type": classification.broker_security_type or None,
        "vehicle_type": classification.vehicle_type.value,
        "economic_asset_class": classification.economic_asset_class.value,
        "economic_asset_subclass": classification.economic_asset_subclass,
        "classification_source": classification.classification_source,
        "classification_confidence": classification.classification_confidence,
        "classification_verification_status": classification.verification_status,
        "classification_version": classification.classification_version,
        "classification_evidence_json": json.dumps(
            evidence_payload,
            ensure_ascii=False,
            default=str,
        ),
    }


def broker_position_classification_fields(
    classification: AssetClassification,
    *,
    evidence: AssetClassificationEvidence,
) -> dict[str, Any]:
    """Serialize classification into the broker-neutral snapshot contract.

    ``asset_class`` remains a deprecated vehicle alias until all historical
    snapshots have migrated; economic consumers must use
    ``economic_asset_class``.
    """
    legacy_vehicle = {
        VehicleType.COMMON_STOCK: "equity",
        VehicleType.ETF: "etf",
        VehicleType.BOND: "bond",
        VehicleType.FUND: "fund",
        VehicleType.CASH: "cash",
        VehicleType.OPTION: "option",
        VehicleType.FUTURE: "future",
        VehicleType.WARRANT: "warrant",
    }.get(classification.vehicle_type, "unknown")
    return {
        "asset_class": legacy_vehicle,
        "broker_security_type": classification.broker_security_type,
        "vehicle_type": classification.vehicle_type.value,
        "economic_asset_class": classification.economic_asset_class.value,
        "economic_asset_subclass": classification.economic_asset_subclass,
        "classification_source": classification.classification_source,
        "classification_confidence": classification.classification_confidence,
        "classification_verification_status": classification.verification_status,
        "classification_version": classification.classification_version,
        "classification_evidence": asdict(evidence),
    }


def economic_asset_class_cn(position: Any) -> str:
    """Return the canonical economic class label for downstream consumers."""
    canonical = _normalize_economic(getattr(position, "economic_asset_class", None))
    if canonical is not None:
        return ECONOMIC_CLASS_TO_CN[canonical]
    legacy = str(getattr(position, "asset_class", "") or "")
    return legacy if legacy in CN_TO_ECONOMIC_CLASS else "未分类"


def economic_asset_class_value(position: Any) -> str:
    """Return the canonical enum value, including for legacy rows."""
    canonical = _normalize_economic(getattr(position, "economic_asset_class", None))
    if canonical is None:
        canonical = CN_TO_ECONOMIC_CLASS.get(
            str(getattr(position, "asset_class", "") or ""),
            EconomicAssetClass.UNKNOWN,
        )
    return canonical.value
