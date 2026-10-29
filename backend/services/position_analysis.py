"""4.2 composition inside original PEER: reuse evidence Skill and AllocationService.

This module owns no data/provider, model, persistence, order or sync operations.
"""
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class ResearchIntent(BaseModel):
    """User assumptions only, never holdings/target confirmation or order input."""
    model_config=ConfigDict(extra='forbid')
    objective: Literal['hold','reduce','exit','evaluate'] | None=None
    review_date: str | None=Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    reference_quantity: float | None=Field(default=None,gt=0,allow_inf_nan=False)
    tranche_fractions: list[float]=Field(default_factory=list,max_length=8)
    historical_price_bands: list[tuple[float,float]]=Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def valid_assumptions(self):
        import math
        from datetime import date
        if self.review_date:date.fromisoformat(self.review_date)
        if any(not math.isfinite(v) or not 0<v<=1 for v in self.tranche_fractions) or sum(self.tranche_fractions)>1+1e-9:
            raise ValueError('RESEARCH_FRACTIONS_INVALID')
        if any(not all(math.isfinite(v) for v in pair) or not 0<pair[0]<=pair[1] for pair in self.historical_price_bands):
            raise ValueError('RESEARCH_OBSERVATION_ASSUMPTION_INVALID')
        return self


def prepare_portfolio(plan, executed, *, at=None):
    """Reuse the same evidence Skill over stored portfolio research, no fetching."""
    from backend.skills import invoke_skill
    from backend.services.market_data.evidence import mapping
    records=[mapping(r) for r in getattr(executed.loaded_data,'research_evidence',[])]
    symbols=list(dict.fromkeys(r.get('symbol') for r in records if r.get('symbol')))[:30]
    return [invoke_skill('wp-assess-position-evidence',symbol=symbol,at=at,
        research=[r for r in records if r.get('symbol')==symbol],
        events=[r for r in records if r.get('symbol')==symbol and r.get('metadata',{}).get('evidence_kind')=='event'],
        planner=plan.planner_trace,
        principles_available=bool(getattr(executed.loaded_data,'retrieved_principles',[]))) for symbol in symbols]


