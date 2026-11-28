"""Shared private E/F capability gate and read projections."""
import os

from .contracts import now
from .portfolio import timestamp
from .portfolio_store import PortfolioStore


def isolation_enabled():
    return os.environ.get("WEALTHPILOT_E1_PORTFOLIO_MODE") == "ISOLATED"


def require_isolation():
    if not isolation_enabled():
        raise ValueError("E1_PORTFOLIO_ISOLATION_REQUIRED")



PRODUCTION_ACCESS = {
    "READ": frozenset({"read"}),
    "PREVIEW": frozenset({"read", "preview"}),
    "CONFIRM": frozenset({"read", "preview", "confirm"}),
}
PORTFOLIO_ENV_KEYS = {"WEALTHPILOT_E1_PORTFOLIO_MODE", "WEALTHPILOT_E1_PORTFOLIO_ACCESS"}


def production_enabled():
    return (os.environ.get("WEALTHPILOT_E1_PORTFOLIO_MODE") == "PRODUCTION"
            and os.environ.get("PUBLIC_DEMO_MODE", "").lower() not in {"1", "true", "yes", "on"})


def portfolio_capabilities():
    if isolation_enabled():
        return PRODUCTION_ACCESS["CONFIRM"]
    if production_enabled():
        return PRODUCTION_ACCESS.get(os.environ.get("WEALTHPILOT_E1_PORTFOLIO_ACCESS"), frozenset())
    return frozenset()


def require_portfolio_access(operation):
    capabilities = portfolio_capabilities()
    if operation not in capabilities:
        raise ValueError("E1_PORTFOLIO_ACCESS_DENIED" if capabilities else "E1_PORTFOLIO_GOVERNANCE_DISABLED")



def require_production_scope(database, portfolio_id, contract):
    from app.shared_sqlite import verify

    if not contract or verify(database, contract)["portfolio_id"] != portfolio_id:
        raise ValueError("E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH")

def load_portfolio_view(database, portfolio_id, snapshot_id, contract=None):
    from app.asset_views import load_view

    require_portfolio_access("read")
    if production_enabled() and not contract:
        raise ValueError("E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH")
    view = load_view(database, portfolio_id, snapshot_id, contract)
    validate_view_origin(view)
    return view


def validate_view_origin(view):
    if production_enabled() and (view.get("views", {}).get("get_wealth_overview", {}).get("synthetic") is not False):
        raise ValueError("E1_PORTFOLIO_PRODUCTION_EVIDENCE_REQUIRED")


def validate_policy_origin(context):
    release = context.get("release") or {}
    if production_enabled():
        if release.get("synthetic") is not False or release.get("environment") != "PRODUCTION":
            raise ValueError("E1_PORTFOLIO_PRODUCTION_EVIDENCE_REQUIRED")
    elif release.get("synthetic") is not True and release.get("environment") != "ISOLATED_REHEARSAL":
        raise ValueError("E1_PORTFOLIO_ISOLATION_REQUIRED")

def read_portfolio_records(thesis_store, request):
    return PortfolioStore(thesis_store).query(**request.model_dump(), at=timestamp(now()),
                                             include_evidence=request.revision is not None)


def risk_preview(source):
    return {"qualification": source["qualification"], "policy_reference": source["policy"]["reference"],
            "actual_risk_candidates": {key: fact for key, fact in source["risk_facts"].items() if fact["hit"]},
            "execution_authorized": False}
