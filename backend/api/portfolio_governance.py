"""Private, opt-in E1 allocation governance. No startup database mutation."""
from functools import lru_cache
import os
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from app import state
from backend.services.investment_thesis.portfolio import PortfolioGovernance
from backend.services.investment_thesis.portfolio_access import (
    portfolio_capabilities, production_enabled, read_portfolio_records, risk_preview,
)
from backend.services.investment_thesis.portfolio_contracts import (
    PortfolioConfirmation, PortfolioPreviewRequest, PortfolioQuery, PortfolioSourceRequest,
)
from backend.services.investment_thesis.portfolio_sources import read_portfolio_sources
from backend.services.investment_thesis.reconciliation import reconcile_portfolio
from backend.services.investment_thesis.reconciliation_contract import ReconciliationRequest
from backend.services.investment_thesis.store import ThesisStore

router = APIRouter(prefix="/governance")


@lru_cache(maxsize=4)
def _bridge(path, portfolio_id, business_db, contract):
    return PortfolioGovernance(ThesisStore(path, portfolio_id),
                               lambda req: read_portfolio_sources(business_db, portfolio_id, req, contract))


def _dispatch(http, capability, operation):
    path = os.environ.get("WEALTHPILOT_E1_DB_PATH")
    capabilities = portfolio_capabilities()
    production = production_enabled()
    scope = getattr(http.app.state, "portfolio_governance_scope", None) if production else state.portfolio_id
    if not path or not capabilities or scope is None:
        raise HTTPException(404, "E1_PORTFOLIO_GOVERNANCE_DISABLED")
    if capability not in capabilities:
        raise HTTPException(403, "E1_PORTFOLIO_ACCESS_DENIED")
    if production and capability == "confirm" and http.headers.get("origin") != (
            f"http://{http.headers.get('host')}"):
        raise HTTPException(403, "E1_PORTFOLIO_PRIVATE_CONFIRMATION_REQUIRED")
    from app.database import DB_PATH
    try:
        return operation(_bridge(path, scope, DB_PATH, os.environ.get("WEALTHPILOT_SQLITE_CONTRACT")))
    except (ValueError, OSError, sqlite3.Error) as error:
        code = str(error)
        if re.fullmatch(r"E1_[A-Z0-9_]{1,100}", code):
            raise HTTPException(409, code) from None
        raise HTTPException(503, "E1_PORTFOLIO_OPERATION_UNAVAILABLE") from None


@router.get("")
def get_records(http: Request, bundle_id: str | None = None, revision: int | None = None, risk_key: str | None = None):
    from pydantic import ValidationError
    try:
        query = PortfolioQuery(bundle_id=bundle_id, revision=revision, risk_key=risk_key)
    except ValidationError:
        raise HTTPException(422, "E1_PORTFOLIO_QUERY_INVALID") from None
    records = _dispatch(http, "read", lambda bridge: read_portfolio_records(bridge.store.thesis_store, query))
    if production_enabled():
        records["capabilities"] = sorted(portfolio_capabilities())
    return records


@router.post("/risk-preview")
def get_risk_candidates(http: Request, request: PortfolioSourceRequest):
    return _dispatch(http, "read", lambda bridge: risk_preview(bridge.source_reader(request)))


@router.post("/preview")
def preview_decision(http: Request, request: PortfolioPreviewRequest):
    return _dispatch(http, "preview", lambda bridge: bridge.preview(request))


@router.post("/commit")
def commit_decision(http: Request, request: PortfolioConfirmation):
    return _dispatch(http, "confirm", lambda bridge: bridge.commit(request))


@router.post("/reconcile")
def reconcile_decision(http: Request, request: ReconciliationRequest):
    from app.database import DB_PATH
    return _dispatch(http, "read", lambda bridge: reconcile_portfolio(bridge.store.thesis_store, DB_PATH, request,
                                                        os.environ.get("WEALTHPILOT_SQLITE_CONTRACT")))