def prepare(plan, executed, *, asset_view=None, options=None, at=None):
    from backend.skills import invoke_skill
    from backend.services.instruments.issuers import IDENTITIES
    from backend.services.market_data.evidence import mapping
    from backend.services.allocation_scenarios import target_context, qualification_view
    from backend.services.allocation_service import get_targets, simulate_exit_scenario
    from backend.services.technical_patterns.decision_integration import target_from_execution_output
    options=options or {}
    target=target_from_execution_output(executed)
    loaded=executed.loaded_data
    market=executed.market_data
    symbol=getattr(market,"symbol",None) or (target.requested_symbol if target else None)
    currency="HKD" if (symbol or "").endswith(":HK") else "USD"
    rows=(asset_view or {}).get("data_quality",{}).get("security_inputs",[])
    native=[r for r in rows if r.get("security_symbol")==symbol and r.get("currency")==currency]
    research=(getattr(loaded,"research_evidence",[]) + list(getattr(loaded,"retrieved_research_views",[]) or []))
    research=research or getattr(loaded,"research",[]) or []
    # Event-tagged entries are taken only from the original research context.
    events=[r for r in research if mapping(r).get("metadata",{}).get("evidence_kind")=="event"]
    assessment=invoke_skill("wp-assess-position-evidence",market_data=market,symbol=symbol,at=at,
        asset_view=qualification_view(asset_view,options.get("scenario_legs",[]),at=at) if asset_view else None,research=research,events=events,
        planner=plan.planner_trace,stored_price=native[0].get("current_price") if native else None,
        principles_available=bool(getattr(loaded,"retrieved_principles",[])))
    issuer_id=IDENTITIES.get((symbol,currency))
    effective_rules=mapping(getattr(loaded,"rules",{}))
    rule_source=getattr(loaded,"rules_provenance",None) or {"basis":"effective_rules_unverified"}
    formal_rules=effective_rules if rule_source.get("basis")=="configured_rule_file" else None
    context=target_context(asset_view or {"portfolio_id":plan.portfolio_id},issuer_id,options.get("target_versions",[]),formal_rules,
        dict(allocation=[t.model_dump(mode="json") for t in get_targets()],
             effective_rules=effective_rules,rule_source=rule_source),at=at)
    context["effective_rules_provenance"]=rule_source
    member_ids={m["id"] for m in (context.get("company_exposure") or {}).get("members",[])}
    context["security_inputs"]=[r for r in rows if r.get("id") in member_ids]
    assessment["allocation_context"]=context
    assessment['research_intent']=research_intent(options)
    if assessment['research_intent']['objective']=='evaluate' and 'objective' not in assessment['research_intent']['sources']:
        formal=context.get('confirmed_stage_targets') or {}
        goal=next((row for row in formal.get('issuers',[]) if row['issuer_id']==issuer_id),{})
        if goal.get('direction')=='gradual_exit':
            assessment['research_intent'].update(objective='exit',formal_target_confirmed=True)
            assessment['research_intent']['sources']['objective']='server_confirmed_stage_target:'+str(formal.get('version'))
    if not context["confirmed_stage_targets"]:
        blockers=["CONFIRMED_STAGE_TARGETS_REQUIRED"]
        if context["proposed_stage_targets"]:
            blockers.append("STAGE_TARGET_CONFIRMATION_UNVERIFIED")
        for level in ("L3","L4"):
            assessment["qualification"][level]["allowed"]=False
            assessment["qualification"][level]["blockers"]+=blockers
        assessment["issues"]+=blockers
        assessment["infoNeeded"]+=blockers
        assessment["confidence_ceiling"]=min(assessment["confidence_ceiling"],.6)
    if context["warnings"]:
        for level in ("L3","L4"):
            assessment["qualification"][level]["allowed"]=False
            assessment["qualification"][level]["blockers"]+=context["warnings"]
        assessment["issues"]+=context["warnings"]
        assessment["infoNeeded"]+=context["warnings"]
        assessment["confidence_ceiling"]=min(assessment["confidence_ceiling"],.6)
    if options.get("scenario_legs"):
        if not asset_view:
            raise ValueError("FIXED_ASSET_VIEW_REQUIRED")
        assessment["scenarios"]=simulate_exit_scenario(asset_view,options["scenario_legs"],
            amount_qualified=assessment["qualification"]["L2"]["allowed"],target=context.get("confirmed_stage_targets") or context["proposed_stage_targets"])
    elif asset_view:
        from backend.services.exit_candidates import build_candidates
        assessment["exit_candidates"]=build_candidates(asset_view,issuer_id,
            context.get("confirmed_stage_targets") or context.get("proposed_stage_targets"),
            amount_qualified=assessment["qualification"]["L2"]["allowed"])
        auto_legs=[leg for c in assessment['exit_candidates'] for leg in c['legs']]
        if any(leg['destination']=='repay_cny_loan' for leg in auto_legs):
            loan_view=qualification_view(asset_view,auto_legs,at=at)
            loan_b=loan_view.get('data_quality',{}).get('analysis_eligibility',{}).get('B',{})
            if not loan_b.get('allowed',False):
                blocker='NET_WORTH_LOAN_AMOUNT_QUALIFICATION_REQUIRED'
                for level in ('L2','L3','L4'):
                    assessment['qualification'][level]['allowed']=False
                    assessment['qualification'][level]['blockers'].append(blocker)
                assessment['issues'].append(blocker)
                assessment['infoNeeded'].append(blocker)
                assessment['confidence_ceiling']=min(assessment['confidence_ceiling'],.35)
                for candidate in assessment['exit_candidates']:
                    for row in candidate['tranches']:
                        row['amount_cny']=None
                    if candidate['scenario']:
                        candidate['scenario']['mode']='illustrative_unqualified'
                        candidate['scenario']['amount_blockers']=loan_b.get('blockers',[])
    from backend.services.market_data.financial_research import build
    assessment['financial_research']=build(assessment)
    return assessment


def research_intent(options):
    """Use explicit current request before user-only history; no memory/DB writes."""
    import re
    provided=options.get('research_context')
    value=ResearchIntent.model_validate(provided or {}).model_dump()
    sources={k:'current_request_assumption' for k,v in value.items() if v not in (None,[], 'evaluate')}
    resolved=bool(provided and provided.get('objective') is not None)
    if value['objective'] is None:value['objective']='evaluate'
    if resolved:sources['objective']='current_request_assumption'
    turns=[('current_request',options.get('research_question',''))]
    turns += [('user_history',t.get('content','')) for t in reversed(options.get('research_history') or []) if t.get('role')=='user']
    for source,text in turns:
        if not isinstance(text,str):continue
        if not resolved:
            affirmative=[];negated_action=False;action_present=False
            for clause in re.split(r'[,，;；。!！?？\n]|但是|而是|但|\bbut\b|\binstead\b',text,flags=re.I):
                action=re.search(r'减仓|减持|退出|持有|卖出|清仓|\breduce\b|\bexit\b|\bsell\b|\bhold(?:ing)?\b',clause,re.I)
                action_present=action_present or bool(action)
                if action and re.search(r'不|别|无需|避免|暂缓|\bnot\b|don.t|\bnever\b',clause[:action.start()],re.I):
                    negated_action=True
                else:affirmative.append(clause)
            cleaned='，'.join(affirmative)
            choices=[]
            if re.search(r'(?:逐步|长期|全部|分批|决定).{0,8}退出|不再.{0,8}核心|exit all|gradual exit',cleaned,re.I):choices.append('exit')
            if re.search(r'减仓|减持|reduce',cleaned,re.I):choices.append('reduce')
            if re.search(r'继续持有|长期持有|\b(?:keep|continue) holding\b',cleaned,re.I):choices.append('hold')
            if choices or negated_action or action_present:
                resolved=True
                strong_exit=re.search(r'(?:长期目标|决定|目标).{0,8}退出',cleaned)
                value['objective']='exit' if strong_exit else choices[0] if len(choices)==1 else 'evaluate'
                sources['objective']=source if strong_exit else 'system_interpretation_of_'+source
        if not value['review_date']:
            matches=re.findall(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})(?:日)?(?=.{0,12}(?:复核|复盘|review))',text,re.I)
            matches+=re.findall(r'(?:复核|复盘|review)[^。\n]{0,10}?(20\d{2})[-年](\d{1,2})[-月](\d{1,2})(?:日)?',text,re.I)
            matches=list(dict.fromkeys(matches))
            if len(matches)==1:
                try:
                    from datetime import date
                    value['review_date']=date(*map(int,matches[0])).isoformat();sources['review_date']=source
                except ValueError:pass
    return dict(value,sources=sources,confirmation='user_research_assumption_only',
                holdings_verified=False,formal_target_confirmed=False)


