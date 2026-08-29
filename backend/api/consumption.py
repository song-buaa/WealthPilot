"""Read-only Consumption Analytics API."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query
from app.database import get_session
from backend.services.consumption.analytics import ConsumptionAnalyticsService

router=APIRouter()

def _value(value):
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, date): return value.isoformat()
    if hasattr(value, "value"): return value.value
    if isinstance(value, tuple): return [_serialize(item) for item in value]
    return value
def _serialize(item): return {key:_value(value) for key,value in item.__dict__.items()}

@router.get("/analytics")
def get_consumption_analytics(
    as_of: date | None = Query(default=None),
    months: int = Query(default=12, ge=1, le=24),
    account_ids: list[str] | None = Query(default=None),
):
    session=get_session()
    try:
        result=ConsumptionAnalyticsService(session).summary(as_of=as_of or date.today(),months=months,account_ids=tuple(account_ids) if account_ids else None)
        return _serialize(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally: session.close()
