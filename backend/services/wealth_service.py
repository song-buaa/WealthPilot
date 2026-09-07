"""Wealth overview v0.1 aggregation and manual-record service.

The investment domain remains authoritative: this module obtains investment
values exclusively through ``portfolio_service.get_summary``.  It owns only
non-investment manual assets/liabilities and their confirmation snapshots.
"""
from __future__ import annotations

from datetime import date, datetime, time
import re
from typing import Any

from sqlalchemy import inspect

from app.fx_service import fx_service
from app.models import WealthItem, WealthItemSnapshot, WealthSnapshot, get_session
from backend.services import portfolio_service


ASSET_TYPES: dict[str, tuple[str, bool]] = {
    "bank_cash": ("cash_deposits", True),
    "time_deposit": ("cash_deposits", True),
    "housing_fund": ("retirement_long_term", True),
    # 企业缴费及收益是否已完全归属个人取决于原单位方案；在明确已归属金额前，仅展示。
    "enterprise_annuity": ("retirement_long_term", False),
    # These retirement products have a material pre-retirement withdrawal
    # restriction, so the core balance sheet treats them as display-only.
    "personal_pension": ("retirement_long_term", False),
    "pension_insurance": ("retirement_long_term", False),
    "basic_pension": ("retirement_long_term", False),
    "other_asset": ("other_assets", True),
}
PENSION_SECURITY_ITEM_TYPES = frozenset({
    "enterprise_annuity", "personal_pension", "pension_insurance", "basic_pension",
})
LIABILITY_TYPES: dict[str, tuple[str, bool]] = {
    "credit_card": ("credit_card", True),
    "consumer_loan": ("consumer_loan", True),
    "mortgage": ("mortgage", True),
    "other_liability": ("other_liability", True),
}


def _now() -> datetime:
    return datetime.now()


def _item_to_dict(item: WealthItem) -> dict[str, Any]:
    today = date.today()
    age_days = max((today - item.updated_at.date()).days, 0)
    freshness = "latest" if age_days <= 30 else "suggested_update" if age_days <= 90 else "long_unupdated"
    effective_included = bool(item.included_in_net_worth)
    return {
        "id": item.id,
        "kind": item.kind.lower(),
        "name": item.name,
        "item_type": item.item_type,
        "category": item.category,
        "source_type": item.source_type,
        "sync_mode": item.sync_mode,
        "current_value": round(float(item.current_value or 0), 2),
        "currency": item.currency,
        "original_value": round(float(item.original_value if item.original_value is not None else item.current_value or 0), 2),
        "fx_rate_to_cny": round(float(item.fx_rate_to_cny or 1), 8),
        "fx_rate_date": item.fx_rate_date,
        "included_in_net_worth": bool(item.included_in_net_worth),
        "already_investment_accounted": bool(item.already_investment_accounted),
        "effective_included_in_net_worth": effective_included,
        "value_as_of": item.value_as_of.isoformat(),
        "last_verified_at": item.last_verified_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "age_days": age_days,
        "freshness": freshness,
        "notes": item.notes,
    }


