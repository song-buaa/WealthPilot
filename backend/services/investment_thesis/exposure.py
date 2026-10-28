"""Pure conditional CNY marked-value exposure. No quotes, funds checks or orders."""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from typing import Literal

from pydantic import Field
from .contracts import Record, Security


class ExposureScenario(Record):
    snapshot_id: str = Field(pattern=r"^asset-view:v1:[1-9][0-9]{0,17}$")
    funding: Literal["INTERNAL_CASH", "EXTERNAL_CASH"]
    investment_value_added_cny: Decimal | None = Field(default=None, gt=0)
    external_cash_added_cny: Decimal | None = Field(default=None, ge=0)
    fees_cny: Decimal | None = Field(default=None, ge=0)
    assumptions_reference: str = Field(min_length=1, max_length=2000)
    hypothetical_limit_weight: Decimal | None = Field(default=None, gt=0, lt=1)
    hypothetical_limit_reference: str | None = Field(default=None, max_length=2000)


def calculate_exposure(view, security, scenario, *, portfolio_id, at, confirmed_targets=None):
    """Reuse U1 whole-denominator qualification and existing issuer owner.

    Supplied views/limits remain conditional inputs, never broker authority.
    All historical results describe `at`, not current trading eligibility.
    """
    from app.asset_views import qualify_view, VIEW_TTL
    from backend.services.instruments.issuers import exposure, IDENTITIES
    from backend.utils.symbol import position_security_identity
    security = Security.model_validate(security)
    scenario = ExposureScenario.model_validate(scenario) if scenario is not None else None
    output = dict(status="CANNOT_QUANTIFY", reasons=[], snapshot_id=(view or {}).get("snapshot_id"),
        decision_as_of=at.isoformat(), currency="CNY", denominator_scope="ALL_STORED_INVESTMENTS_INCLUDING_CASH",
        input_authority="SUPPLIED_SNAPSHOT_CONDITIONAL_NOT_INDEPENDENTLY_CERTIFIED",
        direct_before_cny=None, direct_after_cny=None, denominator_before_cny=None, denominator_after_cny=None,
        direct_weight_before=None, direct_weight_after=None, members=[],
        etf_indirect_exposure="UNKNOWN", total_issuer_exposure="UNKNOWN", limit_status="UNKNOWN",
        confirmed_limit_status="UNKNOWN", execution_authorized=False, order_created=False)
    def refuse(reason):
        output.update(status="CANNOT_QUANTIFY", direct_before_cny=None, direct_after_cny=None,
            denominator_before_cny=None, denominator_after_cny=None, direct_weight_before=None,
            direct_weight_after=None, confirmed_limit_status="UNKNOWN", limit_status="UNKNOWN")
        output["reasons"].append(reason)
        return output
    if not isinstance(view, dict) or not scenario:
        return refuse("QUALIFIED_VIEW_AND_EXPLICIT_SCENARIO_REQUIRED")
    if view.get("portfolio_id") != portfolio_id or scenario.snapshot_id != view.get("snapshot_id"):
        return refuse("CROSS_SCOPE_OR_SNAPSHOT_REJECTED")
    try:
        captured = datetime.fromisoformat(view["captured_at"])
        if captured.tzinfo is None or not 0 <= (at-captured).total_seconds() < VIEW_TTL:
            return refuse("VIEW_EXPIRED_OR_FUTURE")
        q = view["data_quality"]
        if q.get("policy_version") != "d1.1" or not q.get("analysis_eligibility", {}).get("B", {}).get("allowed"):
            return refuse("ORIGINAL_WHOLE_PORTFOLIO_AMOUNT_VETO")
        inputs = q["security_inputs"]
        if not inputs or len({i["id"] for i in inputs}) != len(inputs):
            return refuse("MISSING_OR_DUPLICATED_DENOMINATOR_INPUTS")
        members = deepcopy(inputs)
        recomputed = exposure(members)
        denominator = Decimal(str(recomputed["investment_denominator_cny"]))
        if not denominator.is_finite() or denominator <= 0 or any(
                not Decimal(str(i["market_value_cny"])).is_finite() or Decimal(str(i["market_value_cny"])) < 0 for i in inputs):
            return refuse("INVALID_DENOMINATOR")
        # An account-incomplete, stale or unknown FX row is not excused by size.
        accounts = {a["account_ref"]: a for a in q["accounts"]}
        for i in inputs:
            if i.get("vehicle_type") != "CASH":
                price = Decimal(str(i.get("current_price")))
                quantity = Decimal(str(i.get("quantity")))
                if not price.is_finite() or price <= 0 or not quantity.is_finite() or quantity < 0:
                    return refuse("PRICE_OR_QUANTITY_UNVERIFIED")
            account = accounts.get(i.get("account_ref"), {})
            if account.get("publication_status") != "matched" or account.get("complete") is not True:
                return refuse("DENOMINATOR_ACCOUNT_COVERAGE_UNKNOWN")
        if recomputed["unmapped_direct_holdings"]:
            return refuse("ISSUER_MAPPING_INCOMPLETE")
        if Decimal(str(view["issuer_exposure"]["investment_denominator_cny"])) != denominator:
            return refuse("DENOMINATOR_INPUTS_DO_NOT_RECONCILE")
        derived = deepcopy(view)
        derived["context_current"] = view.get("context_current", False)
        derived["issuer_exposure"] = recomputed
        qualify_view(derived, at=at, symbol=None, analysis_scope="investments")
        output["source_quality"] = derived["data_quality"]
        if not derived["data_quality"]["analysis_eligibility"]["B"]["allowed"]:
            return refuse("WHOLE_PORTFOLIO_AMOUNT_UNQUALIFIED")
        if any(i.get("quality_issues") for i in derived["data_quality"]["security_inputs"]):
            return refuse("DENOMINATOR_SOURCE_PRICE_OR_FX_GAP")
        identity = position_security_identity(security.symbol, None, None, security.currency)
        issuer_id = IDENTITIES.get(identity)
        if not issuer_id or not security.identity_reference:
            return refuse("TARGET_ISSUER_IDENTITY_UNRESOLVED")
        group = next((g for g in recomputed["companies"] if g["issuer_id"] == issuer_id), {})
        before = Decimal(str(group.get("market_value_cny", 0)))
        x, f, c = (scenario.investment_value_added_cny, scenario.external_cash_added_cny, scenario.fees_cny)
        if any(v is None for v in (x, f, c)):
            return refuse("EXPLICIT_AMOUNT_FUNDING_FEES_REQUIRED")
        if (scenario.funding == "INTERNAL_CASH" and f != 0 or
                scenario.funding == "EXTERNAL_CASH" and f <= 0):
            return refuse("FUNDING_ASSUMPTION_INCONSISTENT")
        available = sum((Decimal(str(i["market_value_cny"])) for i in inputs if i.get("vehicle_type") == "CASH"), Decimal(0))
        after_d, after_e = denominator+f-c, before+x
        if after_d <= 0 or x+c > available+f or after_e > after_d:
            return refuse("STATIC_SCENARIO_EXCEEDS_STORED_CASH_OR_DENOMINATOR")
        output.update(status="QUANTIFIED", issuer_id=issuer_id, captured_at=view["captured_at"],
            direct_before_cny=str(before), direct_after_cny=str(after_e),
            denominator_before_cny=str(denominator), denominator_after_cny=str(after_d),
            direct_weight_before=float(before/denominator), direct_weight_after=float(after_e/after_d),
            members=group.get("members", []), scenario=scenario.model_dump(mode="json"),
            warning="Static direct-security exposure only, not total look-through, a suggested purchase amount or proof of available buying power")
        trusted = confirmed_targets or {}
        if (trusted.get("basis") == "server_stage_target_store"
                and trusted.get("confirmation_status") == "confirmed"
                and trusted.get("confirmation_source") == "local_operator_cli"
                and trusted.get("portfolio_id") == portfolio_id
                and datetime.fromisoformat(trusted["effective_from"]) <= at < datetime.fromisoformat(trusted["valid_until"])
                and datetime.fromisoformat(trusted["confirmed_at"]) <= at):
            bound = next((i for i in trusted.get("issuers", []) if i["issuer_id"] == issuer_id), None)
            if bound:
                ceiling = Decimal(str(bound["target_weight"])) + Decimal(str(bound.get("tolerance", 0)))
                if not 0 < ceiling < 1:
                    return refuse("CONFIRMED_TARGET_INVALID")
                output["confirmed_limit_status"] = "EXCEEDED" if after_e/after_d > ceiling else "WITHIN"
                output["confirmed_target"] = dict(version=trusted["version"], ceiling_weight=str(ceiling),
                    basis=trusted["basis"], reference=trusted["confirmation_reference"],
                    scope="Confirmed stage issuer concentration target; does not override formal discipline or authorize orders")
        if scenario.hypothetical_limit_weight is not None and scenario.hypothetical_limit_reference:
            output["limit_status"] = "SCENARIO_EXCEEDED" if after_e/after_d > scenario.hypothetical_limit_weight else "SCENARIO_WITHIN"
            output["limit_authority"] = "USER_HYPOTHETICAL_NOT_CONFIRMED_PERSONAL_POLICY"
        return output
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return refuse("VIEW_INCOMPLETE_OR_INVALID")
