"""S3-A deterministic company comparison over the original qualified assessment.

No collection, TTM reconstruction, portfolio access, LLM or trading authority.
Decimal outputs are strings so frozen calculations survive JSON round trips.
"""
from copy import deepcopy
from decimal import Decimal, localcontext

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.services.market_data.evidence import stamp
from backend.services.market_data.financial_research import build

METHOD_VERSION = "s3-a.earnings-scenarios.v1"
SCENARIOS = ("cautious", "base", "optimistic")
GROUPS = ("优先考虑", "继续观察", "暂不优先")
D = Decimal


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scenario(Contract):
    earnings_growth: Decimal = Field(ge=-.5, le=1)
    annual_share_change: Decimal = Field(ge=-.1, le=.2)
    terminal_pe: Decimal = Field(gt=0, le=100)
    dividend_payout: Decimal = Field(ge=0, le=1)


class Assumptions(Contract):
    basis: str = Field(pattern=r"^S3_RESEARCH_ASSUMPTION_NOT_POLICY$")
    rationale: str = Field(min_length=10, max_length=3000)
    method: str = Field(default="earnings_exit_multiple", pattern="^earnings_exit_multiple$")
    required_return: Decimal = Field(gt=0, le=.5)
    cautious: Scenario
    base: Scenario
    optimistic: Scenario

    @model_validator(mode="after")
    def ordered(self):
        if not self.cautious.earnings_growth <= self.base.earnings_growth <= self.optimistic.earnings_growth:
            raise ValueError("SCENARIO_GROWTH_ORDER")
        return self


class ResearchRequest(Contract):
    pool_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    pool_version: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    purpose: str = Field(min_length=1, max_length=1000)
    decision_at: AwareDatetime
    source_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    companies: dict[str, Assumptions] = Field(min_length=1, max_length=20)
    tie_band: Decimal = Field(default=D(".02"), ge=0, le=.1)
    downside_tie_band: Decimal = Field(default=D(".05"), ge=0, le=.2)
    downside_floor: Decimal = Field(default=D("-.1"), ge=-1, le=0)

    @model_validator(mode="after")
    def symbols(self):
        import re
        if not all(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}:US", s) for s in self.companies):
            raise ValueError("S3_A_SUPPORTED_IDENTITY_REQUIRED")
        return self


def dec(value):
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_NOT_A_FINANCIAL_NUMBER")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_INPUT")
    return result


def text(value):
    return format(value.quantize(D(".00000001")), "f")


def scenario(eps, price, parameters, required_return):
    """Five-year aggregate earnings / relative share index, then terminal PE.

    Dividends are held as non-interest-bearing cash. Buybacks have no separate
    return term; they affect only the explicitly assumed relative share index.
    This is a conditional equity-value estimate, not enterprise DCF.
    """
    p = Scenario.model_validate(parameters)
    with localcontext() as ctx:
        ctx.prec = 34
        eps, price, rate = dec(eps), dec(price), dec(required_return)
        if eps <= 0 or price <= 0 or rate <= 0:
            raise ValueError("POSITIVE_LISTED_EPS_PRICE_AND_RETURN_REQUIRED")
        cash = D(0)
        pv = D(0)
        years = []
        for year in range(1, 6):
            growth = (1 + p.earnings_growth) ** year
            shares = (1 + p.annual_share_change) ** year
            projected = eps * growth / shares
            dividend = projected * p.dividend_payout
            cash += dividend
            pv += dividend / (1 + rate) ** year
            years.append(dict(year=year, earnings_index=text(growth), relative_shares=text(shares),
                              eps=text(projected), dividend=text(dividend)))
        terminal = projected * p.terminal_pe
        pv += terminal / (1 + rate) ** 5
        wealth = terminal + cash
        annualized = (wealth / price) ** (D(1) / 5) - 1
        implied_eps = price * (1 + rate) ** 5 / p.terminal_pe
        implied_growth = (implied_eps / eps) ** (D(1) / 5) * (1 + p.annual_share_change) - 1
        return dict(years=years, terminal_price=text(terminal), dividends_cash=text(cash),
                    terminal_wealth=text(wealth), total_return=text(wealth / price - 1),
                    annualized_return=text(annualized), conditional_value=text(pv),
                    margin_to_conditional_value=text(1 - price / pv),
                    implied_terminal_eps_no_dividends=text(implied_eps),
                    implied_earnings_growth_no_dividends=text(implied_growth))