def research_plan(assessment, *, at):
    """Non-executable L1 planning, composed from original technical/financial facts."""
    from backend.services.market_data.financial_research import build
    intent=assessment.get('research_intent') or research_intent({})
    objective=intent['objective'];user_exit=objective in {'exit','reduce'}
    day=(assessment.get('technical_levels') or {}).get('day')
    band=None
    if day:
        indicators=day['indicators']
        band=dict(lower=indicators['observed_support20'],upper=indicators['observed_resistance20'],
            currency=day['currency'],observed_at=day['end_at'],evidence_ref=day['record_id'],
            basis='observed trailing twenty-session low/high; neither target price nor order limit')
    fractions=intent['tranche_fractions'] or ([.2,.3,.3,.2] if objective=='exit' else [.25,.25] if objective=='reduce' else [])
    conditions=[
        '先复核退出理由与现金需要；不把回本作为唯一等待条件',
        '反弹后复核量价及估值；经营证据不足时不把反弹解释为基本面改善',
        '横盘时比较继续等待与退出后的机会成本；盈利假设失效则优先复核',
        '最迟复核日重新决定剩余方向；不无限期滚动等待历史价格区间']
    stages=[dict(stage=i+1,fraction_of_reference_position=f,
        fraction_basis='user_assumption' if intent['tranche_fractions'] else 'illustrative_research_draft',
        priority=i+1,condition=conditions[min(i,3)],observation_band=band,
        quantity=None,amount=None,execution_authorized=False) for i,f in enumerate(fractions)]
    financial=build(assessment)
    return dict(mode='research_only',non_executable=True,objective=objective,
        objective_source=intent['sources'].get('objective','system_inference_evaluate'),
        assumption_context=intent,stages=stages,price_observation=band,
        historical_bands_review=dict(bands=intent['historical_price_bands'],status='user_assumptions_not_current_price_evidence',
            conclusion='Only overlap with independently observed technical/valuation evidence may inform review; no mechanical reuse.'),
        review_deadline=intent['review_date'] or (at+timedelta(days=7)).date().isoformat(),
        review_deadline_basis=intent['sources'].get('review_date','system_seven_day_review_not_sale_deadline'),
        fundamental_conditions=['收入与同口径盈利变化是否支持原假设；竞争/监管归因仅为待验证机制',
            '利润与经营现金流持续背离时复核盈利质量；负现金流不等于会计利润为假',
            '分部数据缺失时，不用集团利润宣称某分部盈利；以正式披露证实或推翻'],
        scenarios=[dict(name=name,response=response,price_observation=band) for name,response in [
            ('rebound','若量价修复且估值/经营证据支持，比较分批退出与等待；反弹不撤销长期目标' if user_exit else '复核经营是否跟上价格修复'),
            ('sideways','按用户复核期限比较时间成本与替代用途，不无限期等待回本'),
            ('decline','先验证经营反证与流动性需要；假设失效时加速退出研究，不自动止损下单')]],
        limitations=financial['limitations']+([] if band else ['PRICE_OBSERVATION_UNKNOWN'])+
            ['Reference quantities/fractions are assumptions, not verified broker holdings; no order prepared.'],
        order_created=False,execution_authorized=False)


