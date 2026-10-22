"""Account ordering and stage selection over the original scenario calculator.

No asset ledger, price engine, persistence, broker or order operations.
"""
from decimal import Decimal, ROUND_DOWN
from copy import deepcopy


def build_candidates(view, issuer_id, targets, *, amount_qualified=False, _prefix_legs=(), _include_priorities=True):
    from backend.services.allocation_service import simulate_exit_scenario
    from backend.services.allocation_scenarios import IssuerTarget
    if targets:
        targets=deepcopy(targets)
        targets['issuers']=[IssuerTarget.model_validate(t).model_dump(mode='json') for t in targets.get('issuers',[])]
    groups=(view or {}).get('issuer_exposure',{}).get('companies',[])
    group=next((g for g in groups if g['issuer_id']==issuer_id),None)
    target=next((t for t in (targets or {}).get('issuers',[]) if t['issuer_id']==issuer_id),None)
    if not group or not target:
        return []
    denominator=(view or {}).get('issuer_exposure',{}).get('investment_denominator_cny')
    if not denominator or denominator<=0:
        return []
    members=group['members']
    account_info={a['account_ref']:a for a in view.get('data_quality',{}).get('accounts',[])}
    policies=(targets or {}).get('funding_policy',{})
    def destination(m):
        broker=account_info.get(m['account_ref'],{}).get('broker')
        # Existing B0/U0 storage names IBKR holdings "snowball".
        return (policies.get(m['account_ref']) or policies.get('broker:'+str(broker))
                or (policies.get('broker:ibkr') if broker=='snowball' else None))
    prefix_calculation=simulate_exit_scenario(view,list(_prefix_legs),amount_qualified=amount_qualified,target=targets) if _prefix_legs else None
    initial=prefix_calculation['steps'][-1]['company_weights'][issuer_id] if prefix_calculation else group['market_value_cny']/denominator
    rounding_tolerance=.011/denominator
    goals=[target['target_weight']]+target.get('phases',[])
    default_staging=target.get('direction')=='gradual_exit' and target['target_weight']==0 and not target.get('phases')
    if default_staging:
        goals=[initial*2/3,initial/3,0.0]
    goals=list(dict.fromkeys(goals))
    loans=[l for l in view.get('views',{}).get('get_manual_wealth_items',{}).get('items',[])
        if str(l.get('kind','')).upper()=='LIABILITY' and l.get('currency')=='CNY'
        and l.get('effective_included_in_net_worth',l.get('included_in_net_worth')) and (l.get('current_value') or 0)>0
        and (l.get('item_type') or l.get('category'))=='consumer_loan']
    # Ordering is a comparison, never an automatic best-account or tax ranking.
    alternatives=[]
    accounts=list(dict.fromkeys(m['account_ref'] for m in members))
    for first in accounts:
        ordered=sorted(members,key=lambda m:(m['account_ref']!=first,m['account_ref'],m['id']))
        legs=deepcopy(list(_prefix_legs));tranches=[];failures=[];calculation=deepcopy(prefix_calculation)
        for stage,goal in enumerate(goals,1):
            current=(calculation['steps'][-1]['company_weights'][issuer_id] if calculation else initial)
            if current<=goal+rounding_tolerance:
                tranches.append(dict(stage=stage,target_weight=goal,account_ref=first,amount_cny=0 if amount_qualified else None,
                    quantity=None,currency=None,amount_currency='CNY',funds_destination=None,status='target_already_met',
                    condition='持续复核经营与集中度；达到目标不产生卖出要求。'))
                continue
            for member in ordered:
                current=(calculation['steps'][-1]['company_weights'][issuer_id] if calculation else initial)
                if current<=goal+rounding_tolerance:
                    break
                used=sum((Decimal(str(l['sale_value_cny'])) for l in legs if l['position_id']==member['id']),Decimal(0))
                remaining=float(Decimal(str(member['market_value_cny']))-used)
                if remaining<=1e-8:
                    continue
                dest=destination(member)
                if dest is None:
                    failures.append('FUNDING_POLICY_REQUIRED:'+member['account_ref'])
                    continue
                loan=loans[0] if len(loans)==1 else None
                if dest=='repay_cny_loan' and not loan:
                    failures.append('UNIQUE_CONSUMER_LOAN_SELECTION_REQUIRED')
                    symbol=member.get('security_symbol') or member.get('symbol') or ''
                    market=symbol.rsplit(':',1)[-1] if ':' in symbol else None
                    tranches.append(dict(stage=stage,target_weight=goal,position_id=member['id'],account_ref=member['account_ref'],
                        symbol=symbol,market=market,currency={'US':'USD','HK':'HKD'}.get(market),amount_currency='CNY',
                        amount_cny=None,quantity=None,price_trigger=None,funds_destination=dest,
                        status='loan_selection_required',condition='先确认唯一信用贷及余额，不自动选择房贷或其他债务。'))
                    continue
                leg=dict(snapshot_id=view['snapshot_id'],issuer_id=issuer_id,position_id=member['id'],
                    account_ref=member['account_ref'],destination=dest,target_weight=goal,
                    fees_cny=0,tax_cny=0,fx_cost_cny=0,liquidity_floor_cny=0,
                    loan_item_id=loan['id'] if dest=='repay_cny_loan' else None,
                    assumptions='候选静态测算：费用/税费/换汇成本暂按零作示意，真实成本及划转条件未核验；不构成交易指令。')
                try:
                    calculation=simulate_exit_scenario(view,legs+[leg],amount_qualified=amount_qualified,target=targets)
                except ValueError as exc:
                    if str(exc)!='SCENARIO_SALE_OUT_OF_RANGE':
                        failures.append(str(exc));continue
                    # A complete account exit may be needed before the next
                    # account. The original calculator still owns every amount.
                    leg.pop('target_weight');leg['sale_value_cny']=remaining
                    try:
                        calculation=simulate_exit_scenario(view,legs+[leg],amount_qualified=amount_qualified,target=targets)
                    except ValueError as full_error:
                        failures.append(str(full_error));continue
                step=calculation['steps'][-1]
                # Freeze derived amounts before moving to the next target stage.
                rounded=float(Decimal(str(step['sale_value_cny'])).quantize(Decimal('.01'),rounding=ROUND_DOWN))
                if rounded<=0:
                    continue
                leg.pop('target_weight',None);leg['sale_value_cny']=rounded
                calculation=simulate_exit_scenario(view,legs+[leg],amount_qualified=amount_qualified,target=targets)
                step=calculation['steps'][-1]
                legs.append(leg)
                symbol=member.get('security_symbol') or member.get('symbol') or ''
                market=symbol.rsplit(':',1)[-1] if ':' in symbol else None
                tranches.append(dict(stage=stage,target_weight=goal,position_id=member['id'],account_ref=member['account_ref'],
                    symbol=symbol,market=market,currency={'US':'USD','HK':'HKD'}.get(market),amount_currency='CNY',
                    amount_cny=step['sale_value_cny'] if amount_qualified and calculation['mode']=='qualified_static_scenario' else None,
                    quantity=None,price_trigger=None,funds_destination=dest,status=calculation['mode'],
                    loan_item_id=loan['id'] if dest=='repay_cny_loan' else None,
                    cost_quality='unverified_zero_cost_illustration',liquidity_floor_quality='unverified_zero_floor_illustration',
                    quantity_blockers=['VERIFIED_NATIVE_QUOTE_FX_TRADE_UNIT_AND_COSTS_REQUIRED'],
                    condition='核验阶段目标、最新经营披露、账户可划转与流动性；达到阶段条件后人工复核，非自动执行。'))
            current=(calculation['steps'][-1]['company_weights'][issuer_id] if calculation else initial)
            if current>goal+rounding_tolerance:
                failures.append('STAGE_TARGET_NOT_REACHED:'+str(stage))
        alternatives.append(dict(candidate_id='account-first:'+first,preferred_account=first,
            snapshot_id=view['snapshot_id'],issuer_id=issuer_id,target_version=targets.get('version'),
            target_basis=targets.get('basis'),stage_policy='system_default_three_reviews' if default_staging else 'target_defined_stages',
            tranches=tranches,scenario=calculation,legs=legs,blockers=sorted(set(failures)),
            comparison_basis='Different account ordering and declared funding destinations; no best-account/tax claim.',
            order_created=False,execution_authorized=False))
    if _include_priorities and target.get('direction')=='concentration_control':
        prior=[]
        for other in targets.get('issuers',[]):
            if other['issuer_id']==issuer_id or other.get('direction')!='gradual_exit':
                continue
            plans=build_candidates(view,other['issuer_id'],targets,amount_qualified=amount_qualified,_include_priorities=False)
            feasible=next((c for c in plans if c['legs'] and not c['blockers']),None)
            if feasible:
                prior.extend(feasible['legs'])
        if prior:
            # This is an explicit alternative assumption, not an instruction to
            # liquidate other companies or a claim that those exits occurred.
            try:
                after=build_candidates(view,issuer_id,targets,amount_qualified=amount_qualified,
                    _prefix_legs=prior,_include_priorities=False)
            except ValueError:
                after=[]  # combined loan/transfer constraints may invalidate the assumed sequence
            for candidate in after:
                candidate['candidate_id']='after-priority-exits:'+candidate['candidate_id']
                candidate['prerequisite_exits']=deepcopy(prior)
                candidate['comparison_basis']='Assume other gradual-exit targets occur first; compare passive concentration and account funding. None has executed.'
            alternatives.extend(after)
    return alternatives
