"""S3-B bounded, deterministic research rules over original financial Owner.

No parsing, collection, TTM, policy or trading. PASS is scoped to the disclosed
financial window, never proof of durable advantage or absence of all risk.
"""
from decimal import Decimal as D
from backend.services.market_data.evidence import stamp
from backend.services.market_data.financial_research import build_quality

RULE_VERSION = 's3-b.us-gaap-nonfinancial-quality.v1.1'


def evaluate(assessment, legacy):
    inputs = build_quality(assessment)
    floors = legacy['floors']
    major, warnings = [], []
    def val(r): return D(r['value'])
    def floor(key, status, reason, rows=()):
        floors[key] = dict(status=status, reason=reason, rule_version=RULE_VERSION,
            evidence_refs=sorted({ref for r in rows if r for ref in r.get('evidence_refs', [])}),
            scope='US_GAAP_NONFINANCIAL_OBSERVED_WINDOW_RESEARCH_ONLY')
    b, cash = inputs['balance'], inputs['latest_cash']
    scope=assessment.get('quality_scope',{})
    try:
        sic=int(scope.get('sic'))
    except (TypeError,ValueError):
        sic=None
    applicable=bool(scope.get('issuer_type')=='domestic' and sic is not None and 1000<=sic<=8999
        and not 6000<=sic<=6999 and not 4900<=sic<=4999 and scope.get('source_document_sha256')
        and stamp(scope.get('fetched_at')) and stamp(scope['fetched_at'])<=stamp(assessment['as_of']))
    legacy['applicability']=dict(status='APPLICABLE_BOUNDED_NONFINANCIAL_SCREEN' if applicable else 'UNKNOWN_OR_NOT_APPLICABLE', scope_evidence=scope)
    floors['data_trust'].update(rule_version=RULE_VERSION, evidence_refs=sorted(legacy['evidence_catalog']))
    usable = bool(applicable and inputs['fresh'] and not inputs['rejected_refs'] and not inputs['conflicts'])
    balance_fields = ['assets','liabilities','equity','current_assets','current_liabilities','cash','cash_including_restricted','noncurrent_debt']
    balance_rows = [b.get(k) for k in balance_fields]
    debt = b.get('current_debt')
    debt_refs = [debt] if debt else [b.get('current_long_term_debt'), b.get('commercial_paper')]
    debt_value = sum((val(r) for r in debt_refs if r), D(0)) if all(debt_refs) else None
    debt_consistent = (not debt or not b.get('current_long_term_debt')
        or val(debt)>=val(b['current_long_term_debt']))
    if debt and b.get('current_long_term_debt') and b.get('commercial_paper'):
        debt_consistent = debt_consistent and val(debt)==val(b['current_long_term_debt'])+val(b['commercial_paper'])
    floor('financial_sustainability','暂无法判断','Missing, conflicting, stale or unmatched balance/liquidity/current-debt evidence.')
    if usable and all(balance_rows) and debt_value is not None and cash and debt_consistent:
        a,l,e,ca,cl,c,cr,nd = map(val,balance_rows)
        balance_ok = (a>0 and l>=0 and 0<=c<=ca<=a and 0<=debt_value<=cl<=l and c<=cr<=a
                      and 0<=nd<=l-cl and abs(a-l-e)<=D(1))
        if balance_ok:
            if ca>=cl and c>=debt_value and val(cash['cfo'])>0:
                floor('financial_sustainability','通过','Observed balance reconciles; current assets cover current liabilities, cash excluding restricted cash covers disclosed current debt, and same-end CFO is positive. No claim of no going-concern/refinancing risk.',balance_rows+debt_refs+[cash['cfo']])
            else:
                warnings.append('OBSERVED_LIQUIDITY_OR_CURRENT_DEBT_COVERAGE_WARNING')
                floor('financial_sustainability','警示','Observed liquidity/debt coverage or cash generation needs review; debt existence alone is not a veto.',balance_rows+debt_refs+[cash['cfo']])
                if ca<cl and c<debt_value and val(cash['cfo'])<0:
                    major.append('CONFIRMED_CURRENT_LIQUIDITY_SHORTFALL_AND_NEGATIVE_CFO')
        else:
            inputs['conflicts'].append(['BALANCE_OR_CASH_RECONCILIATION_CONFLICT'])
    floor('profit_cash_support','暂无法判断','Same-period GAAP profit/CFO/PP&E outflow/SBC and working-capital observations missing; no invented normalization.')
    if usable and cash:
        if cash.get('cash_conversion') and val(cash['cfo'])<val(cash['net_income']):
            warnings.append('OBSERVED_CFO_BELOW_GAAP_PROFIT')
            floor('profit_cash_support','警示','Same-period CFO trails positive GAAP profit; exact PP&E/one-off or cash-bridge gaps remain UNKNOWN, no normalized number invented.',[cash['cfo'],cash['net_income']])
        required = [cash.get(k) for k in ('net_income','operating_income','capex','sbc','repurchases','receivables_change','inventory_change','payables_change','diluted_shares')]
        if all(required) and cash.get('fcf') and cash.get('fcf_less_sbc_sensitivity'):
            rows=required+[cash['cfo']]
            good=(val(cash['net_income'])>0 and val(cash['operating_income'])>0 and val(cash['diluted_shares'])>0
                  and val(cash['capex'])>=0 and val(cash['sbc'])>=0 and val(cash['repurchases'])>=0
                  and cash.get('cash_conversion') and val(cash['cfo'])>=val(cash['net_income']) and val(cash['cfo'])-val(cash['capex'])-val(cash['sbc'])>0)
            floor('profit_cash_support','通过' if good else '警示',
                  'GAAP cash/profit support passes conservative CFO/profit >= 1 and positive FCF-minus-SBC sensitivity; WC components and repurchases disclosed. Not a full normalized cash bridge or proof of no one-offs.' if good else
                  'Observed cash/profit conversion or FCF-minus-SBC does not meet conservative research screen; investigate WC/one-offs, do not invent adjusted earnings.',rows)
            if not good: warnings.append('OBSERVED_CASH_PROFIT_SUPPORT_WARNING')
    quarters=inputs['quarters']
    observed=[]
    for current in quarters[-2:]:
        prior=next((p for p in quarters if 350<=(stamp(current['period'])-stamp(p['period'])).days<=380
                    and abs((stamp(current['period'])-stamp(current['start'])).days-(stamp(p['period'])-stamp(p['start'])).days)<=7),None)
        if prior and val(prior['revenue'])>0:
            observed.append(dict(period=current['period'], prior_period=prior['period'],
                revenue_yoy=str(val(current['revenue'])/val(prior['revenue'])-1),
                margin_change=str(val(current['operating_income'])/val(current['revenue'])-val(prior['operating_income'])/val(prior['revenue'])),
                operating_income=current['operating_income']['value'],
                evidence_refs=sorted(set(current['margin']['evidence_refs']+prior['margin']['evidence_refs']))))
    continuous = len(quarters)>=2 and (stamp(quarters[-1]['start'])-stamp(quarters[-2]['period'])).days==1
    current_core = bool(quarters and inputs['period']==quarters[-1]['period'])
    floor('core_business_change','暂无法判断','Need continuous quarters and two same-length year-on-year comparisons; causality/competitive advantage are not inferred.')
    deterioration=False
    if usable and continuous and current_core and len(observed)==2:
        good=all(D(r['revenue_yoy'])>=0 and D(r['margin_change'])>=D('-.05') and D(r['operating_income'])>0 for r in observed)
        deterioration=all(D(r['revenue_yoy'])<0 and D(r['operating_income'])<0 for r in observed)
        refs=[dict(evidence_refs=r['evidence_refs']) for r in observed]
        floor('core_business_change','通过' if good else '警示',
              'Two comparable quarters show non-declining revenue, positive operating income and margin compression no worse than 5pp; observed-window screen only, no durable-moat or causal claim.' if good else
              'Comparable operating metrics show an adverse change; a short decline alone does not establish loss of competitive advantage.',refs)
        if not good: warnings.append('OBSERVED_CORE_OPERATING_CHANGE_WARNING')
        if deterioration: major.append('CONFIRMED_TWO_COMPARABLE_QUARTERS_REVENUE_DECLINE_AND_OPERATING_LOSS')
    if inputs['conflicts'] or inputs['rejected_refs']:
        major=[]; warnings=[]; deterioration=False
        floor('data_trust','暂无法判断','Quality facts contain rejected provenance/periods or conflicting aliases; affected quality conclusions remain UNKNOWN.')
        for key in ('financial_sustainability','profit_cash_support','core_business_change'):
            floor(key,'暂无法判断','Unresolved quality-input conflict or invalid evidence; no positive qualification.')
    blockers=[]
    for key,row in floors.items():
        if row['status']!='通过':blockers.append(key.upper()+'_'+('UNKNOWN' if row['status']=='暂无法判断' else 'WARNING'))
    blockers+=major
    catalog={r['record_id']:{k:r.get(k) for k in ('field','values','scope','period','fiscal_period_start','currency','unit','accounting_basis','published_at','fetched_at','publication_links')} for r in assessment.get('quality_field_records',[])}
    liquidity_screen = dict(status='UNKNOWN')
    if usable and not inputs['conflicts'] and b.get('current_assets') and b.get('current_liabilities'):
        ca, cl = val(b['current_assets']), val(b['current_liabilities'])
        liquidity_screen = dict(status='OBSERVATION_NOT_COMPREHENSIVE_FINANCIAL_STRENGTH',
            period=inputs['period'], currency='USD', unit='currency_units',
            current_assets=str(ca), current_liabilities=str(cl), buffer=str(ca-cl),
            current_ratio=str(ca/cl) if cl>0 else None,
            threshold='current_assets >= current_liabilities (buffer >= 0); other sustainability gates remain separate',
            evidence_refs=sorted(set(b['current_assets']['evidence_refs']+b['current_liabilities']['evidence_refs'])))
    legacy.update(liquidity_screen=liquidity_screen, evaluation_scope='RESEARCH_ONLY_US_GAAP_NONFINANCIAL_OBSERVED_WINDOW',
        positive_quality_path_available=True, rule_version=RULE_VERSION, floors=floors,
        status='OBSERVED_RULES_PASSED' if not blockers else 'WARNING' if warnings or major else 'PARTIAL_UNKNOWN',
        evidence_catalog=legacy['evidence_catalog']|catalog, financial_inputs=inputs,
        operating_observations=observed, capital_efficiency=inputs['capital_efficiency'], normalization=inputs['normalization'],
        warnings=warnings, major_risks=major, priority_blockers=blockers,
        fundamental_deterioration=dict(status='CONFIRMED_OBSERVED_OPERATING_DETERIORATION' if deterioration else 'NOT_ESTABLISHED',
            reason='Observed two comparable quarters of revenue decline and operating losses; cause/recovery remain UNKNOWN.' if deterioration else 'No causal or durable competitive-deterioration claim from these bounded metrics.'))
    return legacy