def native_quantity(assessment, step, *, at):
    from backend.services.market_data.evidence import number, record
    from decimal import Decimal, ROUND_FLOOR
    context=assessment.get("allocation_context",{})
    group=context.get("company_exposure") or {}
    member=next((m for m in group.get("members",[]) if m['id']==step['position_id']),{})
    security=next((r for r in context.get("security_inputs",[]) if r.get('id')==step['position_id']),{})
    valid=[r for r in assessment['records'] if r['quality']=='valid']
    levels=assessment['qualification']
    scenarios=assessment.get('scenarios') or {'mode':step.get('mode','illustrative_unqualified')}
    quote=next((r["values"] for r in valid if r["kind"]=="quote"),{})
    from backend.services.market_data.evidence import number
    price=number(quote.get("current_price"))
    fx=number(security.get("fx_rate_to_cny"))
    native_symbol=member.get("security_symbol") or member.get("symbol")
    market=(native_symbol or "").rsplit(":",1)[-1]
    quantity=None
    blockers=[]
    from backend.services.market_data.evidence import record
    unit=security.get('trade_unit_evidence') or {}
    costs=security.get('exit_cost_evidence') or {}
    checked_at=at
    unit_ok=(record('trade_unit',unit,at=checked_at,symbol=native_symbol)['quality']=='valid'
        and unit.get('symbol')==native_symbol and unit.get('currency')==quote.get('currency')
        and unit.get('account_ref')==step['account_ref']
        and number(unit.get('quantity_increment')) is not None and number(unit.get('quantity_increment'))>0)
    if market=='HK' and unit_ok:
        unit_ok=float(unit['quantity_increment']).is_integer()
    cost_ok=(record('exit_cost',costs,at=checked_at,symbol=native_symbol)['quality']=='valid'
        and costs.get('symbol')==native_symbol and costs.get('account_ref')==step['account_ref']
        and number(costs.get('total_cny'))==step['costs_cny'])
    if (levels["L3"]["allowed"] and unit_ok and cost_ok and scenarios["mode"]=="qualified_static_scenario" and native_symbol==assessment["symbol"] and market in {'US','HK'}
            and quote.get("currency")==security.get("original_currency") and price and fx
            and fx>0 and security.get("fx_quality")=="current"):
        from decimal import Decimal, ROUND_FLOOR
        increment=Decimal(str(unit['quantity_increment']))
        quantity=float(((Decimal(str(step['sale_value_cny']))/Decimal(str(price))/Decimal(str(fx))/increment)
            .to_integral_value(rounding=ROUND_FLOOR))*increment)
        held=number(member.get("quantity"))
        if quantity<=0 or held is None or quantity>held:
            quantity=None
            blockers.append("QUANTITY_EXCEEDS_VERIFIED_HOLDING_OR_TOO_SMALL")
    else:
        blockers.append("NATIVE_QUOTE_FX_UNIT_COST_OR_L3_UNVERIFIED")
    return quantity, blockers


