"""Private, opt-in E1 allocation governance. No startup database mutation."""
from functools import lru_cache
import os
import re
import sqlite3

from fastapi import APIRouter, HTTPException

from app import state
from backend.services.investment_thesis.portfolio import PortfolioGovernance
from backend.services.investment_thesis.portfolio_access import isolation_enabled, read_portfolio_records, risk_preview
from backend.services.investment_thesis.portfolio_contracts import (
    PortfolioConfirmation, PortfolioPreviewRequest, PortfolioQuery, PortfolioSourceRequest,
)
from backend.services.investment_thesis.portfolio_sources import read_portfolio_sources
from backend.services.investment_thesis.store import ThesisStore

router = APIRouter(prefix="/governance")


@lru_cache(maxsize=4)
def _bridge(path, portfolio_id, business_db, contract):
    return PortfolioGovernance(ThesisStore(path, portfolio_id),
                               lambda req: read_portfolio_sources(business_db, portfolio_id, req, contract))


def _dispatch(operation):
    path = os.environ.get("WEALTHPILOT_E1_DB_PATH")
    if not path or not isolation_enabled():
        raise HTTPException(404, "E1_PORTFOLIO_GOVERNANCE_DISABLED")
    from app.database import DB_PATH
    try:
        return operation(_bridge(path, state.portfolio_id, DB_PATH, os.environ.get("WEALTHPILOT_SQLITE_CONTRACT")))
    except (ValueError, OSError, sqlite3.Error) as error:
        code = str(error)
        if re.fullmatch(r"E1_[A-Z0-9_]{1,100}", code):
            raise HTTPException(409, code) from None
        raise HTTPException(503, "E1_PORTFOLIO_OPERATION_UNAVAILABLE") from None


@router.get("")
def get_records(bundle_id: str | None = None, revision: int | None = None, risk_key: str | None = None):
    from pydantic import ValidationError
    try:
        query = PortfolioQuery(bundle_id=bundle_id, revision=revision, risk_key=risk_key)
    except ValidationError:
        raise HTTPException(422, "E1_PORTFOLIO_QUERY_INVALID") from None
    return _dispatch(lambda bridge: read_portfolio_records(bridge.store.thesis_store, query))


@router.post("/risk-preview")
def get_risk_candidates(request: PortfolioSourceRequest):
    return _dispatch(lambda bridge: risk_preview(bridge.source_reader(request)))


@router.post("/preview")
def preview_decision(request: PortfolioPreviewRequest):
    return _dispatch(lambda bridge: bridge.preview(request))


@router.post("/commit")
def commit_decision(request: PortfolioConfirmation):
    return _dispatch(lambda bridge: bridge.commit(request))
