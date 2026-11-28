"""Pure scenario calculations for AllocationService over a single U1 view.

Not an asset/loan ledger or trading planner. Nothing is persisted or synchronized.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IssuerTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    issuer_id: str = Field(min_length=1,max_length=80)
    target_weight: float = Field(ge=0,lt=1)
    tolerance: float = Field(default=0,ge=0,le=.2)
    direction: Literal["concentration_control","gradual_exit"] = "concentration_control"
    phases: list[float] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def decreasing_phases(self):
        points=[self.target_weight]+self.phases
        if any(a<=b for a,b in zip(points,points[1:])):
            raise ValueError('TARGET_EXIT_PHASES_MUST_DECREASE')
        return self


class StageTargets(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    version: str = Field(min_length=1,max_length=80)
    supersedes: str | None = Field(default=None,max_length=80)
    stage: str = Field(min_length=1,max_length=80)
    confirmed_by_user: Literal[True]
    confirmed_at: datetime
    valid_until: datetime
    china_equity_max: float | None = Field(default=None,ge=0,le=1)
    fixed_income_target: float | None = Field(default=None,ge=0,le=1)
    fixed_income_ceiling: float | None = Field(default=None,ge=0,le=1)
    investment_cash_max: float | None = Field(default=None,ge=0,le=1)
    issuers: list[IssuerTarget] = Field(default_factory=list,max_length=30)
    funding_policy: dict[str,Literal["offshore_fixed_income","repay_cny_loan","investment_cash"]] = Field(default_factory=dict)
    no_new_leverage: Literal[True] = True

    @model_validator(mode="after")
    def valid_dates(self):
        if (self.confirmed_at.tzinfo is None or self.valid_until.tzinfo is None
                or self.confirmed_at >= self.valid_until):
            raise ValueError("TARGET_DATE_RANGE_INVALID")
        if len({i.issuer_id for i in self.issuers}) != len(self.issuers):
            raise ValueError("DUPLICATE_ISSUER_TARGET")
        if any(any(not 0 <= p < 1 for p in i.phases) for i in self.issuers):
            raise ValueError("TARGET_PHASE_INVALID")
        return self


def select_targets(versions, *, at=None):
    """Validate request proposals, without treating client claims as confirmation.

    Server confirmation is resolved separately by target_context. The legacy
    confirmed_* input fields remain accepted for wire compatibility;
    neither those fields nor a content digest establish a user confirmation.
    """
    at = at or datetime.now(timezone.utc)
    indexed = {}
    for raw in versions:
        target = StageTargets.model_validate(raw)
        content = target.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()
        prior = indexed.get(target.version)
        if prior and prior[1] != fingerprint:
            raise ValueError("TARGET_VERSION_CONTENT_CONFLICT")
        indexed[target.version] = (target,fingerprint)
    active = [(t,d) for t,d in indexed.values() if t.confirmed_at <= at < t.valid_until]
    if not active:
        return None
    active.sort(key=lambda entry:entry[0].confirmed_at)
    if len(active)>1 and active[-1][0].supersedes != active[-2][0].version:
        raise ValueError("TARGET_VERSION_LINEAGE_REQUIRED")
    target,digest = active[-1]
    return {**target.model_dump(mode="json"),"digest":digest,
            "basis":"request_proposal_unverified","confirmation_status":"unverified"}


def target_context(view, issuer_id, versions, formal_rules, defaults, *, at=None):
    proposed = select_targets(versions,at=at)
    from backend.services.stage_targets import read_target_context
    server = read_target_context((view or {}).get("portfolio_id"), at=at)
    confirmed = server["confirmed"]
    if proposed is None:
        proposed = server["candidate"]
    target = confirmed or proposed
    group = next((g for g in (view or {}).get("issuer_exposure",{}).get("companies",[]) if g["issuer_id"]==issuer_id),None)
    warnings=list(server["issues"])
    if confirmed and proposed:
        fields = set(StageTargets.model_fields) - {"confirmed_by_user", "confirmed_at"}
        if any(confirmed.get(key) != proposed.get(key) for key in fields):
            warnings.append("TARGET_REQUEST_DIFFERS_FROM_SERVER_CONFIRMED")
    formal = formal_rules if isinstance(formal_rules,dict) else {}
    if target:
        cash_min = formal.get("min_cash_pct")
        if cash_min is not None and target.get("investment_cash_max") is not None and cash_min>target["investment_cash_max"]:
            warnings.append("TARGET_DISCIPLINE_CASH_CONFLICT")
        concentration_max = formal.get("max_single_position")
        if concentration_max is not None:
            for issuer in target["issuers"]:
                if issuer["target_weight"] + issuer["tolerance"] > concentration_max:
                    warnings.append("TARGET_DISCIPLINE_CONCENTRATION_CONFLICT:"+issuer["issuer_id"])
        effective=defaults.get("effective_rules",{}) if isinstance(defaults,dict) else {}
        if (not formal and target.get("investment_cash_max") is not None
                and effective.get("min_cash_pct",0)>target["investment_cash_max"]):
            warnings.append("DEFAULT_OR_UNVERIFIED_POLICY_TARGET_CASH_DIFFERENCE")
    return dict(snapshot_id=(view or {}).get("snapshot_id"),issuer_id=issuer_id,
        company_exposure=group,denominator_cny=(view or {}).get("issuer_exposure",{}).get("investment_denominator_cny"),
        formal_discipline=formal_rules,confirmed_stage_targets=confirmed,proposed_stage_targets=proposed,system_defaults=defaults,
        target_confirmation_status="confirmed" if confirmed else "unverified" if proposed else "missing",
        warnings=warnings,authority="Only portfolio-bound local server configuration can confirm stage targets. Request proposals remain assumptions; targets do not overwrite formal discipline or authorize trades.")


def qualification_view(view, legs=(), *, at=None):
    """Concentration needs ALL denominator inputs; repayment also needs loan facts.

    D1 single-symbol qualification must not bless other accounts in a scenario.
    Requalification can only restrict the original precise-rebalance boolean.
    """
    from copy import deepcopy
    result=deepcopy(view)
    if result and result.get("captured_at") and result.get("data_quality",{}).get("policy_version")=="d1.1":
        from app.asset_views import qualify_view
        original_allowed=result["data_quality"].get("can_propose_rebalance",False)
        repayment=any((r.model_dump() if hasattr(r,"model_dump") else r).get("destination")=="repay_cny_loan" for r in legs)
        qualify_view(result,at=at or datetime.now(timezone.utc),symbol=None,
            analysis_scope="net_worth" if repayment else "investments")
        result["data_quality"]["can_propose_rebalance"] &= original_allowed
    return result


class ScenarioLeg(BaseModel):
    model_config = ConfigDict(extra="forbid",allow_inf_nan=False)
    snapshot_id: str = Field(pattern=r"^asset-view:v1:[1-9][0-9]{0,17}$")
    issuer_id: str = Field(min_length=1,max_length=80)
    position_id: int = Field(gt=0)
    account_ref: str = Field(pattern=r"^account_[A-Za-z0-9_]{1,80}$")
    destination: Literal["offshore_fixed_income","repay_cny_loan","investment_cash"]
    sale_value_cny: float | None = Field(default=None,gt=0)
    target_weight: float | None = Field(default=None,ge=0,lt=1)
    fees_cny: float = Field(ge=0)
    tax_cny: float = Field(ge=0)
    fx_cost_cny: float = Field(ge=0)
    loan_item_id: int | None = Field(default=None,gt=0)
    liquidity_floor_cny: float = Field(ge=0)
    assumptions: str = Field(min_length=1,max_length=2000)

    @model_validator(mode="after")
    def amount_or_target(self):
        if (self.sale_value_cny is None)==(self.target_weight is None):
            raise ValueError("EXACTLY_ONE_SCENARIO_AMOUNT_OR_TARGET_REQUIRED")
        return self


def simulate(view, legs, *, amount_qualified=False, target=None):
    """Major CNY units, sequential static balances. No forecast or order quantity."""
    def dec(value):
        result=Decimal(str(value))
        if not result.is_finite():
            raise ValueError("NONFINITE_SCENARIO_INPUT")
        return result
    qualified=qualification_view(view,legs)
    amount_qualified = (amount_qualified and qualified.get("context_current",False)
        and qualified.get("data_quality",{}).get("analysis_eligibility",{}).get("B",{}).get("allowed",False))
    exposure = view["issuer_exposure"]
    denominator=dec(exposure["investment_denominator_cny"])
    if denominator<=0:
        raise ValueError("INVESTMENT_DENOMINATOR_INVALID")
    companies={g["issuer_id"]:dec(g["market_value_cny"]) for g in exposure["companies"]}
    members={m["id"]:(g["issuer_id"],m,dec(m["market_value_cny"])) for g in exposure["companies"] for m in g["members"]}
    totals=view.get("totals",{})
    assets=dec(totals["total_assets"]) if totals.get("total_assets") is not None else None
    debt=dec(totals["total_liabilities"]) if totals.get("total_liabilities") is not None else None
    inputs=view.get("data_quality",{}).get("security_inputs",[])
    fixed=sum((dec(i.get("market_value_cny") or 0) for i in inputs if i.get("economic_asset_class")=="FIXED_INCOME"),Decimal(0))
    cash=sum((dec(i.get("market_value_cny") or 0) for i in inputs if i.get("economic_asset_class")=="CASH"),Decimal(0))
    loans={i["id"]:i for i in view.get("views",{}).get("get_manual_wealth_items",{}).get("items",[])
           if str(i.get("kind","")).upper()=="LIABILITY"}
    loan_balances={i:dec(v["current_value"]) for i,v in loans.items()}
    before=dict(investment_assets_cny=denominator,assets_cny=assets,liabilities_cny=debt,
        net_assets_cny=assets-debt if assets is not None and debt is not None else None)
    rows=[]
    cumulative_cost=Decimal(0)
    for raw in legs:
        leg=ScenarioLeg.model_validate(raw)
        if leg.snapshot_id != view["snapshot_id"]:
            raise ValueError("CROSS_SNAPSHOT_SCENARIO_REJECTED")
        if leg.position_id not in members:
            raise ValueError("SCENARIO_POSITION_NOT_FOUND")
        owner,member,remaining=members[leg.position_id]
        if owner!=leg.issuer_id or member["account_ref"]!=leg.account_ref:
            raise ValueError("SCENARIO_ACCOUNT_ISSUER_MISMATCH")
        cost=dec(leg.fees_cny)+dec(leg.tax_cny)+dec(leg.fx_cost_cny)
        if leg.sale_value_cny is None:
            weight=dec(leg.target_weight)
            numerator=companies[owner]-weight*denominator
            sale=(numerator/(1-weight) if leg.destination=="repay_cny_loan" else numerator+weight*cost)
        else:
            sale=dec(leg.sale_value_cny)
        if sale<=0 or sale>remaining or cost>=sale:
            raise ValueError("SCENARIO_SALE_OUT_OF_RANGE")
        net=sale-cost
        if leg.destination=="repay_cny_loan":
            loan=loans.get(leg.loan_item_id)
            if (not loan or loan.get("currency")!="CNY" or not loan.get("effective_included_in_net_worth",loan.get("included_in_net_worth"))
                    or assets is None or debt is None):
                raise ValueError("CONFIRMED_CNY_LOAN_REQUIRED")
            if net>loan_balances[leg.loan_item_id] or net>debt:
                raise ValueError("SCENARIO_REPAYMENT_EXCEEDS_DEBT")
            loan_balances[leg.loan_item_id]-=net
            debt-=net
            assets-=sale
            denominator-=sale
        else:
            denominator-=cost
            if assets is not None:
                assets-=cost
            if leg.destination=="offshore_fixed_income":
                fixed+=net
            else:
                cash+=net
        if denominator<=0 or cash<dec(leg.liquidity_floor_cny):
            raise ValueError("SCENARIO_LIQUIDITY_OR_DENOMINATOR_INVALID")
        companies[owner]-=sale
        members[leg.position_id]=(owner,member,remaining-sale)
        cumulative_cost+=cost
        net_assets=assets-debt if assets is not None and debt is not None else None
        conserved=(net_assets==before["net_assets_cny"]-cumulative_cost) if net_assets is not None else None
        warnings=[]
        if target:
            for it in target["issuers"]:
                if companies.get(it["issuer_id"],0)/denominator>dec(it["target_weight"]+it["tolerance"]):
                    warnings.append("PASSIVE_OR_REMAINING_OVERWEIGHT:"+it["issuer_id"])
            if target.get("fixed_income_target") is not None and fixed/denominator<dec(target["fixed_income_target"]):
                warnings.append("FIXED_INCOME_BELOW_STAGE_TARGET")
            if target.get("fixed_income_ceiling") is not None and fixed/denominator>dec(target["fixed_income_ceiling"]):
                warnings.append("FIXED_INCOME_ABOVE_STAGE_CEILING")
            if target.get("investment_cash_max") is not None and cash/denominator>dec(target["investment_cash_max"]):
                warnings.append("INVESTMENT_CASH_ABOVE_STAGE_TARGET")
            expected_destination=target.get("funding_policy",{}).get(leg.account_ref)
            if expected_destination and expected_destination!=leg.destination:
                warnings.append("FUNDING_DESTINATION_DIFFERS_FROM_REQUEST_PROPOSAL")
        known_cn=sum((companies[g["issuer_id"]] for g in exposure["companies"] if g.get("economic_region")=="CN"),Decimal(0))
        if target and target.get("china_equity_max") is not None and known_cn/denominator>dec(target["china_equity_max"]):
            warnings.append("KNOWN_CHINA_DIRECT_EQUITY_ABOVE_STAGE_TARGET")
        rows.append(dict(position_id=leg.position_id,account_ref=leg.account_ref,issuer_id=owner,
            sale_value_cny=sale,destination=leg.destination,proceeds_cny=net,
            costs_cny=cost,investment_assets_cny=denominator,assets_cny=assets,liabilities_cny=debt,
            net_assets_cny=net_assets,fixed_income_cny=fixed,fixed_income_weight=fixed/denominator,
            investment_cash_cny=cash,company_values_cny=dict(companies),
            known_china_direct_equity_weight=known_cn/denominator,loan_balances_cny=dict(loan_balances),
            company_weights={k:v/denominator for k,v in companies.items()},
            conservation_verified=conserved,assumptions=leg.assumptions,warnings=warnings))
    def serial(value):
        if isinstance(value,Decimal):
            return float(value)
        if isinstance(value,dict):
            return {k:serial(v) for k,v in value.items()}
        if isinstance(value,list):
            return [serial(v) for v in value]
        return value
    return serial(dict(snapshot_id=view["snapshot_id"],schema_version="wealthpilot.allocation-scenario.v1",
        mode="qualified_static_scenario" if amount_qualified else "illustrative_unqualified",
        unit="CNY major",before=before,steps=rows,order_created=False,execution_authorized=False,
        limitations=["Static marked-value assumptions, not future returns or a trade instruction.",
                     "FX, fees, taxes, transfer eligibility and timing are explicit user assumptions, not verified execution facts."]))


# V2 consumes derived scenario balances. ScenarioLeg and its callers retain V1.
Money = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=8)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=8)]
Ref = Annotated[str, Field(min_length=1, max_length=110)]
CashState = Literal["AVAILABLE", "UNSETTLED", "SETTLED_RESTRICTED", "TRANSFER_IN_FLIGHT",
                    "APPLIED_TO_PURCHASE", "APPLIED_TO_REPAYMENT", "FAILED", "UNKNOWN"]
ZERO = Decimal(0)


class TransitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Action(TransitionModel):
    action_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
    amount: PositiveMoney
    fee: Money
    outcome: Literal["APPLIED", "UNSETTLED", "RESTRICTED", "IN_FLIGHT", "FAILED", "UNKNOWN"]
    assumption: Annotated[str, Field(min_length=1, max_length=500)]


class Sell(Action):
    kind: Literal["SELL_TO_CASH"]
    position_ref: Ref


class Buy(Action):
    kind: Literal["BUY_WITH_CASH"]
    cash_ref: Ref
    symbol: Ref


class Transfer(Action):
    kind: Literal["TRANSFER_CASH"]
    cash_ref: Ref
    destination_account: Ref
    destination_currency: Annotated[str, Field(pattern=r"^[A-Z]{3,5}$")]
    # Explicit receiving units per source unit. Frozen valuation rates are
    # separate. Their difference from this quote is the FX gain/loss.
    destination_per_source: PositiveMoney


class Repay(Action):
    kind: Literal["REPAY_DEBT_FROM_CASH"]
    loan_ref: Ref
    cash_ref: Ref | None = None
    external_cashflow: Literal["SALARY_CASHFLOW_REPAY"] | None = None

    @model_validator(mode="after")
    def funding_source(self):
        if (self.cash_ref is None) == (self.external_cashflow is None):
            raise ValueError("EXACTLY_ONE_REPAYMENT_SOURCE_REQUIRED")
        return self


TransitionAction = Annotated[Sell | Buy | Transfer | Repay, Field(discriminator="kind")]


class TransitionStage(TransitionModel):
    label: Annotated[str, Field(min_length=1, max_length=80)]
    actions: list[TransitionAction] = Field(min_length=1, max_length=12)


class TransitionRequest(TransitionModel):
    snapshot_id: Annotated[str, Field(pattern=r"^asset-view:v1:[1-9][0-9]{0,17}$")]
    snapshot_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    policy_bundle_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    paths: dict[Literal["A", "B"], list[TransitionStage]]
    assumed_denominator_cny: PositiveMoney | None = None
    assumed_amounts_cny: dict[Ref, Money] = Field(default_factory=dict, max_length=100)
    assumed_loan_principal_cny: dict[Ref, Money] = Field(default_factory=dict, max_length=30)
    assumed_fx_to_cny: dict[str, PositiveMoney] = Field(default_factory=dict, max_length=12)

    @model_validator(mode="after")
    def bounded_paths(self):
        if "A" not in self.paths or any(not 1 <= len(stages) <= 8 for stages in self.paths.values()):
            raise ValueError("SCENARIO_PATH_LENGTH_INVALID")
        for stages in self.paths.values():
            ids = [action.action_id for stage in stages for action in stage.actions]
            if len(ids) != len(set(ids)):
                raise ValueError("SCENARIO_ACTION_ID_DUPLICATE")
        return self


@dataclass(frozen=True)
class ScenarioHolding:
    source_ref: str
    amount_cny: Decimal
    account: str | None
    currency: str
    identity: dict
    economic: str | None
    evidence: tuple[str, ...]
    original_amount_cny: Decimal | None = None


@dataclass(frozen=True)
class CashLot:
    account: str | None
    currency: str
    amount: Decimal | None
    rate: Decimal | None
    included: bool
    state: CashState
    pending_account: str | None = None
    pending_currency: str | None = None
    frozen_cny: Decimal | None = None
    availability_basis: Literal["SCENARIO_ACTION_OUTCOME", "ASSUMED_FROM_FROZEN_BALANCE", "UNKNOWN"] = "SCENARIO_ACTION_OUTCOME"

    @property
    def cny(self):
        return self.frozen_cny if self.frozen_cny is not None else self.amount * self.rate


TRANSITION_REJECTION_CODES = {
    "SCENARIO_ASSUMED_ASSET_NOT_FOUND",
    "SCENARIO_ASSUMED_ASSET_NOT_UNCLASSIFIED_POSITION",
    "SCENARIO_ASSUMED_DENOMINATOR_INVALID",
    "SCENARIO_ASSUMED_FX_CONFLICT",
    "SCENARIO_ASSUMED_LOAN_CONFLICT",
    "SCENARIO_ASSUMED_LOAN_NOT_FOUND",
    "SCENARIO_ASSUMPTIONS_REQUIRE_UNQUALIFIED_BASELINE",
    "SCENARIO_BASELINE_NOT_CLOSED",
    "SCENARIO_BUY_IDENTITY_OR_CASH_INVALID",
    "SCENARIO_INSUFFICIENT_CASH",
    "SCENARIO_INVESTMENT_CONSERVATION_FAILED",
    "SCENARIO_LOAN_TOTAL_NOT_CLOSED",
    "SCENARIO_NONPOSITIVE_DENOMINATOR",
    "SCENARIO_POSITION_UNAVAILABLE",
    "SCENARIO_REPAYMENT_EXCEEDS_OR_LACKS_DEBT",
    "SCENARIO_REPAYMENT_REQUIRES_APPLIED_CNY",
    "SCENARIO_SALE_AMOUNT_OR_FX_INVALID",
    "SCENARIO_SALE_OUTCOME_INVALID",
    "SCENARIO_SAME_CURRENCY_FX_INVALID",
    "SCENARIO_SNAPSHOT_BINDING_MISMATCH",
    "SCENARIO_TRANSFER_DESTINATION_OR_FX_INVALID",
    "SCENARIO_TRANSFER_SOURCE_INVALID",
}


class TransitionRejected(ValueError):
    """Expected scenario-domain rejection. Never signals a real operation."""


def simulate_transition(request, *, holdings, cash, loans, total_debt, net_assets,
                        denominator, rates, accounts, instruments, evaluate, archive_stage_holdings=False):
    """Evaluate independent paths from one immutable baseline. All money is Decimal."""
    with localcontext() as ctx:
        ctx.prec = 60
        return _simulate_transition(request, holdings, cash, loans, total_debt, net_assets,
                                    denominator, rates, accounts, instruments, evaluate, archive_stage_holdings)


def _simulate_transition(request, holdings, cash, loans, total_debt, net_assets,
                         denominator, rates, accounts, instruments, evaluate, archive_stage_holdings):
    def investable(securities, balances):
        return (sum((row.amount_cny for row in securities.values()), ZERO)
                + sum((lot.cny for lot in balances.values() if lot.included), ZERO))

    visible = investable(holdings, cash)
    if denominator is not None and denominator != visible:
        raise TransitionRejected("SCENARIO_BASELINE_NOT_CLOSED")
    assumed = request.assumed_denominator_cny
    if assumed is not None and (denominator is not None or assumed < visible):
        raise TransitionRejected("SCENARIO_ASSUMED_DENOMINATOR_INVALID")
    baseline = denominator if denominator is not None else assumed
    paths = {}
    for name, stages in request.paths.items():
        securities, balances, debts = dict(holdings), dict(cash), dict(loans)
        debt, nw, costs, fx = total_debt, net_assets, ZERO, ZERO
        prior_hits = {}
        output = []
        for stage in [None, *stages]:
            events = []
            opening = investable(securities, balances)
            for action in stage.actions if stage else ():
                event = _transition_action(action, securities, balances, debts, rates, accounts, instruments)
                events.append(event)
                debt = debt - event["principal_repaid_cny"] if debt is not None else None
                if nw is not None:
                    nw += event["fx_effect_cny"] - event["cost_cny"] + event["external_contribution_cny"]
                costs += event["cost_cny"]
                fx += event["fx_effect_cny"]
            current = investable(securities, balances)
            if current != opening + sum((event["investment_change_cny"] for event in events), ZERO):
                raise TransitionRejected("SCENARIO_INVESTMENT_CONSERVATION_FAILED")
            projected = baseline + current - visible if baseline is not None else None
            if projected is not None and projected <= 0:
                raise TransitionRejected("SCENARIO_NONPOSITIVE_DENOMINATOR")
            allocation, concentration = evaluate(securities, balances, projected, denominator is not None)
            severity = {"WARNING": 1, "HUMAN_REVIEW": 2}
            hits = {issuer: severity.get(result.get("scenario_risk_status"), 0)
                    for issuer, result in concentration.get("issuers", {}).items()}
            output.append({
                "label": stage.label if stage else "S0", "investment_assets_cny": projected,
                "visible_investment_assets_cny": current, "liabilities_cny": debt, "loan_balances_cny": dict(debts),
                "known_principal_repaid_cny": sum(loans.values(), ZERO) - sum(debts.values(), ZERO),
                "net_assets_cny": nw, "cash": {ref: vars(lot) for ref, lot in balances.items() if lot.cny},
                "allocation": allocation, "concentration": concentration,
                "newly_breached_issuers": sorted(issuer for issuer, level in hits.items()
                                                 if level > prior_hits.get(issuer, 0)) if stage else [],
                "cash_flow_conservation_verified": True,
                "cumulative_cost_cny": costs, "cumulative_fx_effect_cny": fx, "actions": events,
            })
            if archive_stage_holdings:
                output[-1]["scenario_holdings"] = [
                    {"source_ref": ref, "account_ref": row.account,
                     "symbol": row.identity.get("security_symbol") or row.identity.get("symbol"),
                     "currency": row.currency, "amount_cny": row.amount_cny, "identity_evidence": row.evidence}
                    for ref, row in securities.items() if row.amount_cny
                ]
            prior_hits = hits
        paths[name] = output
    return paths


def _transition_action(action, securities, balances, debts, rates, accounts, instruments):
    result = {"action_id": action.action_id, "kind": action.kind, "assumption": action.assumption,
              "outcome": action.outcome, "cash_state": action.outcome, "cost_cny": ZERO,
              "fx_effect_cny": ZERO, "investment_change_cny": ZERO, "principal_repaid_cny": ZERO, "external_contribution_cny": ZERO,
              "inputs": action.model_dump(mode="json"), "net_cny": None, "blocked_reason": None}
    if action.outcome in {"FAILED", "UNKNOWN"}:
        result["blocked_reason"] = "SCENARIO_ACTION_" + action.outcome
        return result
    fee = action.fee
    amount = action.amount
    cash_id = "action:" + action.action_id
    if isinstance(action, Sell):
        holding = securities.get(action.position_ref)
        if holding is None or holding.account not in accounts:
            raise TransitionRejected("SCENARIO_POSITION_UNAVAILABLE")
        rate = rates.get(holding.currency)
        if rate is None or fee >= amount or amount * rate > holding.amount_cny:
            raise TransitionRejected("SCENARIO_SALE_AMOUNT_OR_FX_INVALID")
        states = {"APPLIED": "AVAILABLE", "UNSETTLED": "UNSETTLED", "RESTRICTED": "SETTLED_RESTRICTED"}
        if action.outcome not in states:
            raise TransitionRejected("SCENARIO_SALE_OUTCOME_INVALID")
        securities[action.position_ref] = replace(holding, amount_cny=holding.amount_cny - amount * rate)
        balances[cash_id] = CashLot(holding.account, holding.currency, amount - fee, rate,
                                    True, states[action.outcome])
        result.update(cost_cny=fee * rate, investment_change_cny=-fee * rate,
                      net_cny=(amount - fee) * rate, cash_state=states[action.outcome])
        return result

    external = isinstance(action, Repay) and action.external_cashflow is not None
    lot = None if external else balances.get(action.cash_ref)
    completing = (isinstance(action, Transfer) and lot is not None
                  and lot.state in {"UNSETTLED", "SETTLED_RESTRICTED", "TRANSFER_IN_FLIGHT"}
                  and action.outcome == "APPLIED" and amount == lot.amount and fee == 0
                  and (action.destination_account, action.destination_currency) ==
                      (lot.pending_account or lot.account, lot.pending_currency or lot.currency))
    if not external and (lot is None or (lot.state != "AVAILABLE" and not completing)):
        result.update(cash_state=lot.state if lot else "UNKNOWN", blocked_reason="SCENARIO_CASH_NOT_AVAILABLE")
        return result
    if lot is not None and (amount + fee > lot.amount or (amount + fee) * lot.rate > lot.cny):
        raise TransitionRejected("SCENARIO_INSUFFICIENT_CASH")
    rate = lot.rate if lot else Decimal(1)  # External payroll assumption is CNY.
    if isinstance(action, Buy):
        instrument = instruments.get(action.symbol)
        if action.outcome != "APPLIED" or lot.currency != "USD" or not lot.included or lot.account not in accounts or instrument is None:
            raise TransitionRejected("SCENARIO_BUY_IDENTITY_OR_CASH_INVALID")
        row, economic, evidence = instrument
        securities[cash_id] = ScenarioHolding(cash_id, amount * rate, lot.account, lot.currency,
                                               row, economic, evidence)
        result["cash_state"] = "APPLIED_TO_PURCHASE"
    elif isinstance(action, Transfer):
        destination_rate = rates.get(action.destination_currency)
        if action.destination_currency == lot.currency and action.destination_per_source != 1:
            raise TransitionRejected("SCENARIO_SAME_CURRENCY_FX_INVALID")
        if (action.outcome not in {"APPLIED", "IN_FLIGHT", "RESTRICTED"} or destination_rate is None
                or action.destination_account not in accounts
                or ((action.destination_account, action.destination_currency) == (lot.account, lot.currency) and not completing)):
            raise TransitionRejected("SCENARIO_TRANSFER_DESTINATION_OR_FX_INVALID")
        if lot.account not in accounts or not lot.included:
            raise TransitionRejected("SCENARIO_TRANSFER_SOURCE_INVALID")
        if action.outcome == "IN_FLIGHT":
            # Keep source currency while in flight. No conversion proceeds
            # or destination balance exists until explicit simulated arrival.
            balances[cash_id] = replace(lot, amount=amount, state="TRANSFER_IN_FLIGHT",
                                         pending_account=action.destination_account,
                                         pending_currency=action.destination_currency, frozen_cny=None,
                                         availability_basis="SCENARIO_ACTION_OUTCOME")
            result["cash_state"] = "TRANSFER_IN_FLIGHT"
        else:
            converted = amount * action.destination_per_source
            state = "AVAILABLE" if action.outcome == "APPLIED" else "SETTLED_RESTRICTED"
            balances[cash_id] = CashLot(action.destination_account, action.destination_currency, converted,
                                        destination_rate, lot.included, state)
            result.update(cash_state=state, fx_effect_cny=converted * destination_rate - amount * rate)
    else:
        if action.outcome != "APPLIED" or (lot and lot.currency != "CNY"):
            raise TransitionRejected("SCENARIO_REPAYMENT_REQUIRES_APPLIED_CNY")
        if action.loan_ref not in debts or amount > debts[action.loan_ref]:
            raise TransitionRejected("SCENARIO_REPAYMENT_EXCEEDS_OR_LACKS_DEBT")
        debts[action.loan_ref] -= amount
        result.update(principal_repaid_cny=amount, cash_state="APPLIED_TO_REPAYMENT",
                      external_contribution_cny=amount + fee if external else ZERO)

    if lot:
        balances[action.cash_ref] = replace(lot, amount=lot.amount - amount - fee,
                                            frozen_cny=lot.cny - (amount + fee) * lot.rate)
    investment_change = ZERO
    if lot and lot.included:
        investment_change = result["fx_effect_cny"] - fee * rate - result["principal_repaid_cny"]
    result.update(cost_cny=fee * rate, investment_change_cny=investment_change,
                  net_cny=amount * rate + result["fx_effect_cny"])
    return result