def list_items(portfolio_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    session = get_session()
    try:
        query = session.query(WealthItem).filter_by(portfolio_id=portfolio_id)
        if kind:
            query = query.filter_by(kind=kind.upper())
        return [_item_to_dict(item) for item in query.order_by(WealthItem.kind, WealthItem.category, WealthItem.name)]
    finally:
        session.close()


def _type_metadata(kind: str, item_type: str) -> tuple[str, bool]:
    values = ASSET_TYPES if kind == "ASSET" else LIABILITY_TYPES
    try:
        return values[item_type]
    except KeyError as exc:
        raise ValueError("不支持的财富项目类型") from exc


def _validate_payload(payload: dict[str, Any]) -> tuple[str, str, str, bool]:
    kind = str(payload.get("kind", "")).upper()
    if kind not in {"ASSET", "LIABILITY"}:
        raise ValueError("kind 必须是 asset 或 liability")
    item_type = str(payload.get("item_type", ""))
    category, default_included = _type_metadata(kind, item_type)
    if float(payload.get("current_value", 0)) < 0:
        raise ValueError("金额不能小于 0")
    if not str(payload.get("name", "")).strip():
        raise ValueError("请填写名称")
    return kind, item_type, category, default_included


def _base_currency_value(amount: float, currency: str) -> tuple[float, float, str]:
    """Convert an asset fact to CNY through the shared FX service."""
    normalized_currency = (currency or "CNY").upper()
    if normalized_currency == "CNY":
        return amount, 1.0, "latest"
    converted, rate, rate_date = fx_service.convert(amount, normalized_currency, "CNY")
    return converted, rate, rate_date


def _currency_payload_values(payload: dict[str, Any], existing: WealthItem | None = None) -> tuple[float, str, float, float, str]:
    """Return CNY value plus preserved source-currency facts.

    Legacy callers only provide ``current_value`` and therefore retain their
    CNY behaviour. New callers may provide a source ``currency`` and
    ``original_value``; the CNY aggregation value is then derived here.
    """
    currency = str(payload.get("currency", existing.currency if existing else "CNY") or "CNY").upper()
    if "original_value" in payload:
        original_value = float(payload["original_value"])
    elif existing and currency == existing.currency and "current_value" not in payload:
        original_value = float(existing.original_value if existing.original_value is not None else existing.current_value)
    else:
        original_value = float(payload.get("current_value", existing.current_value if existing else 0))
    if original_value < 0:
        raise ValueError("金额不能小于 0")
    cny_value, fx_rate, fx_rate_date = _base_currency_value(original_value, currency)
    return cny_value, currency, original_value, fx_rate, fx_rate_date


def create_item(portfolio_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    kind, item_type, category, default_included = _validate_payload(payload)
    as_of = payload.get("value_as_of") or date.today()
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    cny_value, currency, original_value, fx_rate, fx_rate_date = _currency_payload_values(payload)
    now = _now()
    item = WealthItem(
        portfolio_id=portfolio_id,
        kind=kind,
        name=str(payload["name"]).strip(),
        item_type=item_type,
        category=category,
        source_type=str(payload.get("source_type") or "MANUAL").upper(),
        sync_mode="MANUAL",
        current_value=cny_value,
        currency=currency,
        original_value=original_value,
        fx_rate_to_cny=fx_rate,
        fx_rate_date=fx_rate_date,
        included_in_net_worth=bool(payload.get("included_in_net_worth", default_included)),
        already_investment_accounted=bool(payload.get("already_investment_accounted", False)),
        value_as_of=as_of,
        last_verified_at=now,
        notes=payload.get("notes") or None,
    )
    session = get_session()
    try:
        session.add(item)
        session.flush()
        session.add(WealthItemSnapshot(item_id=item.id, value=item.current_value, value_as_of=item.value_as_of))
        session.commit()
        item_id = item.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    _record_aggregate_snapshot(portfolio_id)
    return get_item(portfolio_id, item_id)


def get_item(portfolio_id: int, item_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        item = session.query(WealthItem).filter_by(id=item_id, portfolio_id=portfolio_id).first()
        if not item:
            raise LookupError("财富项目不存在")
        return _item_to_dict(item)
    finally:
        session.close()


def update_item(portfolio_id: int, item_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    session = get_session()
    try:
        item = session.query(WealthItem).filter_by(id=item_id, portfolio_id=portfolio_id).first()
        if not item:
            raise LookupError("财富项目不存在")
        merged = {key: getattr(item, key) for key in ("kind", "name", "item_type", "current_value")}
        merged.update(payload)
        kind, item_type, category, _ = _validate_payload(merged)
        as_of = payload.get("value_as_of", item.value_as_of)
        if isinstance(as_of, str):
            as_of = date.fromisoformat(as_of)
        item.kind, item.item_type, item.category = kind, item_type, category
        item.name = str(merged["name"]).strip()
        cny_value, currency, original_value, fx_rate, fx_rate_date = _currency_payload_values(payload, item)
        item.current_value = cny_value
        item.currency = currency
        item.original_value = original_value
        item.fx_rate_to_cny = fx_rate
        item.fx_rate_date = fx_rate_date
        item.value_as_of = as_of
        item.included_in_net_worth = bool(payload.get("included_in_net_worth", item.included_in_net_worth))
        item.already_investment_accounted = bool(payload.get("already_investment_accounted", item.already_investment_accounted))
        item.source_type = str(payload.get("source_type", item.source_type)).upper()
        item.notes = payload.get("notes", item.notes) or None
        item.last_verified_at = _now()
        session.add(WealthItemSnapshot(item_id=item.id, value=item.current_value, value_as_of=item.value_as_of))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    _record_aggregate_snapshot(portfolio_id)
    return get_item(portfolio_id, item_id)


def delete_item(portfolio_id: int, item_id: int) -> None:
    session = get_session()
    try:
        item = session.query(WealthItem).filter_by(id=item_id, portfolio_id=portfolio_id).first()
        if not item:
            raise LookupError("财富项目不存在")
        # SQLite does not necessarily enforce ON DELETE CASCADE in older local DBs.
        session.query(WealthItemSnapshot).filter_by(item_id=item.id).delete()
        session.delete(item)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    _record_aggregate_snapshot(portfolio_id)


def _investment_summary(portfolio_id: int) -> dict[str, Any]:
    summary = portfolio_service.get_summary(portfolio_id)
    if "error" in summary:
        return {"total_assets": 0.0, "total_profit_loss": None, "allocation": {}}
    return summary


def _manual_totals(portfolio_id: int) -> dict[str, float]:
    session = get_session()
    try:
        items = session.query(WealthItem).filter_by(portfolio_id=portfolio_id).all()
        non_investment_assets = 0.0
        pension_benefit_value = 0.0
        reclassified_investment_assets = 0.0
        investment_linked_assets = 0.0
        liabilities = 0.0
        by_category: dict[str, float] = {}
        liability_categories: dict[str, float] = {}
        for item in items:
            value = float(item.current_value or 0)
            effective = item.included_in_net_worth and not item.already_investment_accounted
            if item.kind == "ASSET":
                if item.already_investment_accounted:
                    # The investment module remains the source of the value.
                    # Remove its amount from ordinary investment assets before
                    # either reclassifying it as a core manual asset or showing
                    # it as a non-core retirement security benefit.
                    investment_linked_assets += value
                if item.item_type in PENSION_SECURITY_ITEM_TYPES and not item.included_in_net_worth:
                    pension_benefit_value += value
                if item.already_investment_accounted and item.included_in_net_worth:
                    # This is a classification link to an investment-account
                    # fact, not a second asset. Shift it out of the ordinary
                    # investment bucket while keeping the global total intact.
                    reclassified_investment_assets += value
                    by_category[item.category] = by_category.get(item.category, 0.0) + value
                    continue
                if effective:
                    non_investment_assets += value
                    by_category[item.category] = by_category.get(item.category, 0.0) + value
            elif effective:
                liabilities += value
                liability_categories[item.category] = liability_categories.get(item.category, 0.0) + value
        return {
            "non_investment_assets": non_investment_assets,
            "pension_benefit_value": pension_benefit_value,
            "reclassified_investment_assets": reclassified_investment_assets,
            "investment_linked_assets": investment_linked_assets,
            "liabilities": liabilities,
            "asset_categories": by_category,
            "liability_categories": liability_categories,
        }
    finally:
        session.close()


def _current_totals(portfolio_id: int) -> dict[str, Any]:
    investment = _investment_summary(portfolio_id)
    manual = _manual_totals(portfolio_id)
    gross_investment_assets = float(investment.get("total_assets") or 0)
    reclassified = manual["reclassified_investment_assets"]
    investment_linked = manual["investment_linked_assets"]
    if investment_linked > gross_investment_assets:
        raise ValueError("投资账户重分类金额不能超过投资资产总额")
    investment_assets = gross_investment_assets - investment_linked
    # Reclassified values are already present in the investment source of
    # truth; add them back only under their retirement classification.
    total_assets = investment_assets + manual["non_investment_assets"] + reclassified
    total_liabilities = manual["liabilities"]
    return {
        "investment": investment,
        "investment_assets": investment_assets,
        "investment_profit_loss": investment.get("total_profit_loss"),
        "gross_investment_assets": gross_investment_assets,
        "reclassified_investment_assets": reclassified,
        "investment_linked_assets": investment_linked,
        "non_investment_assets": manual["non_investment_assets"],
        "pension_benefit_value": manual["pension_benefit_value"],
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net_worth": total_assets - total_liabilities,
        "asset_categories": manual["asset_categories"],
        "liability_categories": manual["liability_categories"],
    }


def _record_aggregate_snapshot(portfolio_id: int, totals: dict[str, Any] | None = None) -> None:
    totals = _current_totals(portfolio_id) if totals is None else totals
    session = get_session()
    try:
        # Append current observations without rewriting historical scope. Repeated
        # reads of unchanged values on the same day must not create duplicates.
        latest = session.query(WealthSnapshot).filter_by(portfolio_id=portfolio_id).order_by(
            WealthSnapshot.recorded_at.desc(), WealthSnapshot.id.desc()
        ).first()
        fields = ("total_assets", "total_liabilities", "net_worth", "investment_assets",
                  "investment_profit_loss", "non_investment_assets", "pension_benefit_value")
        if latest and latest.recorded_at.date() == date.today() and all(
            getattr(latest, field) == totals[field] for field in fields
        ):
            return
        session.add(WealthSnapshot(
            portfolio_id=portfolio_id,
            total_assets=totals["total_assets"],
            total_liabilities=totals["total_liabilities"],
            net_worth=totals["net_worth"],
            investment_assets=totals["investment_assets"],
            investment_profit_loss=totals["investment_profit_loss"],
            non_investment_assets=totals["non_investment_assets"],
            pension_benefit_value=totals["pension_benefit_value"],
        ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cashflow_for_month(portfolio_id: int, now: date) -> dict[str, Any]:
    """Reuse confirmed consumption economic events without treating absent imports as zero."""
    # The consumption domain is optional in v0.1 and tables can be absent in an old local DB.
    session = get_session()
    try:
        if "consumption_economic_events" not in inspect(session.bind).get_table_names():
            return {"income": None, "expense": None, "surplus": None, "available": False}
        from backend.services.consumption.models import EconomicEvent
        from backend.services.consumption.economic_events import EventType

        start = date(now.year, now.month, 1)
        events = session.query(EconomicEvent).filter(
            EconomicEvent.is_active.is_(True),
            EconomicEvent.analytics_effective_date >= start,
            EconomicEvent.analytics_effective_date <= now,
            EconomicEvent.base_amount.isnot(None),
        ).all()
        if not events:
            return {"income": None, "expense": None, "surplus": None, "available": False}
        income = sum(float(item.base_amount) for item in events if item.event_type == EventType.INCOME.value)
        expense = sum(float(item.base_amount) for item in events if item.event_type == EventType.CONSUMPTION.value)
        return {"income": income, "expense": expense, "surplus": income - expense, "available": True}
    finally:
        session.close()


def _month_start_snapshot(portfolio_id: int, today: date) -> WealthSnapshot | None:
    start = datetime.combine(date(today.year, today.month, 1), time.min)
    session = get_session()
    try:
        return (session.query(WealthSnapshot)
                .filter(WealthSnapshot.portfolio_id == portfolio_id, WealthSnapshot.recorded_at < start)
                .order_by(WealthSnapshot.recorded_at.desc()).first())
    finally:
        session.close()


def _trend(portfolio_id: int, totals: dict[str, Any], days: int | None) -> list[dict[str, Any]]:
    session = get_session()
    try:
        query = session.query(WealthSnapshot).filter_by(portfolio_id=portfolio_id)
        if days:
            since = datetime.combine(date.today().fromordinal(date.today().toordinal() - days), time.min)
            query = query.filter(WealthSnapshot.recorded_at >= since)
        points = query.order_by(WealthSnapshot.recorded_at).all()
        # Multiple confirmations in one day are represented by their latest state.
        by_day: dict[str, WealthSnapshot] = {}
        for point in points:
            by_day[point.recorded_at.date().isoformat()] = point
        result = [{"date": day, "net_worth": round(value.net_worth, 2)} for day, value in by_day.items()]
        today_key = date.today().isoformat()
        if result and result[-1]["date"] == today_key:
            # Preserve the stored snapshot for audit history while rendering
            # today's current calculation under the latest accounting scope.
            result[-1]["net_worth"] = round(totals["net_worth"], 2)
        else:
            result.append({"date": today_key, "net_worth": round(totals["net_worth"], 2)})
        return result
    finally:
        session.close()


def get_summary(portfolio_id: int, trend_days: int | None = None) -> dict[str, Any]:
    totals = _current_totals(portfolio_id)
    _record_aggregate_snapshot(portfolio_id, totals)
    today = date.today()
    baseline = _month_start_snapshot(portfolio_id, today)
    monthly_change = round(totals["net_worth"] - baseline.net_worth, 2) if baseline else None
    cashflow = _cashflow_for_month(portfolio_id, today)
    investment_return = None
    if baseline and baseline.investment_profit_loss is not None and totals["investment_profit_loss"] is not None:
        investment_return = round(float(totals["investment_profit_loss"]) - float(baseline.investment_profit_loss), 2)
    known = (cashflow["surplus"] or 0) + (investment_return or 0)
    other_adjustment = round(monthly_change - known, 2) if monthly_change is not None else None
    investment = totals["investment"]
    assets = [
        {"category": "investment", "label": "投资资产", "value": totals["investment_assets"], "source": "investment_account"},
        *[
            {"category": category, "label": _category_label(category), "value": value, "source": "manual"}
            for category, value in totals["asset_categories"].items()
        ],
    ]
    return {
        "net_worth": round(totals["net_worth"], 2),
        "total_assets": round(totals["total_assets"], 2),
        "total_liabilities": round(totals["total_liabilities"], 2),
        "monthly_net_worth_change": monthly_change,
        "investment": {
            "total_assets": round(totals["investment_assets"], 2),
            "total_profit_loss": investment.get("total_profit_loss"),
            "allocation": investment.get("allocation", {}),
            "source": "portfolio_summary",
        },
        "pension_benefit": round(totals["pension_benefit_value"], 2),
        "total_wealth_including_pension_benefit": round(totals["total_assets"] + totals["pension_benefit_value"], 2),
        "asset_breakdown": assets,
        "liability_breakdown": [
            {"category": category, "label": _category_label(category), "value": value}
            for category, value in totals["liability_categories"].items()
        ],
        "attribution": {
            "cash_surplus": cashflow["surplus"],
            "cashflow_available": cashflow["available"],
            "cashflow_income": cashflow["income"],
            "cashflow_expense": cashflow["expense"],
            "investment_return": investment_return,
            "investment_return_available": investment_return is not None,
            "other_adjustment": other_adjustment,
            "baseline_available": baseline is not None,
        },
        "trend": _trend(portfolio_id, totals, trend_days),
    }


def _category_label(category: str) -> str:
    return {
        "cash_deposits": "现金及存款",
        "retirement_long_term": "养老与长期权益",
        "pension_benefit": "养老保障权益",
        "other_assets": "其他资产",
        "credit_card": "信用卡",
        "consumer_loan": "信用贷",
        "mortgage": "房贷",
        "other_liability": "其他负债",
    }.get(category, category)


def preview_import(filename: str, content: bytes, content_type: str | None) -> dict[str, Any]:
    """Best-effort candidate extraction; persistence is always a separate confirmation."""
    text = ""
    source_type = "PDF" if filename.lower().endswith(".pdf") else "SCREENSHOT"
    if source_type == "PDF":
        try:
            from backend.services.research_service import _extract_pdf_text
            text, _ = _extract_pdf_text(content)
        except Exception:
            text = ""
    searchable = f"{filename}\n{text}".lower()
    matched_type = next((key for key, words in {
        "housing_fund": ("公积金",), "enterprise_annuity": ("企业年金",),
        "personal_pension": ("个人养老金",), "basic_pension": ("养老保险", "社保"),
        "credit_card": ("信用卡",), "consumer_loan": ("信用贷", "消费贷"),
        "mortgage": ("房贷",), "time_deposit": ("定期", "大额存单"),
        "bank_cash": ("活期", "余额", "存款"),
    }.items() if any(word in searchable for word in words)), "other_asset")
    kind = "liability" if matched_type in LIABILITY_TYPES else "asset"
    # A date or page number must never be guessed as a balance.  Only return a
    # candidate where the source itself carries an explicit currency marker.
    amount_match = re.search(r"(?:¥|￥|人民币|rmb)\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text, re.I)
    value = float(amount_match.group(1).replace(",", "")) if amount_match else None
    date_match = re.search(r"(20\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})", text)
    value_as_of = "-".join((date_match.group(1), date_match.group(2).zfill(2), date_match.group(3).zfill(2))) if date_match else None
    return {
        "requires_confirmation": True,
        "source_type": source_type,
        "draft": {"kind": kind, "item_type": matched_type, "name": filename.rsplit(".", 1)[0], "current_value": value, "value_as_of": value_as_of},
        "message": "已生成候选值，请核对后确认保存。" if value is not None else "未能可靠提取金额，请手工补全后确认保存。",
    }