def exit_plan(assessment, *, at=None):
    """A reviewable suggestion; no ActionDraft, confirmation URL or order object."""
    at=at or datetime.now(timezone.utc)
    context=assessment.get("allocation_context",{})
    targets=context.get("confirmed_stage_targets") or context.get("proposed_stage_targets") or {}
    issuer=context.get("issuer_id")
    group=context.get("company_exposure") or {}
    target=next((t for t in targets.get("issuers",[]) if t["issuer_id"]==issuer),{})
    levels=assessment["qualification"]
    current=group.get("weight_of_investments")
    if current is None and context.get("denominator_cny"):
        current=group.get("market_value_cny",0)/context["denominator_cny"]
    phases=[target["target_weight"]]+target.get("phases",[]) if target else [None]
    valid=[r for r in assessment["records"] if r["quality"]=="valid"]
    technical=assessment.get("technical_levels")
    price_trigger=None
    if levels["L3"]["allowed"] and technical:
        daily=technical["day"]
        price_trigger=dict(symbol=assessment["symbol"],market=daily["market"],currency=daily["currency"],
            observation_band=[daily["indicators"]["observed_support20"],daily["indicators"]["observed_resistance20"]],
            condition="Reassess observed trend/volume at the evidence band; this is not an order limit.",
            evidence_record_id=daily["record_id"],data_as_of=daily["end_at"],
            valid_until=(at+timedelta(days=1)).isoformat())
    tranches=[]
    for index, weight in enumerate(phases):
        tranches.append(dict(stage=index+1,target_weight=weight,
            condition="复核最新正式经营披露、配置目标和账户限制后，人工评估下一阶段；目标不自动强平",
            amount_cny=None,quantity=None,account_ref=None,market=None,currency=None,
            funds_destination=None,price_trigger=price_trigger,
            status="directional_research" if not levels["L2"]["allowed"] else "scenario_review_required"))
    from copy import deepcopy
    candidates=deepcopy(assessment.get("exit_candidates",[]))
    for candidate in candidates:
        allocated={}
        calculation=candidate.get('scenario')
        steps=iter(s for s in (calculation or {}).get('steps',[]) if s['issuer_id']==issuer)
        for row in candidate['tranches']:
            if row.get('position_id') is None or row.get('status')=='loan_selection_required':
                continue
            step=next(steps)
            row['quantity'],row['quantity_blockers']=native_quantity(assessment,
                {**step,'mode':calculation['mode']},at=at)
            held=next((m.get('quantity') for m in group.get('members',[]) if m['id']==row['position_id']),None)
            if row['quantity'] is not None:
                if held is None or allocated.get(row['position_id'],0)+row['quantity']>held:
                    row['quantity']=None
                    row['quantity_blockers'].append('CUMULATIVE_QUANTITY_EXCEEDS_VERIFIED_HOLDING')
                else:
                    allocated[row['position_id']]=allocated.get(row['position_id'],0)+row['quantity']
            if row['quantity'] is not None:
                row['quantity_basis']='verified native quantity increment and cost evidence'
                row['cost_quality']='verified'
                row['price_trigger']=price_trigger if row['symbol']==assessment['symbol'] else None
    if candidates:
        # Alternatives remain distinct; do not sum competing account plans.
        tranches=candidates[0]["tranches"]
    # User-defined account-specific simulations become clearly labeled review rows,
    # never executable orders, and cannot be promoted when L2 is blocked.
    scenarios=assessment.get("scenarios")
    if scenarios:
        allocated={}
        members={m["id"]:m for m in group.get("members",[])}
        for step in scenarios["steps"]:
            if step["issuer_id"]!=issuer:
                continue
            member=members.get(step["position_id"],{})
            security=next((r for r in context.get("security_inputs",[]) if r.get("id")==step["position_id"]),{})
            quantity,blockers=native_quantity(assessment,step,at=at)
            if quantity is not None:
                if allocated.get(step['position_id'],0)+quantity>member.get('quantity',0):
                    quantity=None
                    blockers.append('CUMULATIVE_QUANTITY_EXCEEDS_VERIFIED_HOLDING')
                else:
                    allocated[step['position_id']]=allocated.get(step['position_id'],0)+quantity
            quote=next((r["values"] for r in valid if r["kind"]=="quote"),{})
            native_symbol=member.get("security_symbol") or member.get("symbol")
            market=(native_symbol or "").rsplit(":",1)[-1]
            tranches.append(dict(stage="scenario",position_id=step["position_id"],account_ref=step["account_ref"],
                symbol=native_symbol,
                condition="仅在本静态情景、目标确认状态与成本/单位证据仍有效时人工复核",
                amount_cny=step["sale_value_cny"] if levels["L2"]["allowed"] and scenarios["mode"]=="qualified_static_scenario" else None,
                quantity=quantity,quantity_basis="verified native quantity increment; no ADR or cross-market conversion" if quantity else None,
                quantity_blockers=blockers,market=market,currency={'US':'USD','HK':'HKD'}.get(market),
                amount_currency="CNY",funds_destination=step["destination"],
                status=scenarios["mode"],price_trigger=price_trigger if native_symbol==assessment["symbol"] else None))
    # An exit direction has an operating-review milestone before the allocation milestone.
    if target.get("direction")=="gradual_exit":
        tranches.insert(0,dict(stage="operating_review",condition="核验新财报及失效条件，优先评估逐步退出",
            amount_cny=None,quantity=None,account_ref=None,market=None,currency=None,
            funds_destination=None,price_trigger=price_trigger,status="research_milestone"))
    research=research_plan(assessment,at=at)
    return dict(schema_version="wealthpilot.exit-plan.v1",issuer_id=issuer,snapshot_id=assessment["snapshot_id"],
        research_plan=research,
        target_allocation=dict(current=current,target=target.get("target_weight"),tolerance=target.get("tolerance"),
            stage=targets.get("stage"),version=targets.get("version"),digest=targets.get("digest"),
            confirmation_status=context.get("target_confirmation_status","missing"),basis=targets.get("basis")),
        account_holdings=group.get("members",[]),tranches=tranches,price_trigger=price_trigger,
        account_alternatives=candidates,
        fundamental_trigger=dict(condition="核验交付/收入/利润率/现金流及竞争变化；未知指标不得填造",
            evidence=[{k:r[k] for k in ("kind","source","source_url","record_id","period","data_as_of")}
                      for r in valid if r["kind"] in {"fundamentals","research","event"}]),
        funds_policy=targets.get("funding_policy",{}),
        fallback_rule=["反弹未出现：在复核期限重新评估配置与机会成本，不无限期等待成本价。",
                       "经营恶化：先验证正式披露与原论据失效，再人工复核退出方向。",
                       "贷款到期或流动性压力：核实余额、期限和可划转条件后优先复核偿债。"],
        review_deadline=research['review_deadline'],review_deadline_basis=research['review_deadline_basis'],
        evidence_quality=dict(qualification=levels,issues=assessment["issues"],
            missing=assessment["infoNeeded"],facts=[{k:r[k] for k in ("kind","source","record_id","data_as_of","quality")} for r in valid],
            assumptions=["目标确认状态："+context.get('target_confirmation_status','missing')+"；不覆盖正式纪律，不等于交易授权。","情景基于固定估值，不预测未来收益。"],
            excluded=[{k:r[k] for k in ("kind","source","data_as_of","quality")} for r in assessment["records"] if r["quality"]!="valid"]),
        order_created=False,execution_authorized=False,manual_trade_preparation_allowed=False)


