"""Pure scenario calculations for AllocationService over a single U1 view.

Not an asset/loan ledger or trading planner. Nothing is persisted or synchronized.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IssuerTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    issuer_id: str = Field(min_length=1,max_length=80)
    target_weight: float = Field(ge=0,lt=1)
    tolerance: float = Field(default=0,ge=0,le=.2)
    direction: Literal["concentration_control","gradual_exit"] = "concentration_control"
    phases: list[float] = Field(default_factory=list,max_length=8)


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
    return {**target.model_dump(mode="json"),"digest":digest,"basis":"user_confirmed_analysis_targets"}


def target_context(view, issuer_id, versions, formal_rules, defaults, *, at=None):
    target = select_targets(versions,at=at)
    group = next((g for g in (view or {}).get("issuer_exposure",{}).get("companies",[]) if g["issuer_id"]==issuer_id),None)
    warnings=[]
    formal = formal_rules if isinstance(formal_rules,dict) else {}
    if target:
        cash_min = formal.get("min_cash_pct")
        if cash_min is not None and target.get("investment_cash_max") is not None and cash_min>target["investment_cash_max"]:
            warnings.append("TARGET_DISCIPLINE_CASH_CONFLICT")
        effective=defaults.get("effective_rules",{}) if isinstance(defaults,dict) else {}
        if (not formal and target.get("investment_cash_max") is not None
                and effective.get("min_cash_pct",0)>target["investment_cash_max"]):
            warnings.append("DEFAULT_OR_UNVERIFIED_POLICY_TARGET_CASH_DIFFERENCE")
    return dict(snapshot_id=(view or {}).get("snapshot_id"),issuer_id=issuer_id,
        company_exposure=group,denominator_cny=(view or {}).get("issuer_exposure",{}).get("investment_denominator_cny"),
        formal_discipline=formal_rules,confirmed_stage_targets=target,system_defaults=defaults,
        warnings=warnings,authority="Formal discipline is not overwritten; stage targets are analysis preferences, not forced sale or trading authorization.")


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
                warnings.append("FUNDING_DESTINATION_DIFFERS_FROM_CONFIRMED_POLICY")
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
