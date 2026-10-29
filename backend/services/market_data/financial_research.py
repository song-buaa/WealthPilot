"""Pure L1 composition of existing Evidence and AV arithmetic, never a source owner.

Historical comparison is explicit; these records cannot grant L2-L4 permission.
No persistence, Provider, shares inference, corporate-action or order calculations.
"""
from datetime import date, timedelta
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
    'revenue_yoy_pct':'季度收入同比','net_income_yoy_pct':'季度合并净利润同比',
    'parent_income_yoy_pct':'季度归母净利润同比','net_margin_pct':'季度合并净利率',
    'ordinary_income_yoy_pct':'季度普通股股东收益同比',
    'operating_margin_pct':'季度营业利润率','cash_conversion':'经营现金流/合并净利润',
    'net_margin_prior_pct':'可比历史季度合并净利率','operating_margin_prior_pct':'可比历史季度营业利润率',
    'net_margin_change_pp':'合并净利率同比变化','operating_margin_change_pp':'营业利润率同比变化',
    'free_cash_flow_quarter':'季度OCF减已核验CapEx',
    'diluted_eps_ttm':'四季度稀释EPS合计','pe_ttm':'基础稀释TTM PE',
    'earnings_yield_pct':'基础TTM盈利收益率','observed_price':'历史收盘观察价格',
    'fx_rate':'财报币/上市币汇率',
}


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
    def unique(field,end,currency=None):
        rows=[r for r in source if r.get('field')==field and r.get('scope')=='quarterly'
              and r.get('period')==end and (currency is None or r.get('currency')==currency)]
        identities={(r.get('currency'),r.get('accounting_basis'),number(r['values'].get(field)),r.get('fiscal_period_start'),r.get('unit'),
                     r.get('ordinary_shares_per_ads'),r.get('issuer_type')) for r in rows}
        # Equal source aliases survive in evidence_refs; competing values/bases do not.
        if len(identities)!=1 or not rows or next(iter(identities))[2] is None:return None
        return rows[0],list(dict.fromkeys(r['record_id'] for r in rows))
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
        prior=unique(field,prior_end,cur)
        if prior and prior[0].get('accounting_basis')==r.get('accounting_basis') and _same_span(r,prior[0],prior_year=True):
            previous=number(prior[0]['values'][field])
            if previous>0:
                add(growth,(value/previous-1)*100,refs+prior[1],period=latest,unit='percent',
                    details=dict(comparison_period=prior_end,formula='(current/prior - 1)*100'))
            else:missing.append(growth+': PRIOR_NONPOSITIVE; loss/profit crossover is not normal growth')
        else:missing.append(growth+': COMPARABLE_PRIOR_QUARTER_UNKNOWN')
    revenue=unique('revenue_quarter',latest)
    income=unique('net_income_quarter',latest)
    for numerator,out in [('net_income_quarter','net_margin_pct'),('operating_income_quarter','operating_margin_pct')]:
        n=unique(numerator,latest,revenue[0]['currency']) if revenue else None
        if revenue and n and _same_span(revenue[0],n[0]) and revenue[0]['values']['revenue_quarter']>0:
            add(out,n[0]['values'][numerator]/revenue[0]['values']['revenue_quarter']*100,
                n[1]+revenue[1],period=latest,unit='percent',details=dict(formula=numerator+'/revenue_quarter*100'))
            try:prior_end=date.fromisoformat(latest).replace(year=date.fromisoformat(latest).year-1).isoformat()
            except ValueError:continue
            pr=unique('revenue_quarter',prior_end,revenue[0]['currency']);pn=unique(numerator,prior_end,revenue[0]['currency'])
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
    return dict(records=metrics,limitations=sorted(set(missing)),valuation_sensitivity=sensitivity)


def display(row,field):
    value=number(row.get('values',{}).get(field))
    if value is None:return None
    unit=row.get('unit');suffix='%' if unit=='percent' else '个百分点' if unit=='percentage_points' else '倍' if unit in {'ratio','multiple'} else ''
    label=LABELS.get(field,field)
    caveat='；静态估算，股本/拆股变更未核验，非当前精确估值' if field in {'pe_ttm','earnings_yield_pct'} and row.get('source')=='owner_calculation' else ''
    return f"{label}（{row.get('period') or row.get('data_as_of')}，{row.get('currency') or '无币种比率'}）{value:,.4f}{suffix}{caveat}"
