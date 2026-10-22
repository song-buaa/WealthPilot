"""4.2 composition inside original PEER: reuse evidence Skill and AllocationService.

This module owns no data/provider, model, persistence, order or sync operations.
"""
from datetime import datetime, timedelta, timezone


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
    context=target_context(asset_view,issuer_id,options.get("target_versions",[]),formal_rules,
        dict(allocation=[t.model_dump(mode="json") for t in get_targets()],
             effective_rules=effective_rules,rule_source=rule_source),at=at)
    context["effective_rules_provenance"]=rule_source
    member_ids={m["id"] for m in (context.get("company_exposure") or {}).get("members",[])}
    context["security_inputs"]=[r for r in rows if r.get("id") in member_ids]
    assessment["allocation_context"]=context
    if not context["confirmed_stage_targets"]:
        assessment["infoNeeded"].append("CONFIRMED_STAGE_TARGETS_REQUIRED")
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
            amount_qualified=assessment["qualification"]["L2"]["allowed"],target=context["confirmed_stage_targets"])
    return assessment


def exit_plan(assessment, *, at=None):
    """A reviewable suggestion; no ActionDraft, confirmation URL or order object."""
    at=at or datetime.now(timezone.utc)
    context=assessment.get("allocation_context",{})
    targets=context.get("confirmed_stage_targets") or {}
    issuer=context.get("issuer_id")
    group=context.get("company_exposure") or {}
    target=next((t for t in targets.get("issuers",[]) if t["issuer_id"]==issuer),{})
    levels=assessment["qualification"]
    current=group.get("weight_of_investments")
    if current is None and context.get("denominator_cny"):
        current=group.get("market_value_cny",0)/context["denominator_cny"]
    phases=[target["target_weight"]]+target.get("phases",[]) if target else [None]
    deadline=(at+timedelta(days=7)).isoformat()
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
    # User-defined account-specific simulations become clearly labeled review rows,
    # never executable orders, and cannot be promoted when L2 is blocked.
    scenarios=assessment.get("scenarios")
    if scenarios:
        members={m["id"]:m for m in group.get("members",[])}
        for step in scenarios["steps"]:
            if step["issuer_id"]!=issuer:
                continue
            member=members.get(step["position_id"],{})
            security=next((r for r in context.get("security_inputs",[]) if r.get("id")==step["position_id"]),{})
            quote=next((r["values"] for r in valid if r["kind"]=="quote"),{})
            from backend.services.market_data.evidence import number
            price=number(quote.get("current_price"))
            fx=number(security.get("fx_rate_to_cny"))
            native_symbol=member.get("security_symbol") or member.get("symbol")
            market=(native_symbol or "").rsplit(":",1)[-1]
            quantity=None
            blockers=[]
            if (levels["L3"]["allowed"] and scenarios["mode"]=="qualified_static_scenario" and native_symbol==assessment["symbol"] and market=="US"
                    and quote.get("currency")==security.get("original_currency") and price and fx
                    and fx>0 and security.get("fx_quality")=="current"):
                from decimal import Decimal, ROUND_FLOOR
                quantity=int((Decimal(str(step["sale_value_cny"]))/Decimal(str(price))/Decimal(str(fx))).to_integral_value(rounding=ROUND_FLOOR))
                held=number(member.get("quantity"))
                if quantity<=0 or held is None or quantity>held:
                    quantity=None
                    blockers.append("QUANTITY_EXCEEDS_VERIFIED_HOLDING_OR_TOO_SMALL")
            else:
                blockers.append("NATIVE_QUOTE_FX_UNIT_OR_L3_UNVERIFIED")
            tranches.append(dict(stage="scenario",position_id=step["position_id"],account_ref=step["account_ref"],
                symbol=native_symbol,
                condition="仅在本静态情景假设和确认目标仍有效时人工复核",
                amount_cny=step["sale_value_cny"] if levels["L2"]["allowed"] and scenarios["mode"]=="qualified_static_scenario" else None,
                quantity=quantity,quantity_basis="whole US listed units; no ADR or cross-market conversion" if quantity else None,
                quantity_blockers=blockers,market=market,currency=quote.get("currency") if quantity else "CNY",
                amount_currency="CNY",funds_destination=step["destination"],
                status=scenarios["mode"],price_trigger=price_trigger if native_symbol==assessment["symbol"] else None))
    # An exit direction has an operating-review milestone before the allocation milestone.
    if target.get("direction")=="gradual_exit":
        tranches.insert(0,dict(stage="operating_review",condition="核验新财报及失效条件，优先评估逐步退出",
            amount_cny=None,quantity=None,account_ref=None,market=None,currency=None,
            funds_destination=None,price_trigger=price_trigger,status="research_milestone"))
    return dict(schema_version="wealthpilot.exit-plan.v1",issuer_id=issuer,snapshot_id=assessment["snapshot_id"],
        target_allocation=dict(current=current,target=target.get("target_weight"),tolerance=target.get("tolerance"),
            stage=targets.get("stage"),version=targets.get("version"),digest=targets.get("digest")),
        account_holdings=group.get("members",[]),tranches=tranches,price_trigger=price_trigger,
        fundamental_trigger=dict(condition="核验交付/收入/利润率/现金流及竞争变化；未知指标不得填造",
            evidence=[{k:r[k] for k in ("kind","source","source_url","record_id","period","data_as_of")}
                      for r in valid if r["kind"] in {"fundamentals","research","event"}]),
        funds_policy=targets.get("funding_policy",{}),
        fallback_rule=["反弹未出现：在复核期限重新评估配置与机会成本，不无限期等待成本价。",
                       "经营恶化：先验证正式披露与原论据失效，再人工复核退出方向。",
                       "贷款到期或流动性压力：核实余额、期限和可划转条件后优先复核偿债。"],
        review_deadline=deadline,review_deadline_basis="system default seven-day review, not a sale deadline",
        evidence_quality=dict(qualification=levels,issues=assessment["issues"],
            missing=assessment["infoNeeded"],facts=[{k:r[k] for k in ("kind","source","record_id","data_as_of","quality")} for r in valid],
            assumptions=["阶段目标是确认偏好，非正式纪律覆盖或交易授权。","情景基于固定估值，不预测未来收益。"],
            excluded=[{k:r[k] for k in ("kind","source","data_as_of","quality")} for r in assessment["records"] if r["quality"]!="valid"]),
        order_created=False,execution_authorized=False,manual_trade_preparation_allowed=False)