def fact_sections(assessment, *, include_scenarios=True):
    """Existing deterministic research sections, shared by full and facts-only reports."""
    sections=[]
    def section(name, text):
        sections.append(name+"\n"+text)
    labels={"pe_ttm":"市盈率TTM","eps_ttm":"每股收益TTM","revenue_ttm":"营收TTM",
        "net_income_ttm":"净利润TTM","gross_margin":"毛利率","profit_margin":"净利润率"}
    facts=[]
    for r in assessment["records"]:
        if r["quality"]!="valid" or r["kind"] not in {"fundamentals","research"}:
            continue
        values=[f"{label}：{r['values'][key]}" for key,label in labels.items() if r["values"].get(key) is not None]
        content=r["values"].get("content") or r["values"].get("text")
        if content:
            values.append(str(content))
        field_currencies=sorted({f.get('currency') for f in assessment.get('verified_field_records',[]) if f.get('currency') and str(f.get('record_id','')).startswith(str(r.get('record_id'))+':')})
        report_currency=r['values'].get('report_currency') or '/'.join(field_currencies) or '未核验'
        facts.append(f"- {r['source']}，{r['data_as_of']}，期间 {r['period']}；经营财务币种 {report_currency}、上市估值币种 {r['values'].get('valuation_currency') or '未核验'}："+"；".join(values))
    # Display equal facts once; all original records/references remain in evidence.
    import json
    grouped={}
    for r in assessment.get('verified_field_records',[])+assessment.get('historical_field_records',[]):
        key=(r.get('field'),r.get('period'),r.get('currency'),r.get('scope'),
             json.dumps(r.get('values'),sort_keys=True,default=str))
        grouped.setdefault(key,[]).append(r)
    for equal in grouped.values():
        r=equal[0]
        scope="历史可比基线（非当前经营事实）" if r.get('historical_only') else "年度经营基线（非最新季度）" if r.get('scope')=='annual' else "季度"
        sources="、".join(dict.fromkeys(x['source']+" "+str(x.get('source_url') or x.get('record_id')) for x in equal))
        facts.append(f"- {scope}字段事实 {r.get('field')}：{r.get('values')}；币种 {r.get('currency') or '未核验'}；来源 {sources}；期间 {r['period']}；数据日 {r['data_as_of']}。")
    from backend.services.market_data.financial_research import build,display
    financial=build(assessment)
    for metric in financial['records']:
        facts.append('- 确定性研究计算 '+display(metric,metric['field'])+'；依据：'+'、'.join(metric['evidence_refs'])+'；计算口径：'+str(metric['details']))
    if include_scenarios and financial['valuation_sensitivity']:
        import json
        facts.append('估值敏感性（盈利/倍数变化为假设，固定FX；非预测、目标价或订单）：'+json.dumps(financial['valuation_sensitivity'],ensure_ascii=False))
    facts.append('独立缺口（不影响其他合格研究）：'+'；'.join(financial['limitations'])+'。旧观点和Seed不作当前经营事实。')
    section("②经营与估值事实","\n".join(facts) if facts else "缺少当前可核验的经营或估值事实。旧观点和Seed仅保留在来源记录，不作当前估值。")
    technical=assessment.get("technical_levels")
    technical_lines=[]
    if technical:
        for period,d in technical.items():
            i=d["indicators"]
            technical_lines.append(f"- {'日线' if period=='day' else '周线'}：{d['start_at']} 至 {d['end_at']}，{d['market']} / {d['currency']}；"
                +f"MA5={i['ma5']:.4f}、MA20={i['ma20']:.4f}、RSI14={i['rsi14']:.2f}、MACD={i['macd']:.4f}；"
                +f"末期量={i['volume']:.0f}，20期均量={i['average_volume20']:.0f}。"
                +f"20期观测区间 {i['observed_support20']:.4f}—{i['observed_resistance20']:.4f}，仅作研究观察，不是下单限价。")
    for parent in assessment['records']:
        if parent.get('kind')!='technical':continue
        for period,state in parent.get('period_quality',{}).items():
            missing=state.get('missing_trading_sessions',[])
            if missing:
                technical_lines.append(f"- {period} 缺失应交易日期："+"、".join(missing)+"；未取得停牌证明，不能据此忽略缺口。")
    section("③技术趋势与关键位","\n".join(technical_lines) if technical_lines else "TECHNICAL_UNAVAILABLE：技术观察位为空，先补可核验的日线、周线及来源。")
    events=[f"- {r['source']}，披露 {r['published_at']}，期间 {r['period']}；{r['record_id'] or r['source_url']}"
        for r in assessment["records"] if r["quality"]=="valid" and r["kind"]=="event"]
    section("④事件催化","\n".join(events) if events else "事件证据不足，需核验正式披露和公告日期。")
    return sections


