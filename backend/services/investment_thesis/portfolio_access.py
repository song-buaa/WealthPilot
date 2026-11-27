"""Shared isolation gate and private E1 read projections."""
import os

from .contracts import now
from .portfolio import timestamp
from .portfolio_store import PortfolioStore


def isolation_enabled():
    return os.environ.get("WEALTHPILOT_E1_PORTFOLIO_MODE") == "ISOLATED"


def require_isolation():
    if not isolation_enabled():
        raise ValueError("E1_PORTFOLIO_ISOLATION_REQUIRED")


def read_portfolio_records(thesis_store, request):
    return PortfolioStore(thesis_store).query(**request.model_dump(), at=timestamp(now()),
                                             include_evidence=request.revision is not None)


def risk_preview(source):
    return {"qualification": source["qualification"], "policy_reference": source["policy"]["reference"],
            "actual_risk_candidates": {key: fact for key, fact in source["risk_facts"].items() if fact["hit"]},
            "execution_authorized": False}