def report(assessment, plan, decision):
    """Seven-layer human report; numerical statements come from verified inputs."""
    context=assessment.get("allocation_context",{})
    targets=context.get("confirmed_stage_targets") or {}
    group=context.get("company_exposure") or {}
    levels=assessment["qualification"]
    sections=[]
    def section(name,text):
        sections.append(name+"\n"+text)
    def amount(value):
        return "未知" if value is None else f"¥{value:,.2f}"
    def weight(value):
        return "未设定" if value is None else f"{value:.1%}"
    mode="证据达到精确分析门槛，仍须人工复核。" if levels["L3"]["allowed"] else "受限分析：不提供可执行限价或股数。"
    allocation=plan["target_allocation"]
    text=(mode+f"\n固定快照：{plan['snapshot_id'] or '未提供'}；公司：{plan['issuer_id'] or '尚未确认'}。"
        +f"\n公司跨账户参考市值 {amount(group.get('market_value_cny'))}；投资资产分母 {amount(context.get('denominator_cny'))}。"
        +f"当前参考占比 {weight(allocation['current'])}；阶段目标 {weight(allocation['target'])}。"
        +"\n冻结估值不代表实时可交易报价；来源过期时，金额仅作示意。")
    if group.get("members"):
        text+="\n\n| 账户 | 市场证券 | 本账户数量 | 参考人民币市值 |\n|---|---|---:|---:|"
        for m in group["members"]:
            text+=f"\n| {m.get('account_ref')} | {m.get('security_symbol') or m.get('symbol')} | {m.get('quantity','未知')} | {amount(m.get('market_value_cny'))} |"
    section("①目标配置 / 当前敞口",text)
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
        facts.append(f"- {r['source']}，{r['data_as_of']}，期间 {r['period']}；报告币种 {r['values'].get('report_currency') or '未核验'}、上市估值币种 {r['values'].get('valuation_currency') or '未核验'}："+"；".join(values))
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
    section("③技术趋势与关键位","\n".join(technical_lines) if technical_lines else "TECHNICAL_UNAVAILABLE：技术观察位为空，先补可核验的日线、周线及来源。")
    events=[f"- {r['source']}，披露 {r['published_at']}，期间 {r['period']}；{r['record_id'] or r['source_url']}"
        for r in assessment["records"] if r["quality"]=="valid" and r["kind"]=="event"]
    section("④事件催化","\n".join(events) if events else "事件证据不足，需核验正式披露和公告日期。")
    direction={"trim":"减仓研究","exit":"逐步退出研究","hold":"持有并复核","watch":"继续观察",
               "buy_init":"新建仓评估","buy_more":"加仓评估"}.get(decision,"继续观察")
    lines=[f"原专业链路方向：{direction}（分析推断，不能独立授予交易资格）。",
        f"用户确认目标版本：{targets['version']}，阶段：{targets['stage']}。" if targets else "缺少有效的用户确认目标；系统默认配置不能代表个人目标。"]
    for row in plan["tranches"]:
        line=f"- 阶段 {row['stage']}：{row['condition']}。"
        if row.get("target_weight") is not None:
            line+=f"目标占比 {weight(row['target_weight'])}，并非无条件卖出要求。"
        if row.get("amount_cny") is not None:
            line+=f"假设减仓额 {amount(row['amount_cny'])}，账户 {row['account_ref']}，去向 {row['funds_destination']}。"
        lines.append(line)
    if plan["funds_policy"]:
        destination_names={"offshore_fixed_income":"境外固收","repay_cny_loan":"偿还人民币贷款","investment_cash":"投资账户现金"}
        lines.append("确认的资金去向规则："+"；".join(f"{a} → {destination_names[d]}" for a,d in plan["funds_policy"].items())+"。")
    scenario=assessment.get("scenarios")
    if scenario:
        lines.append("\n静态情景测算（"+("示意、证据资格不足" if scenario["mode"]=="illustrative_unqualified" else "金额资格达标、仍依赖列明假设")+"，不是收益预测或交易指令）：")
        for row in scenario["steps"]:
            lines.append(f"- {row['issuer_id']} / {row['account_ref']}：假设卖出 {amount(row['sale_value_cny'])}，"
                +f"投资分母 {amount(row['investment_assets_cny'])}，固收占比 {weight(row['fixed_income_weight'])}，"
                +f"负债 {amount(row['liabilities_cny'])}，净资产 {amount(row['net_assets_cny'])}；假设：{row['assumptions']}。")
    section("⑤多情景减仓建议","\n".join(lines))
    section("⑥失效 / 复核条件","\n".join("- "+r for r in plan["fallback_rule"])
        +f"\n最迟复核：{plan['review_deadline']}；采用默认七日复核政策，不是必须卖完的日期。")
    quality_names={"valid":"可核验","stale":"过期","unknown":"未核验","seed":"Seed","mock":"Mock",
                   "conflict":"冲突","superseded":"已被更新披露取代"}
    sources=["| 类型 | 来源 / 记录 | 数据日期 | 状态 |","|---|---|---|---|"]
    for r in assessment["records"]:
        ref=f"[{r['source']}]({r['source_url']})" if r['source_url'] else str(r['record_id'] or r['source'])
        sources.append(f"| {r['kind']} | {ref} | {r['data_as_of'] or '未知'} | {quality_names.get(r['quality'],r['quality'])} |")
    sources.append("\n规划器："+str(assessment['planner'].get('status','unknown'))+"；规划降级与研究缺证分别判断。")
    sources.append("缺口 / 告警："+"、".join(sorted(set(assessment["issues"]+assessment["infoNeeded"]))))
    sources.append("L4未授权；本报告未创建、准备、确认或提交订单。")
    section("⑦来源、日期、质量警告","\n".join(sources))
    return "\n\n".join(sections)
