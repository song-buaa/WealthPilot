"""Consumption analytics, detail editing, and explicit candidate-review API."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from app.database import get_session
from backend.services.consumption.analytics import ConsumptionAnalyticsService
from backend.services.consumption.candidate_review import CandidateReviewError, ConsumptionCandidateReviewService
from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.classification_design import (
    DAILY_SECONDARY, HARD_INELIGIBLE, HOUSING_SECONDARY, TRAVEL_SECONDARY,
    ClassificationStatus, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.economic_events import EventType
from backend.services.consumption.models import ConsumptionInterpretation, EconomicEvent

router=APIRouter()


def _month_start(value: str) -> date:
    try:
        return date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="month must use YYYY-MM") from exc

def _value(value):
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, date): return value.isoformat()
    if hasattr(value, "value"): return value.value
    if isinstance(value, tuple): return [_serialize(item) for item in value]
    return value
def _serialize(item): return {key:_value(value) for key,value in item.__dict__.items()}


class DetailClassificationUpdate(BaseModel):
    primary_category: PrimaryCategory
    secondary_category: str


_SECONDARY_BY_PRIMARY = {
    PrimaryCategory.DAILY: DAILY_SECONDARY,
    PrimaryCategory.TRAVEL: TRAVEL_SECONDARY,
    PrimaryCategory.HOUSING: HOUSING_SECONDARY,
}


def _csv_response(text: str, filename: str) -> Response:
    return Response(
        content="\ufeff".encode("utf-8") + text.encode("utf-8"),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _validate_detail_filters(
    primary_category: PrimaryCategory | None, secondary_category: str | None,
) -> None:
    if secondary_category is None:
        return
    if primary_category is None:
        raise HTTPException(status_code=422, detail="secondary_category requires primary_category")
    if secondary_category not in _SECONDARY_BY_PRIMARY[primary_category]:
        raise HTTPException(status_code=422, detail="secondary_category does not belong to primary_category")

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


@router.get("/events")
def get_consumption_events(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    account_ids: list[str] | None = Query(default=None),
    classification_status: Literal["CLASSIFIED", "NEEDS_REVIEW"] | None = Query(default=None),
    primary_category: PrimaryCategory | None = Query(default=None),
    secondary_category: str | None = Query(default=None),
):
    session=get_session()
    try:
        _validate_detail_filters(primary_category, secondary_category)
        result=ConsumptionAnalyticsService(session).monthly_detail(
            month=_month_start(month), limit=limit, offset=offset,
            account_ids=tuple(account_ids) if account_ids else None,
            classification_status=classification_status and ClassificationStatus(classification_status),
            primary_category=primary_category, secondary_category=secondary_category,
        )
        return _serialize(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally: session.close()


@router.get("/candidates")
def get_consumption_candidates(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    session=get_session()
    try:
        result=ConsumptionCandidateReviewService(session).list_candidates(
            month=_month_start(month) if month else None, limit=limit, offset=offset,
        )
        return _serialize(result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally: session.close()


@router.get("/events/export.csv")
def export_consumption_events(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    account_ids: list[str] | None = Query(default=None),
    classification_status: Literal["CLASSIFIED", "NEEDS_REVIEW"] | None = Query(default=None),
    primary_category: PrimaryCategory | None = Query(default=None),
    secondary_category: str | None = Query(default=None),
):
    session=get_session()
    try:
        _validate_detail_filters(primary_category, secondary_category)
        return _csv_response(
            ConsumptionAnalyticsService(session).export_monthly_detail_csv(
                month=_month_start(month), account_ids=tuple(account_ids) if account_ids else None,
                classification_status=classification_status and ClassificationStatus(classification_status),
                primary_category=primary_category, secondary_category=secondary_category,
            ),
            f"consumption-events-{month}.csv",
        )
    finally: session.close()


@router.put("/events/{event_id}/classification")
def update_consumption_event_classification(event_id: str, update: DetailClassificationUpdate):
    if update.secondary_category not in _SECONDARY_BY_PRIMARY[update.primary_category]:
        raise HTTPException(status_code=422, detail="secondary category does not belong to primary category")
    session=get_session()
    try:
        event=session.get(EconomicEvent,event_id)
        current=session.query(ConsumptionInterpretation).filter_by(event_id=event_id,is_active=True).one_or_none()
        if event is None or not event.is_active or current is None:
            raise HTTPException(status_code=404, detail="consumption event not found")
        event_type=EventType(event.event_type)
        if event_type in HARD_INELIGIBLE or event_type == EventType.REFUND:
            raise HTTPException(status_code=422, detail="hard-excluded events cannot be classified as consumption")
        if current.eligibility_status != EligibilityStatus.ELIGIBLE.value:
            raise HTTPException(status_code=409, detail="only eligible consumption detail events can be edited")
        result=ClassificationResolver().confirm_event(
            session, event_id,
            eligibility_status=EligibilityStatus.ELIGIBLE,
            primary_category=update.primary_category,
            secondary_category=update.secondary_category,
            reason="DETAIL_CLASSIFICATION_EDIT",
        )
        session.commit()
        return {
            "event_id": result.event_id,
            "primary_category": result.primary_category,
            "secondary_category": result.secondary_category,
            "classification_status": result.classification_status,
            "revision_number": result.revision_number,
        }
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally: session.close()


@router.put("/candidates/{event_id}/confirm")
def confirm_consumption_candidate(event_id: str, update: DetailClassificationUpdate):
    if update.secondary_category not in _SECONDARY_BY_PRIMARY[update.primary_category]:
        raise HTTPException(status_code=422, detail="secondary category does not belong to primary category")
    session=get_session()
    try:
        result=ConsumptionCandidateReviewService(session).confirm_as_consumption(
            event_id, primary_category=update.primary_category, secondary_category=update.secondary_category,
        )
        session.commit()
        return {
            "event_id": result.event_id,
            "eligibility_status": result.eligibility_status,
            "primary_category": result.primary_category,
            "secondary_category": result.secondary_category,
            "classification_status": result.classification_status,
            "revision_number": result.revision_number,
        }
    except CandidateReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally: session.close()


@router.put("/candidates/{event_id}/reject")
def reject_consumption_candidate(event_id: str):
    session=get_session()
    try:
        result=ConsumptionCandidateReviewService(session).reject_as_non_consumption(event_id)
        session.commit()
        return {
            "event_id": result.event_id,
            "eligibility_status": result.eligibility_status,
            "classification_status": result.classification_status,
            "revision_number": result.revision_number,
        }
    except CandidateReviewError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally: session.close()