def report(assessment, plan, decision):
    """Seven-layer human report; numerical statements come from verified inputs."""
    context=assessment.get("allocation_context",{})
    targets=context.get("confirmed_stage_targets") or context.get("proposed_stage_targets") or {}
    group=context.get("company_exposure") or {}
    levels=assessment["qualification"]
    sections=[]
    def section(name,text):
        sections.append(name+"\n"+text)
    def amount(value):
        return "未知" if value is None else f"¥{value:,.2f}"
    def weight(value):
        return "未设定" if value is None else f"{value:.1%}"
    mode="证据达到精确分析门槛，仍须人工复核。" if levels["L3"]["allowed"] else "L1研究保留所有合格经营/技术事实；执行价量条件未建立，不提供可执行限价或股数。"
    allocation=plan["target_allocation"]
    text=(mode+f"\n固定快照：{plan['snapshot_id'] or '未提供'}；公司：{plan['issuer_id'] or '尚未确认'}。"
        +f"\n公司跨账户参考市值 {amount(group.get('market_value_cny'))}；投资资产分母 {amount(context.get('denominator_cny'))}。"
        +f"当前参考占比 {weight(allocation['current'])}；请求假设目标 {weight(allocation['target'])}。"
        +"\n冻结估值不代表实时可交易报价；来源过期时，金额仅作示意。")
    rule_source=context.get('effective_rules_provenance') or {'basis':'effective_rules_unverified'}
    text+=f"\n纪律来源：{rule_source.get('basis')}；个人纪律确认版本未核验。历史单证券持仓阈值参考不能替代当前跨账户公司集中度判断。"
    if group.get("members"):
        text+="\n\n| 账户 | 市场证券 | 本账户数量 | 参考人民币市值 |\n|---|---|---:|---:|"
        for m in group["members"]:
            text+=f"\n| {m.get('account_ref')} | {m.get('security_symbol') or m.get('symbol')} | {m.get('quantity','未知')} | {amount(m.get('market_value_cny'))} |"
    section("①目标配置 / 当前敞口",text)
    sections.extend(fact_sections(assessment))
    direction={"trim":"减仓研究","exit":"逐步退出研究","hold":"持有并复核","watch":"继续观察",
               "buy_init":"新建仓评估","buy_more":"加仓评估"}.get(decision,"继续观察")
    confirmed=context.get('target_confirmation_status')=='confirmed'
    lines=[f"原专业链路方向：{direction}（分析推断，不能独立授予交易资格）。",
        (f"服务端已确认目标版本：{targets['version']}，阶段：{targets['stage']}；来源 {targets.get('confirmation_source')}，确认时间 {targets.get('confirmed_at')}。" if confirmed else
        f"未核验的请求目标版本：{targets['version']}，阶段：{targets['stage']}；仅为情景假设，尚无可信用户确认。") if targets else "缺少有效的用户确认目标；系统默认配置不能代表个人目标。"]
    for row in plan["tranches"]:
        line=f"- 阶段 {row['stage']}：{row['condition']}。"
        if row.get("target_weight") is not None:
            line+=f"阶段研究目标占比 {weight(row['target_weight'])}，并非无条件卖出要求。"
        if row.get("amount_cny") is not None:
            line+=f"假设减仓额 {amount(row['amount_cny'])}，账户 {row['account_ref']}，去向 {row['funds_destination']}。"
        lines.append(line)
    research=plan.get('research_plan',{})
    if research:
        names={'exit':'逐步退出','reduce':'减仓','hold':'持有复核','evaluate':'评估'}
        lines.append('L1研究方案（research-only / non-executable，不要求L3，不产生交易授权）：目标 '+names[research['objective']]
            +'；来源 '+research['objective_source']+'，用户声明不代表实时持仓或正式配置。')
        for row in research['stages']:
            lines.append(f"- 研究批次 {row['stage']}，参考持仓比例 {row['fraction_of_reference_position']:.0%}（{row['fraction_basis']}，非当前已核验股数）：{row['condition']}。")
        band=research.get('price_observation')
        if band:
            lines.append(f"价格观察区间 {band['lower']}—{band['upper']} {band['currency']}；截止 {band['observed_at']}，依据 {band['evidence_ref']}；历史窗口高低点，不是目标价或限价。")
        else:lines.append('价格观察区间UNKNOWN，不编造技术价位。')
        lines.extend('- '+r['name']+'：'+r['response'] for r in research['scenarios'])
        lines.extend('- 经营复核：'+condition for condition in research['fundamental_conditions'])
        if research['assumption_context'].get('reference_quantity'):
            lines.append('历史参考数量 '+str(research['assumption_context']['reference_quantity'])+' 仅为用户假设；未核验当前券商股数，不用于执行。')
        lines.append('复核期限：'+research['review_deadline']+'；来源 '+research['review_deadline_basis']+'（不是卖出截止或自动触发）。')
    if plan["funds_policy"]:
        destination_names={"offshore_fixed_income":"境外固收","repay_cny_loan":"偿还人民币贷款","investment_cash":"投资账户现金"}
        lines.append(("服务端确认的资金去向：" if confirmed else "请求假设的资金去向：")+"；".join(f"{a} → {destination_names[d]}" for a,d in plan["funds_policy"].items()))
    for candidate in plan.get('account_alternatives',[]):
        lines.append(f"\n账户顺序比较 {candidate['preferred_account']}；费用、税费、换汇与最低现金暂按零作示意，真实约束待核验；不推荐自动执行。")
        if candidate.get('prerequisite_exits'):
            lines.append("本分支假设其他逐步退出目标先完成，再复核本公司被动集中的变化；这些退出并未实际发生，也不是自动执行要求。")
        if candidate.get('blockers'):
            lines.append("方案待核验："+"、".join(candidate['blockers']))
        for row in candidate['tranches']:
            lines.append(f"- 阶段 {row['stage']} / {row.get('account_ref')} / {row.get('currency')}：目标 {weight(row.get('target_weight'))}；假设金额 {amount(row.get('amount_cny'))}；去向 {row.get('funds_destination')}；{row['condition']}")
    scenario=assessment.get("scenarios")
    if scenario:
        lines.append("\n静态情景测算（"+("示意、证据资格不足" if scenario["mode"]=="illustrative_unqualified" else "金额资格达标、仍依赖列明假设")+"，不是收益预测或交易指令）：")
        for row in scenario["steps"]:
            lines.append(f"- {row['issuer_id']} / {row['account_ref']}：假设卖出 {amount(row['sale_value_cny'])}，"
                +f"投资分母 {amount(row['investment_assets_cny'])}，固收占比 {weight(row['fixed_income_weight'])}，"
                +f"负债 {amount(row['liabilities_cny'])}，净资产 {amount(row['net_assets_cny'])}；假设：{row['assumptions']}。")
    section("⑤多情景减仓建议","\n".join(lines))
    section("⑥失效 / 复核条件","\n".join("- "+r for r in plan["fallback_rule"])
        +f"\n最迟复核：{plan['review_deadline']}；依据 {plan['review_deadline_basis']}，不是必须卖完的日期。")
    quality_names={"valid":"可核验","stale":"过期","unknown":"未核验","seed":"Seed","mock":"Mock",
                   "conflict":"冲突","superseded":"已被更新披露取代"}
    sources=["| 类型 | 来源 / 记录 | 数据日期 | 状态 |","|---|---|---|---|"]
    seen_sources=set()
    for r in assessment["records"]:
        key=(r['kind'],r['record_id'],r['source_url'],r['data_as_of'],r['quality'])
        if key in seen_sources:continue
        seen_sources.add(key)
        ref=f"[{r['source']}]({r['source_url']})" if r['source_url'] else str(r['record_id'] or r['source'])
        state=quality_names.get(r['quality'],r['quality'])
        if r['source']=='official_disclosure' and r['quality']=='stale' and any(f.get('scope')=='annual' and str(f.get('record_id','')).startswith(str(r['record_id'])+':') for f in assessment.get('verified_field_records',[])):
            state='研究时钟过期；独立年度字段见经营基线'
        sources.append(f"| {r['kind']} | {ref} | {r['data_as_of'] or '未知'} | {state} |")
    sources.append("\n规划器："+str(assessment['planner'].get('status','unknown'))+"；规划降级与研究缺证分别判断。")
    sources.append("缺口 / 告警："+"、".join(sorted(set(assessment["issues"]+assessment["infoNeeded"]))))
    sources.append("L4未授权；本报告未创建、准备、确认或提交订单。")
    section("⑦来源、日期、质量警告","\n".join(sources))
    return "\n\n".join(sections)
