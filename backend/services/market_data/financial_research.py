"""Pure L1 composition of existing Evidence and AV arithmetic, never a source owner.

Historical comparison is explicit; these records cannot grant L2-L4 permission.
No persistence, Provider, shares inference, corporate-action or order calculations.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math

from backend.services.market_data.evidence import number, stamp
from backend.services.market_data.av_fundamentals_service import _quarter_window, _complete_sum, statement_metrics

LABELS={
    'revenue_quarter':'季度收入','net_income_quarter':'季度合并净利润',
    'net_income_attributable_parent_quarter':'季度归母净利润',
    'net_income_available_to_ordinary_shareholders_quarter':'季度归属于普通股股东的收益',
    'operating_income_quarter':'季度营业利润','operating_cash_flow_quarter':'季度经营现金流',
    'diluted_eps_ads_quarter':'季度稀释每ADS收益','diluted_eps_ordinary_quarter':'季度稀释普通股收益',
    'vehicle_deliveries_quarter':'季度汽车交付量','vehicle_sales_quarter':'季度汽车销售收入',
    'vehicle_margin_pct':'季度汽车业务毛利率','vehicle_revenue_per_delivery_approx':'季度汽车收入/交付量近似值',
    'vehicle_deliveries_yoy_pct':'季度交付量同比','vehicle_sales_yoy_pct':'季度汽车收入同比',
    'vehicle_deliveries_qoq_pct':'季度交付量环比','vehicle_margin_change_pp':'汽车业务毛利率同比变化',
    'revenue_yoy_pct':'季度收入同比','net_income_yoy_pct':'季度合并净利润同比',
    'gross_margin_pct':'季度集团综合毛利率',
    'parent_income_yoy_pct':'季度归母净利润同比','net_margin_pct':'季度合并净利率',
    'ordinary_income_yoy_pct':'季度普通股股东收益同比',
    'operating_margin_pct':'季度营业利润率','cash_conversion':'经营现金流/合并净利润',
    'net_margin_prior_pct':'可比历史季度合并净利率','operating_margin_prior_pct':'可比历史季度营业利润率',
    'net_margin_change_pp':'合并净利率同比变化','operating_margin_change_pp':'营业利润率同比变化',
    'free_cash_flow_quarter':'季度OCF减已核验CapEx',
    'cost_of_revenue_reported_quarter':'报表营业成本（保留原符号）',
    'sales_marketing_reported_quarter':'报表销售营销费用（保留原符号）',
    'general_admin_reported_quarter':'报表管理费用（保留原符号）',
    'research_development_reported_quarter':'报表研发费用（保留原符号）',
    'income_tax_reported_quarter':'报表所得税项目（保留原符号）',
    'selling_general_admin_reported_quarter':'报表销售及管理合并费用（保留原符号，不分拆）',
    'diluted_eps_ttm':'四季度稀释EPS合计','pe_ttm':'基础稀释TTM PE',
    'earnings_yield_pct':'基础TTM盈利收益率','observed_price':'历史收盘观察价格',
    'fx_rate':'财报币/上市币汇率',
}
for _stem,_label in {'cost_of_revenue':'营业成本率','sales_marketing':'销售营销费用率',
                      'general_admin':'管理费用率','research_development':'研发费用率'}.items():
    LABELS[_stem+'_burden_pct']=_label
    LABELS[_stem+'_burden_prior_pct']='历史可比'+_label
    LABELS[_stem+'_burden_change_pp']=_label+'同比变化'


def _span(row):
    """Only independently established starts; never invent a quarter from its end."""
    try:
        start=date.fromisoformat(row['fiscal_period_start']);end=date.fromisoformat(row['period'])
        return (start,end) if 70<=(end-start).days+1<=105 else None
    except (ValueError,TypeError,KeyError):return None


def _same_span(left,right,*,prior_year=False):
    a,b=_span(left),_span(right)
    if not a or not b:return False
    if not prior_year:return a==b
    try:return a[0].replace(year=a[0].year-1)==b[0] and a[1].replace(year=a[1].year-1)==b[1]
    except ValueError:return False  # e.g. leap-day fiscal end; comparison unknown


def _continuous(rows):
    spans=sorted((_span(row) for row in rows),key=lambda s:s[0] if s else date.min)
    return len(rows)==4 and all(spans) and all(a[1]+timedelta(days=1)==b[0] for a,b in zip(spans,spans[1:]))


def build(assessment):
    """All calculations consume unique exact-basis evidence; incomplete stays unknown."""
    symbol=assessment.get('symbol')
    source=[r for r in assessment.get('verified_field_records',[])+assessment.get('historical_field_records',[])
            if r.get('quality')=='valid' and not r.get('is_seed') and not r.get('is_mock') and r.get('symbol')==symbol
            and isinstance(r.get('field'),str) and isinstance(r.get('values'),dict) and stamp(r.get('period'))]
    metrics=[];missing=[]
    def add(field,value,refs,*,period,currency=None,unit='ratio',details=None):
        if number(value) is None or not math.isfinite(value):return None
        refs=list(dict.fromkeys(refs))
        signature=json.dumps([symbol,field,value,period,currency,refs],sort_keys=True)
        row=dict(kind='research_metric',field=field,symbol=symbol,source='owner_calculation',
            record_id='calc:'+hashlib.sha256(signature.encode()).hexdigest()[:24],quality='valid',
            source_url=None,period=period,data_as_of=period,published_at=None,fetched_at=None,
            values={field:value},currency=currency,unit=unit,evidence_refs=refs,details=details or {},
            historical_only=False,is_seed=False,is_mock=False)
        metrics.append(row);return row
    selections=[]
    def document_keys(row):
        return {(link.get('record_id'),link.get('source_document_sha256'))
                for link in row.get('publication_links',[]) if link.get('record_id') and link.get('source_document_sha256')}
    def quantum(row):
        values={number(link.get('reporting_quantum')) for link in row.get('publication_links',[])}
        return next(iter(values)) if len(values)==1 and None not in values and next(iter(values))>0 else None
    def unique(field,end,currency=None,anchor=None):
        rows=[r for r in source if r.get('field')==field and r.get('scope')=='quarterly'
              and r.get('period')==end and (currency is None or r.get('currency')==currency)]
        identity=lambda r:(r.get('currency'),r.get('accounting_basis'),r.get('fiscal_period_start'),r.get('unit'),
                           r.get('ordinary_shares_per_ads'),r.get('issuer_type'))
        # Never reconcile a different unit/basis/span/ADS identity with money tolerance.
        if not rows or len({identity(r) for r in rows})!=1:return None
        amounts={number(r['values'].get(field)) for r in rows}
        if None in amounts:return None
        anchor_aliases=[r for r in source if anchor and r.get('field')==anchor.get('field') and r.get('period')==anchor.get('period')
                        and identity(r)==identity(anchor) and r['values'].get(r['field'])==anchor['values'].get(anchor['field'])]
        anchor_keys=set().union(*(document_keys(r) for r in anchor_aliases))
        paired=[r for r in rows if anchor and document_keys(r)&anchor_keys
                and _same_span(anchor,r,prior_year=True)]
        chosen=rows
        method='exact_alias'
        if len(amounts)>1:
            if paired and len({r['values'][field] for r in paired})==1:
                # A later statement's explicit comparison is its own presentation,
                # not proof that the earlier original had an identical value.
                chosen=paired;method='same_disclosure_comparative'
            else:
                if any(link.get('presentation_basis')=='as_restated' for r in rows for link in r.get('publication_links',[])):return None
                if not all(quantum(r) for r in rows):return None
                finest=min(rows,key=lambda r:quantum(r))
                exact=Decimal(str(finest['values'][field]))
                def rounds_to(r):
                    q=Decimal(str(quantum(r)))
                    return (exact/q).quantize(Decimal(1),rounding=ROUND_HALF_UP)*q==Decimal(str(r['values'][field]))
                if not all(rounds_to(r) for r in rows):return None
                chosen=[r for r in rows if quantum(r)==quantum(finest)];method='explicit_rounding_precision'
            selected=sorted(chosen,key=lambda r:r['record_id'])[0]
            q=quantum(selected)
            variants=[]
            for r in sorted(rows,key=lambda r:r['record_id']):
                v=r['values'][field];delta=v-selected['values'][field]
                rounds=False
                if q and quantum(r) and q!=quantum(r):
                    coarse=max(q,quantum(r));fine=selected if q<quantum(r) else r
                    fine_v=Decimal(str(fine['values'][field]));coarse_v=selected['values'][field] if q==coarse else v
                    rounds=(fine_v/Decimal(str(coarse))).quantize(Decimal(1),rounding=ROUND_HALF_UP)*Decimal(str(coarse))==Decimal(str(coarse_v))
                declared=any(link.get('presentation_basis')=='as_restated' for link in selected.get('publication_links',[]))
                variants.append(dict(evidence_ref=r['record_id'],value=v,difference_from_selected=delta,
                    reporting_quantum=quantum(r),source_url=r.get('source_url'),
                    presentation_basis=sorted({link.get('presentation_basis','UNKNOWN') for link in r.get('publication_links',[])}),
                    relation='identical' if not delta else 'declared_restatement' if declared else 'rounding_compatible' if rounds else 'unexplained_comparative_difference'))
            note=dict(field=field,period=end,currency=selected.get('currency'),method=method,
                      selected_ref=selected['record_id'],selected_value=selected['values'][field],variants=variants)
            if note not in selections:selections.append(note)
        elif paired:
            chosen=paired
        return sorted(chosen,key=lambda r:r['record_id'])[0],list(dict.fromkeys(r['record_id'] for r in chosen))
    current=sorted({r['period'] for r in source if not r.get('historical_only') and r.get('scope')=='quarterly'},reverse=True)
    latest=current[0] if current else None
    for field,growth in [('revenue_quarter','revenue_yoy_pct'),('net_income_quarter','net_income_yoy_pct'),
                         ('net_income_attributable_parent_quarter','parent_income_yoy_pct'),
                         ('net_income_available_to_ordinary_shareholders_quarter','ordinary_income_yoy_pct')]:
        row=unique(field,latest)
        if not row:missing.append(field+': CURRENT_EXACT_BASIS_UNKNOWN');continue
        r,refs=row;value=number(r['values'][field]);cur=r['currency']
        try:
            end=date.fromisoformat(latest);prior_end=end.replace(year=end.year-1).isoformat()
        except ValueError:
            missing.append(growth+': COMPARABLE_PRIOR_QUARTER_UNKNOWN');continue
        prior=unique(field,prior_end,cur,anchor=r)
        if prior and prior[0].get('accounting_basis')==r.get('accounting_basis') and _same_span(r,prior[0],prior_year=True):
            previous=number(prior[0]['values'][field])
            if previous>0:
                add(growth,(value/previous-1)*100,refs+prior[1],period=latest,unit='percent',
                    details=dict(comparison_period=prior_end,formula='(current/prior - 1)*100'))
            else:missing.append(growth+': PRIOR_NONPOSITIVE; loss/profit crossover is not normal growth')
        else:missing.append(growth+': COMPARABLE_PRIOR_QUARTER_UNKNOWN')
    # Automotive observations are issuer-neutral and never inferred from group revenue.
    for field,stem in [('vehicle_deliveries_quarter','vehicle_deliveries'),('vehicle_sales_quarter','vehicle_sales')]:
        now=unique(field,latest)
        if not now:continue
        current_value=number(now[0]['values'][field])
        if current_value is None or current_value<=0:continue
        for suffix,prior_end in [('yoy',date.fromisoformat(latest).replace(year=date.fromisoformat(latest).year-1).isoformat()),
                                 ('qoq',(date.fromisoformat(now[0]['fiscal_period_start'])-timedelta(days=1)).isoformat())]:
            prior=unique(field,prior_end,now[0].get('currency'),anchor=now[0] if suffix=='yoy' else None)
            if (prior and prior[0].get('accounting_basis')==now[0].get('accounting_basis')
                    and (suffix!='qoq' or date.fromisoformat(prior[0]['period'])+timedelta(days=1)
                         == date.fromisoformat(now[0]['fiscal_period_start']))):
                previous=number(prior[0]['values'][field])
                if previous and previous>0:
                    add(stem+'_'+suffix+'_pct',(current_value/previous-1)*100,now[1]+prior[1],period=latest,unit='percent',
                        details=dict(formula='(current/prior - 1)*100',comparison_period=prior_end))
    vehicle_sales=unique('vehicle_sales_quarter',latest)
    deliveries=unique('vehicle_deliveries_quarter',latest)
    if vehicle_sales and deliveries and _same_span(vehicle_sales[0],deliveries[0]):
        count=number(deliveries[0]['values']['vehicle_deliveries_quarter'])
        if count and count>0:
            add('vehicle_revenue_per_delivery_approx',vehicle_sales[0]['values']['vehicle_sales_quarter']/count,
                vehicle_sales[1]+deliveries[1],period=latest,currency=vehicle_sales[0]['currency'],unit='currency_per_vehicle',
                details=dict(formula='quarter vehicle sales revenue / reported quarter deliveries',
                    caveat='Delivery cohorts need not match revenue recognition; not a transaction ASP or price/mix proof'))
    margin=unique('vehicle_margin_pct',latest)
    if margin:
        try:
            prior_end=date.fromisoformat(latest).replace(year=date.fromisoformat(latest).year-1).isoformat()
            before=unique('vehicle_margin_pct',prior_end,anchor=margin[0])
            if before and _same_span(margin[0],before[0],prior_year=True):
                add('vehicle_margin_change_pp',margin[0]['values']['vehicle_margin_pct']-before[0]['values']['vehicle_margin_pct'],
                    margin[1]+before[1],period=latest,unit='percentage_points',details=dict(formula='current vehicle gross margin - prior vehicle gross margin'))
        except ValueError:pass
    revenue=unique('revenue_quarter',latest)
    income=unique('net_income_quarter',latest)
    cost=unique('cost_of_revenue_reported_quarter',latest,revenue[0]['currency']) if revenue else None
    if (revenue and cost and _same_span(revenue[0],cost[0])
            and document_keys(revenue[0]) & document_keys(cost[0])):
        rev=revenue[0]['values']['revenue_quarter']
        signed_cost=cost[0]['values']['cost_of_revenue_reported_quarter']
        if rev>0 and signed_cost<0 and 0<=rev+signed_cost<=rev:
            add('gross_margin_pct',(rev+signed_cost)/rev*100,revenue[1]+cost[1],
                period=latest,unit='percent',details=dict(
                    formula='(quarter group revenue + reported signed group cost of sales) / quarter group revenue * 100',
                    caveat='Group gross margin; not vehicle business gross margin'))
    for numerator,out in [('net_income_quarter','net_margin_pct'),('operating_income_quarter','operating_margin_pct')]:
        n=unique(numerator,latest,revenue[0]['currency']) if revenue else None
        if revenue and n and _same_span(revenue[0],n[0]) and revenue[0]['values']['revenue_quarter']>0:
            add(out,n[0]['values'][numerator]/revenue[0]['values']['revenue_quarter']*100,
                n[1]+revenue[1],period=latest,unit='percent',details=dict(formula=numerator+'/revenue_quarter*100'))
            try:prior_end=date.fromisoformat(latest).replace(year=date.fromisoformat(latest).year-1).isoformat()
            except ValueError:continue
            pr=unique('revenue_quarter',prior_end,revenue[0]['currency'],anchor=revenue[0]);pn=unique(numerator,prior_end,revenue[0]['currency'],anchor=n[0])
            if (pr and pn and _same_span(pr[0],pn[0]) and _same_span(revenue[0],pr[0],prior_year=True)
                    and pr[0].get('accounting_basis')==revenue[0].get('accounting_basis')
                    and pn[0].get('accounting_basis')==n[0].get('accounting_basis') and pr[0]['values']['revenue_quarter']>0):
                before=pn[0]['values'][numerator]/pr[0]['values']['revenue_quarter']*100
                prefix='net_margin' if numerator=='net_income_quarter' else 'operating_margin'
                prior=add(prefix+'_prior_pct',before,pn[1]+pr[1],period=prior_end,unit='percent')
                if prior:prior['historical_only']=True
                add(prefix+'_change_pp',n[0]['values'][numerator]/revenue[0]['values']['revenue_quarter']*100-before,
                    n[1]+revenue[1]+pn[1]+pr[1],period=latest,unit='percentage_points',details=dict(comparison_period=prior_end,formula='current margin - comparable prior margin'))
    ocf=unique('operating_cash_flow_quarter',latest,income[0]['currency']) if income else None
    if ocf and income and _same_span(ocf[0],income[0]) and income[0]['values']['net_income_quarter']>0:
        add('cash_conversion',ocf[0]['values']['operating_cash_flow_quarter']/income[0]['values']['net_income_quarter'],
            ocf[1]+income[1],period=latest,details=dict(formula='OCF/consolidated net income; negative OCF is retained'))
    cash=unique('operating_cash_flow_quarter',latest)
    capex=unique('capital_expenditures_quarter',latest,cash[0]['currency']) if cash else None
    # Product's retained zero-CapEx policy: zero still needs independent absence
    # semantics, not merely a vendor/statement zero. Do not fabricate FCF from it.
    if cash and capex and _same_span(cash[0],capex[0]) and capex[0]['values']['capital_expenditures_quarter']>0:
        add('free_cash_flow_quarter',cash[0]['values']['operating_cash_flow_quarter']-capex[0]['values']['capital_expenditures_quarter'],
            cash[1]+capex[1],period=latest,currency=cash[0]['currency'],unit='currency_units',
            details=dict(formula='quarter OCF - verified positive CapEx; not all shareholder-distributable cash'))
    else:
        missing.append('QUARTER_FCF_CAPEX_ZERO_MISSING_OR_PERIOD_UNVERIFIED')
    # Expense directions are not guessed from a negative cell: only a complete
    # exact operating-profit bridge certifies deduction magnitudes/ratios.
    expense_fields=['cost_of_revenue_reported_quarter','sales_marketing_reported_quarter',
                    'general_admin_reported_quarter','research_development_reported_quarter']
    expense_ratios={}
    if latest:
        try:prior_end=date.fromisoformat(latest).replace(year=date.fromisoformat(latest).year-1).isoformat()
        except ValueError:prior_end=None
        for end in (latest,prior_end):
            rev=unique('revenue_quarter',end,anchor=revenue[0] if revenue and end!=latest else None);op=unique('operating_income_quarter',end,rev[0]['currency'],anchor=revenue[0] if revenue and end!=latest else None) if rev else None
            costs=[unique(f,end,rev[0]['currency'],anchor=revenue[0] if revenue and end!=latest else None) if rev else None for f in expense_fields]
            if not (rev and op and all(costs) and rev[0]['values']['revenue_quarter']>0):continue
            if not all(_same_span(rev[0],row[0]) for row in [op,*costs]):continue
            reported=[row[0]['values'][field] for field,row in zip(expense_fields,costs)]
            if not all(v<=0 for v in reported) or rev[0]['values']['revenue_quarter']+sum(reported)!=op[0]['values']['operating_income_quarter']:continue
            bridge_refs=rev[1]+op[1]+[ref for row in costs for ref in row[1]]
            for field,row,value in zip(expense_fields,costs,reported):
                key=field.replace('_reported_quarter','')+'_burden_pct'
                ratio=-value/rev[0]['values']['revenue_quarter']*100
                metric=add(key if end==latest else key.replace('_pct','_prior_pct'),ratio,bridge_refs,period=end,unit='percent',
                    details=dict(formula='-reported deduction/revenue*100; revenue + four deductions = operating income',reported_signed_value=value))
                if metric:metric['historical_only']=end!=latest
                expense_ratios[field,end]=(ratio,bridge_refs)
        for field in expense_fields:
            now=expense_ratios.get((field,latest));before=expense_ratios.get((field,prior_end))
            current=unique(field,latest);prior=unique(field,prior_end,anchor=current[0] if current else None)
            if (now and before and current and prior and _same_span(current[0],prior[0],prior_year=True)
                    and current[0]['currency']==prior[0]['currency'] and current[0]['accounting_basis']==prior[0]['accounting_basis']):
                add(field.replace('_reported_quarter','')+'_burden_change_pp',now[0]-before[0],now[1]+before[1],
                    period=latest,unit='percentage_points',details=dict(comparison_period=prior_end,formula='current expense burden - prior comparable burden'))
    # Original AV statement arithmetic also serves SEC-verified components.
    # Missing fields stay None rather than borrowing incompatible net income.
    income_rows=[];cash_rows=[];component_refs={};component_rows={}
    currencies={r.get('currency') for r in source if r.get('period')==latest}
    if len(currencies)==1:
        cur=next(iter(currencies))
        ends=sorted({r['period'] for r in source if r.get('scope')=='quarterly'},reverse=True)
        for end in ends:
            ir=dict(fiscalDateEnding=end,reportedCurrency=cur);cr=dict(ir)
            for field,key,target in [('revenue_quarter','totalRevenue',ir),('net_income_quarter','netIncome',ir),
                                     ('operating_cash_flow_quarter','operatingCashflow',cr),('capital_expenditures_quarter','capitalExpenditures',cr)]:
                chosen=unique(field,end,cur)
                if chosen:
                    target[key]=chosen[0]['values'][field]
                    component_refs[key,end]=chosen[1]
                    component_rows[key,end]=chosen[0]
            income_rows.append(ir);cash_rows.append(cr)
        computed,provenance,_=statement_metrics(symbol or '',{'quarterlyReports':income_rows},{'quarterlyReports':cash_rows},fetched_at=None)
        dependencies={'revenue_ttm':['totalRevenue'],'net_income_ttm':['netIncome'],
            'operating_cash_flow_ttm':['operatingCashflow'],'capital_expenditures_ttm':['capitalExpenditures'],
            'free_cash_flow_ttm':['operatingCashflow','capitalExpenditures']}
        for field,keys in dependencies.items():
            meta=provenance.get(field,{})
            periods=meta.get('fiscal_quarter_ends',[])
            if field not in computed or not periods or max(periods)!=latest:continue
            if any(not _continuous([component_rows.get((key,end),{}) for end in periods]) for key in keys):continue
            if len(keys)>1 and any(not _same_span(component_rows[keys[0],end],component_rows[key,end]) for key in keys[1:] for end in periods):continue
            refs=[ref for key in keys for end in periods for ref in component_refs.get((key,end),[])]
            add(field,computed[field],refs,period=latest,currency=cur,unit='currency_units',
                details=dict(formula='original AV statement_metrics',quarter_ends=periods))
    # Same canonical validated bars; no asset snapshots or broker calls required.
    day=(assessment.get('technical_levels') or {}).get('day')
    price=None
    if day and day.get('bars'):
        close=number(day['bars'][-1].get('close'))
        if close and close>0:
            price=add('observed_price',close,[day['record_id']],period=day['end_at'],currency=day['currency'],unit='price',
                details=dict(basis='last validated daily close; not live or an order limit',technical_period='day'))
    if price is None:
        quote=next((r for r in assessment.get('records',[]) if r.get('kind')=='quote' and r.get('quality')=='valid'
                    and not r.get('is_seed') and not r.get('is_mock') and r.get('symbol')==symbol),None)
        amount=number((quote or {}).get('values',{}).get('current_price'))
        ref=(quote or {}).get('record_id') or (quote or {}).get('source_url')
        native={'US':'USD','HK':'HKD'}.get((symbol or '').rsplit(':',1)[-1])
        if quote and amount and amount>0 and ref and stamp(quote.get('data_as_of')) and quote['values'].get('currency')==native and native:
            price=add('observed_price',amount,[ref],
                period=quote['data_as_of'],currency=native,unit='price',
                details=dict(basis='independently qualified quote observation, not a daily series or order limit'))
    # Reuse the existing four-quarter window + summation. Never annualize Q2.
    eps=None
    for field in ('diluted_eps_ads_quarter','diluted_eps_ordinary_quarter'):
        rows=[];components=[]
        for end in sorted({r['period'] for r in source if r['field']==field},reverse=True):
            chosen=unique(field,end)
            if chosen:
                r,refs=chosen
                rows.append(dict(fiscalDateEnding=end,reportedCurrency=r['currency'],value=r['values'][field]))
                components.append((r,refs))
        window=_quarter_window({'quarterlyReports':rows})
        if not window or window[0]['fiscalDateEnding']!=latest:continue
        ends={r['fiscalDateEnding'] for r in window}
        selected=[c for c in components if c[0]['period'] in ends]
        if not _continuous([r for r,_ in selected]):
            missing.append('TTM_EPS_PERIOD_START_OR_CONTINUITY_UNKNOWN');continue
        # Ordinary EPS requires an explicit domestic financial identity; ADS EPS
        # is already per listed ADS, and all ratios must be explicitly consistent.
        ratios={r.get('ordinary_shares_per_ads') for r,_ in selected}
        if field.endswith('ads_quarter') and (len(ratios)!=1 or not all(number(x) and x>0 for x in ratios)):
            missing.append('ADS_RATIO_OR_CHANGE_UNKNOWN');continue
        if field.endswith('ordinary_quarter') and not all(r.get('issuer_type')=='domestic' for r,_ in selected):
            missing.append('LISTED_ORDINARY_SHARE_IDENTITY_UNKNOWN');continue
        eps=add('diluted_eps_ttm',_complete_sum(window,'value'),[ref for _,refs in selected for ref in refs],
            period=latest,currency=window[0]['reportedCurrency'],unit='currency_per_listed_unit',
            details=dict(formula='sum_four_consecutive_quarters',periods=sorted(ends),
                share_basis=field,ordinary_shares_per_ads=list(ratios),
                limitation='sum of period diluted EPS; share-count changes may limit comparability'))
        break
    if not eps:missing.append('TTM_EPS_COMPONENTS_OR_LISTED_SHARE_BASIS_UNKNOWN')
    if eps and price:
        e=eps['values']['diluted_eps_ttm'];rate=1;fxrefs=[]
        if eps['currency']!=price['currency']:
            fx=assessment.get('research_fx') or {}
            fxdate=stamp(fx.get('rate_date'));pricedate=stamp(price['period'])
            if (fx.get('from_currency')==price['currency'] and fx.get('to_currency')==eps['currency']
                    and fx.get('quality') in {'current','historical'} and fx.get('source','').startswith('provider')
                    and fxdate and pricedate and 0<=(pricedate-fxdate).total_seconds()<=4*86400
                    and number(fx.get('rate')) and fx['rate']>0):
                rate=fx['rate']
                f=add('fx_rate',rate,[],period=fx['rate_date'],unit='currency_ratio',details=fx)
                fxrefs=[f['record_id']]
            else:rate=None;missing.append('VALUATION_FX_DATE_OR_CURRENCY_UNKNOWN')
        if e>0 and rate:
            pe=price['values']['observed_price']*rate/e
            if math.isfinite(pe) and pe>0:
                add('pe_ttm',pe,[price['record_id'],eps['record_id']]+fxrefs,period=price['period'],unit='multiple',
                    details=dict(formula='listed price * reporting_currency_per_listed_currency / diluted TTM EPS',
                        financial_end=latest,price_date=price['period'],limitation='historical static ratio, not fair value; share-count changes/splits after the financial periods are unverified'))
                add('earnings_yield_pct',100/pe,[price['record_id'],eps['record_id']]+fxrefs,period=price['period'],unit='percent')
            else:missing.append('PE_COMPUTATION_NONFINITE')
        elif e<=0:missing.append('PE_NOT_APPLICABLE_NONPOSITIVE_EARNINGS')
    missing.extend(['MARKET_CAP_CURRENT_SHARE_COUNT_UNKNOWN','NET_CASH_RESTRICTED_FUNDS_DEBT_ATTRIBUTION_UNKNOWN',
                    'SEGMENT_PROFIT_AND_SHARE_NOT_INFERABLE_FROM_GROUP_ACCOUNTS'])
    pe=next((r for r in metrics if r['field']=='pe_ttm'),None)
    sensitivity=[]
    if pe:
        # Explicit assumptions, never facts or automatically chosen fair multiples.
        for earnings_factor in (.8,1.,1.2):
            for multiple_factor in (.8,1.,1.2):
                sensitivity.append(dict(earnings_factor=earnings_factor,multiple_factor=multiple_factor,
                    hypothetical_price=price['values']['observed_price']*earnings_factor*multiple_factor,
                    currency=price['currency'],basis='illustrative earnings x multiple, fixed FX; not forecast/target/order',
                    evidence_refs=[pe['record_id'],price['record_id']]))
    used_refs={ref for metric in metrics for ref in metric['evidence_refs']}
    selections=[note for note in selections if note['selected_ref'] in used_refs]
    for metric in metrics:
        relevant=[n for n in selections if n['selected_ref'] in metric['evidence_refs']]
        if relevant:metric['details']['comparative_selections']=relevant
    if selections:missing.append('COMPARATIVE_PRESENTATIONS_DIFFER; selected basis and all original values retained, unexplained differences are not certified as rounding')
    return dict(records=metrics,limitations=sorted(set(missing)),valuation_sensitivity=sensitivity,comparative_selections=selections)


def display(row,field):
    value=number(row.get('values',{}).get(field))
    if value is None:return None
    unit=row.get('unit');suffix='%' if unit=='percent' else '个百分点' if unit=='percentage_points' else '倍' if unit in {'ratio','multiple'} else ''
    label=LABELS.get(field,field)
    caveat='；静态估算，股本/拆股变更未核验，非当前精确估值' if field in {'pe_ttm','earnings_yield_pct'} and row.get('source')=='owner_calculation' else ''
    return f"{label}（{row.get('period') or row.get('data_as_of')}，{row.get('currency') or '无币种比率'}）{value:,.4f}{suffix}{caveat}"