def _facts(assessment, decision_at):
    """Reject incompatible qualified rows rather than guess a normalized basis."""
    rows = assessment.get("verified_field_records", []) + assessment.get("historical_field_records", [])
    result = []
    for row in rows:
        if row.get("quality") != "valid":
            continue
        field = row["field"]
        expected_basis = {"revenue": "total_revenue", "operating_income": "operating_income",
                          "operating_cash_flow": "operating_cash_flow", "capital_expenditures": "positive_capital_cash_outflow",
                          "net_income_attributable_parent": "net_income_attributable_parent",
                          "diluted_eps_ordinary": "diluted_eps_ordinary"}
        stem = field.removesuffix("_quarter").removesuffix("_annual")
        if stem not in expected_basis:
            continue  # Unsupported fields stay in the frozen input, never become S3 facts.
        if row.get("accounting_basis") != expected_basis[stem]:
            raise ValueError("FIELD_ACCOUNTING_BASIS_CONFLICT")
        expected_unit = "currency_per_ordinary_share" if field.startswith("diluted_eps_ordinary") else "currency_units"
        if (row.get("symbol") != assessment["symbol"] or row.get("currency") != "USD"
                or row.get("unit") != expected_unit or row.get("is_seed") or row.get("is_mock")):
            raise ValueError("FIELD_IDENTITY_CURRENCY_OR_UNIT_CONFLICT")
        start, end = stamp(row.get("fiscal_period_start")), stamp(row.get("period"))
        if not start or not end or start >= end or end > decision_at:
            raise ValueError("FIELD_PERIOD_CONFLICT")
        for clock in (row.get("published_at"), row.get("fetched_at")):
            if not stamp(clock) or stamp(clock) > decision_at:
                raise ValueError("FIELD_CLOCK_UNKNOWN_OR_FUTURE")
        if not row.get("publication_links"):
            raise ValueError("FIELD_SOURCE_CLOSURE_MISSING")
        for link in row["publication_links"]:
            if not link.get("source_document_sha256") or not link.get("source_url"):
                raise ValueError("FIELD_RAW_SOURCE_REFERENCE_MISSING")
            for key in ("published_at", "fetched_at"):
                if not stamp(link.get(key)) or stamp(link[key]) > decision_at:
                    raise ValueError("FIELD_LINK_CLOCK_UNKNOWN_OR_FUTURE")
        dec(row["values"][field])
        result.append(row)
    return result


def _series(rows, field):
    groups = {}
    for row in rows:
        if row["field"] == field:
            groups.setdefault(row["period"], []).append(row)
    output = []
    for period, aliases in sorted(groups.items()):
        identity = {(dec(r["values"][field]), r["fiscal_period_start"], r["accounting_basis"]) for r in aliases}
        if len(identity) != 1:
            raise ValueError("SAME_FIELD_SOURCE_CONFLICT")
        output.append(dict(period=period, start=aliases[0]["fiscal_period_start"],
                           value=text(dec(aliases[0]["values"][field])),
                           evidence_refs=sorted(r["record_id"] for r in aliases)))
    return output


