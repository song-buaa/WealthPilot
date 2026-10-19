"""Protected, non-trading prepare; existing private Web may read fixed views."""
import os
import secrets
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import state
from app.asset_views import load_view, public_view, ERRORS
from app.database import DB_PATH
from backend.services.asset_analysis import AssetAnalysisPreparation, worker_call, REQUEST_PATTERN
from pocs.chatgpt_skills.analysis import AnalysisRequest

router = APIRouter()


def _authorized(authorization):
    from pocs.chatgpt_skills.prepare import private_token
    try:
        token = private_token(os.environ["WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE"])
    except (KeyError, OSError, ValueError):
        raise HTTPException(503, "ANALYSIS_PREPARE_NOT_CONFIGURED") from None
    if not authorization or not secrets.compare_digest(authorization, "Bearer "+token):
        raise HTTPException(403, "ANALYSIS_PREPARE_FORBIDDEN")


def _preparer():
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if PUBLIC_DEMO_MODE or os.environ.get("WEALTHPILOT_ENABLED_SYNC_BROKERS") is None:
        raise HTTPException(503, "ANALYSIS_PREPARE_NOT_CONFIGURED")
    from backend.api.broker_sync import _executor
    return AssetAnalysisPreparation(_executor(), DB_PATH, contract=os.environ.get("WEALTHPILOT_SQLITE_CONTRACT"))


def _error(exc):
    code = str(exc) if str(exc) in ERRORS else "ASSET_VIEW_INVALID"
    return HTTPException(404 if code == "ASSET_VIEW_NOT_FOUND" else 409, code)


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_request_id: str = Field(pattern="^"+REQUEST_PATTERN+"$")


@router.post("/prepare")
def prepare(request: PrepareRequest, tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    service = _preparer()
    try:
        result, claim = service.request(request.analysis_request_id)
    except ValueError as exc:
        raise _error(exc) from None
    if claim:
        tasks.add_task(service.run, *claim)
    return result


@router.get("/requests/{request_id}")
def status(request_id: str, authorization: str | None = Header(default=None)):
    _authorized(authorization)
    try:
        return _preparer().status(request_id)
    except ValueError as exc:
        raise _error(exc) from None


def fixed_view(snapshot_id):
    try:
        return load_view(DB_PATH, state.portfolio_id, snapshot_id, os.environ.get("WEALTHPILOT_SQLITE_CONTRACT"))
    except ValueError as exc:
        raise _error(exc) from None


def fixed_projection(snapshot_id, key):
    view = public_view(fixed_view(snapshot_id))
    return {**view["views"][key], "snapshot_id":snapshot_id, "data_quality":view["data_quality"]}


@router.get("/views/{snapshot_id}")
def get_view(snapshot_id: str):
    return public_view(fixed_view(snapshot_id))


@router.post("/views/{snapshot_id}/review")
def review(snapshot_id: str, request: AnalysisRequest):
    fixed_view(snapshot_id)
    if request.snapshot_id and request.snapshot_id != snapshot_id:
        raise HTTPException(409, "ANALYSIS_REQUEST_CONFLICT")
    try:
        return worker_call(DB_PATH, state.portfolio_id, "review_portfolio",
                           request.model_copy(update={"snapshot_id":snapshot_id}).model_dump(mode="json"),
                           contract=os.environ.get("WEALTHPILOT_SQLITE_CONTRACT"))
    except ValueError as exc:
        raise _error(exc) from None