def _quality(rows, metrics):
    series = {field: _series(rows, field) for field in (
        "revenue_annual", "operating_income_annual", "revenue_quarter", "operating_income_quarter",
        "net_income_attributable_parent_annual", "operating_cash_flow_annual", "capital_expenditures_annual")}
    margins, fcf = [], []
    for numerator, target, is_cash in (("operating_income_annual", margins, False),
                                      ("operating_cash_flow_annual", fcf, True)):
        other = {r["period"]: r for r in series["capital_expenditures_annual" if is_cash else "revenue_annual"]}
        for row in series[numerator]:
            denominator = other.get(row["period"])
            if not denominator or row["start"] != denominator["start"] or dec(denominator["value"]) <= 0:
                continue
            value = dec(row["value"]) - dec(denominator["value"]) if is_cash else dec(row["value"]) / dec(denominator["value"])
            target.append(dict(period=row["period"], value=text(value),
                               evidence_refs=row["evidence_refs"] + denominator["evidence_refs"]))
    revenues = series["revenue_annual"]
    growth = [dict(period=b["period"], prior_period=a["period"], value=text(dec(b["value"]) / dec(a["value"]) - 1),
                   evidence_refs=a["evidence_refs"] + b["evidence_refs"])
              for a, b in zip(revenues, revenues[1:]) if dec(a["value"]) > 0
              and 330 <= (stamp(b["period"]) - stamp(a["period"])).days <= 400]
    core_status = "警示" if any(dec(g["value"]) < 0 for g in growth) else "暂无法判断"
    return dict(
        floors={
            "data_trust": dict(status="通过" if metrics.get("diluted_eps_ttm") and metrics.get("observed_price") else "暂无法判断",
                               reason="Owner-qualified facts retain provenance; missing EPS or price prevents a complete valuation input chain."),
            "financial_sustainability": dict(status="暂无法判断", reason="Qualified balance sheet, liquidity and debt/restricted-cash closure unavailable."),
            "profit_cash_support": dict(status="暂无法判断", reason="Historical annual CFO/CapEx shown where comparable; current cash/one-offs/SBC reconciliation incomplete."),
            "core_business_change": dict(status=core_status, reason="Historical growth and operating margins are observations; durable advantage and causal recovery evidence remain unverified."),
        },
        evidence_catalog={r["record_id"]: {k: r.get(k) for k in (
            "field", "values", "period", "fiscal_period_start", "currency", "unit", "accounting_basis",
            "published_at", "fetched_at", "publication_links", "historical_only")} for r in rows},
        gaap_series=series, historical_revenue_growth=growth, historical_operating_margins=margins,
        historical_fcf=fcf, current_operating_margin=metrics.get("operating_margin_pct"),
        normalization=dict(status="UNKNOWN", adjustments=[], reason="No verified one-off, working-capital or SBC adjustments; GAAP not silently normalized."),
        capital_efficiency={k: dict(status="UNKNOWN", reason=v) for k, v in {
            "roe": "Average equity, repurchase effects and attributable earnings bridge missing.",
            "roic": "Average invested capital, NOPAT, leases and R&D treatment missing.",
            "sbc": "Qualified SBC/dilution reconciliation missing; scenario share change is an assumption.",
            "net_cash": "Cash restrictions, debt and equity attribution missing; no EV bridge."}.items()},
        priority_blockers=["FINANCIAL_SUSTAINABILITY_UNKNOWN", "CURRENT_CASH_AND_NORMALIZATION_UNKNOWN", "DURABLE_BUSINESS_EVIDENCE_UNKNOWN"])


def company(assessment, assumptions, decision_at):
    at = stamp(decision_at)
    if not at or stamp(assessment.get("as_of")) != at:
        raise ValueError("COMMON_DECISION_CLOCK_REQUIRED")
    if assessment.get("conflicts") or assessment.get("source_conflicts"):
        raise ValueError("UNRESOLVED_SOURCE_CONFLICT")
    rows = _facts(assessment, at)
    derived = build(assessment)  # Original owner performs all TTM/EPS/price arithmetic.
    metrics = {r["field"]: r for r in derived["records"]}
    quality = _quality(rows, metrics)
    card = dict(symbol=assessment["symbol"], status="partial", quality=quality,
                cross_checks={k: metrics.get(k) for k in ("diluted_eps_ttm", "observed_price", "pe_ttm", "earnings_yield_pct")},
                assumptions=assumptions.model_dump(mode="json"), scenarios={}, sensitivity=[],
                limitations=sorted(set(derived["limitations"] + ["EARNINGS_SCENARIOS_NOT_INTRINSIC_DCF", "NO_VERIFIED_GUIDANCE_OR_SCENARIO_PROBABILITIES", "CORPORATE_ACTION_IR_WEB_BYTES_NOT_FROZEN"])),
                change_conditions=["Resolve balance-sheet and current cash evidence before priority promotion.",
                                   "Recompute on changed operating growth, dilution, payout or terminal assumptions.",
                                   "Verify durable business evidence and changes in qualified filings."],
                configuration_priority=dict(status="UNKNOWN", reason="NO_QUALIFIED_HOLDINGS_OR_EFFECTIVE_POLICY_ATTACHED"),
                execution_authorized=False)
    eps, price = metrics.get("diluted_eps_ttm"), metrics.get("observed_price")
    if not eps or not price:
        card["valuation_status"] = "UNKNOWN"
        return card
    if (eps.get("currency") != "USD" or price.get("currency") != "USD"
            or eps.get("unit") != "currency_per_listed_unit" or eps["details"]["share_basis"] != "diluted_eps_ordinary_quarter"):
        raise ValueError("LISTED_SHARE_OR_PRICE_CURRENCY_CONFLICT")
    if dec(eps["values"]["diluted_eps_ttm"]) <= 0:
        card["valuation_status"] = "NOT_APPLICABLE_NONPOSITIVE_EPS"
        return card
    e, p = eps["values"]["diluted_eps_ttm"], price["values"]["observed_price"]
    card["valuation_status"] = "CONDITIONAL_RESEARCH"
    card["formula"] = "EPS[t]=EPS0*(1+aggregate_earnings_growth)^t/(1+share_change)^t; dividend[t]=EPS[t]*payout; terminal=EPS[5]*PE; value=sum(dividend[t]/(1+r)^t)+terminal/(1+r)^5; CAGR=((terminal+cash_dividends)/price)^(1/5)-1"
    card["scenarios"] = {name: scenario(e, p, getattr(assumptions, name), assumptions.required_return) for name in SCENARIOS}
    for field, offsets in (("earnings_growth", (D("-.02"), D(".02"))),
                           ("terminal_pe", (D("-.2"), D(".2"))),
                           ("annual_share_change", (D("-.01"), D(".01"))),
                           ("required_return", (D("-.02"), D(".02")))):
        for delta in offsets:
            params = assumptions.base.model_dump()
            rate = assumptions.required_return
            if field == "required_return":
                rate += delta
            elif field == "terminal_pe":
                params[field] *= 1 + delta
            else:
                params[field] += delta
            try:
                value = scenario(e, p, params, rate)
            except ValueError:
                value = None  # Out-of-contract sensitivities never produce guessed values.
            card["sensitivity"].append(dict(parameter=field, delta=str(delta), parameters=Scenario.model_validate(params).model_dump(mode="json") if value else None,
                                            required_return=str(rate), result=value))
    values = [dec(v["conditional_value"]) for v in card["scenarios"].values()]
    card["conditional_value_range"] = [text(min(values)), text(max(values))]
    returns = [dec(v["result"]["annualized_return"]) for v in card["sensitivity"] if v["result"]]
    card["high_sensitivity"] = bool(returns and max(returns) - min(returns) > D(".05"))
    return card


def _rank(cards, request):
    """Ordered gates, then return/risk; never add correlated quality scores."""
    for card in cards:
        scenarios = card.get("scenarios", {})
        if not scenarios:
            card.update(group=GROUPS[2], rank=None, rank_reason="No applicable qualified valuation; retain company and missing reasons.")
            continue
        base = dec(scenarios["base"]["annualized_return"])
        cautious = dec(scenarios["cautious"]["total_return"])
        blockers = card["quality"]["priority_blockers"]
        required_return = dec(card["assumptions"]["required_return"])
        if base < required_return or cautious < request.downside_floor:
            group, reason = GROUPS[2], "Conditional return or cautious downside fails disclosed research thresholds; quality gaps remain visible."
        elif blockers or card.get("high_sensitivity"):
            group, reason = GROUPS[1], "Unresolved quality evidence blocks priority despite conditional valuation."
        else:
            group, reason = GROUPS[0], "All quality gates resolved; base return and cautious downside meet disclosed research thresholds."
        card.update(group=group, rank=None, rank_reason=reason)
    for group in GROUPS:
        ordered = sorted((c for c in cards if c["group"] == group and c.get("scenarios")),
                         key=lambda c: (-dec(c["scenarios"]["base"]["annualized_return"]),
                                        -dec(c["scenarios"]["cautious"]["total_return"]), c["symbol"]))
        anchor, downside_anchor, evidence_anchor, position = None, None, None, 0
        for i, card in enumerate(ordered, 1):
            value = dec(card["scenarios"]["base"]["annualized_return"])
            downside = dec(card["scenarios"]["cautious"]["total_return"])
            evidence = tuple(v["status"] for v in card["quality"]["floors"].values())
            if (anchor is None or anchor - value > request.tie_band
                    or abs(downside_anchor - downside) > request.downside_tie_band or evidence != evidence_anchor):
                anchor, downside_anchor, evidence_anchor, position = value, downside, evidence, i
            card["rank"] = position
            card["tie_reason"] = "Same evidence states, base return within tie band and cautious return within downside tie band share rank; symbol only stabilizes display."
    return cards


def compare(request, assessments):
    request = ResearchRequest.model_validate(request)
    cards = []
    with localcontext() as ctx:
        ctx.prec = 34
        for symbol, params in sorted(request.companies.items()):
            try:
                assessment = assessments[symbol]
                if assessment.get("symbol") != symbol:
                    raise ValueError("COMPANY_IDENTITY_CONFLICT")
                cards.append(company(assessment, params, request.decision_at))
            except (ValueError, KeyError, TypeError, ArithmeticError) as error:
                # A bounded category only; neither original payload nor exception is exposed.
                reason = str(error) if type(error) is ValueError and str(error).isupper() and str(error).replace("_", "").isalnum() else "OWNER_INPUT_INVALID"
                cards.append(dict(symbol=symbol, status="unavailable", valuation_status="UNKNOWN", scenarios={},
                                  missing=["COMPANY_INPUT_REJECTED_OR_MISSING"], failure_category=type(error).__name__,
                                  failure_reason=reason,
                                  execution_authorized=False, configuration_priority=dict(status="UNKNOWN")))
        _rank(cards, request)
        baseline = {c["symbol"]: [c["group"], c["rank"]] for c in cards}
        changes = []
        for card in cards:
            for i, variant in enumerate(card.get("sensitivity", [])):
                if not variant["result"]:
                    continue
                others = deepcopy(cards)
                changed = next(c for c in others if c["symbol"] == card["symbol"])
                changed["scenarios"]["base"] = variant["result"]
                changed["assumptions"]["required_return"] = variant["required_return"]
                _rank(others, request)
                after = {c["symbol"]: [c["group"], c["rank"]] for c in others}
                if after != baseline:
                    changes.append(dict(changed_symbol=card["symbol"], sensitivity_index=i,
                                        parameter=variant["parameter"], delta=variant["delta"], after=after))
        for card in cards:
            card["ranking_unstable"] = bool(changes)
            if card.get("sensitivity"):
                returns = [dec(v["result"]["annualized_return"]) for v in card["sensitivity"] if v["result"]]
                card["high_sensitivity"] = bool(returns and max(returns) - min(returns) > D(".05"))
        return dict(schema_version="wealthpilot.s3-a.v1", method_version=METHOD_VERSION,
                    decision_at=request.decision_at.isoformat(), cards=cards,
                    ranking=dict(groups=list(GROUPS), tie_band=str(request.tie_band), downside_floor=str(request.downside_floor),
                                 downside_tie_band=str(request.downside_tie_band),
                                 changes=changes, stable=not changes,
                                 rule="Return/downside thresholds and quality gates; within group base return, cautious downside and matching evidence tie bands; no LLM scores."),
                    status="partial", execution_authorized=False, order_created=False,
                    configuration_priority=dict(status="UNKNOWN", reason="COMPANY_RESEARCH_ONLY"))
