"""Independent P2 arithmetic/rank verifier. Standard library only, no services."""
from copy import deepcopy
from decimal import Decimal as D, localcontext
from datetime import datetime
import hashlib
import json
from pathlib import Path


def close(actual,expected):
    assert abs(D(str(actual))-expected)<D('.00000002'), ('P2 arithmetic differs',actual,str(expected))


def settlement(eps,price,g,shares,pe,rate):
    terminal=eps*((1+g)/(1+shares))**5*pe
    return dict(terminal_price=terminal,terminal_wealth=terminal,dividends_cash=D(0),
        annualized_return=(terminal/price)**(D(1)/5)-1,total_return=terminal/price-1,
        conditional_value=terminal/(1+rate)**5,margin_to_conditional_value=1-price/(terminal/(1+rate)**5),
        implied_terminal_eps_no_dividends=price*(1+rate)**5/pe,
        implied_earnings_growth_no_dividends=(price*(1+rate)**5/pe/eps)**(D(1)/5)*(1+shares)-1)


def path_value(eps,price,b,params,rate):
    growth=[D(x) for x in params['revenue_growth']]
    assert len(growth)==5 and all(D('-.5')<=x<=1 for x in growth)
    assert D(params.get('dividend_payout','0'))==0
    m0,c0=D(b['operating_margin']),D(b['net_to_operating_conversion'])
    m,c=D(params['terminal_operating_margin']),D(params['terminal_conversion'])
    assert 0<m<=D('.8') and 0<c<=D('1.2')
    share=D(params['annual_share_change']);assert D('-.1')<=share<=D('.2')
    pe=D(params['terminal_pe']);assert 0<pe<=100
    sales=D(1);years=[]
    for t,g in enumerate(growth,1):
        sales*=1+g;mt=m0+(m-m0)*t/5;ct=c0+(c-c0)*t/5
        profit=sales*(mt/m0)*(ct/c0);units=(1+share)**t
        years.append(dict(year=t,revenue_index=sales,operating_margin=mt,net_to_operating_conversion=ct,
            earnings_index=profit,relative_shares=units,eps=eps*profit/units,dividend=D(0)))
    compound=profit**(D(1)/5)-1
    assert D('-.5')<=compound<=1
    value=settlement(eps,price,compound,share,pe,rate)
    needed=price*(1+rate)**5/eps/pe*(1+share)**5/(m/m0)/(c/c0)
    value['implied_revenue_cagr_for_required_return']=needed**(D(1)/5)-1
    value['equivalent_aggregate_earnings_cagr']=compound
    return value,years,dict(earnings_growth=str(compound),annual_share_change=str(share),terminal_pe=str(pe),dividend_payout='0')


def eight_values(eps,price,base,rate):
    values=[]
    for field,offsets in [('earnings_growth',('-.02','.02')),('terminal_pe',('-.2','.2')),
                           ('annual_share_change',('-.01','.01')),('required_return',('-.02','.02'))]:
        for delta in offsets:
            params={k:D(v) for k,v in base.items()};required=rate
            if field=='required_return':required+=D(delta)
            elif field=='terminal_pe':params[field]*=1+D(delta)
            else:params[field]+=D(delta)
            value=None
            if D('-.5')<=params['earnings_growth']<=1 and D('-.1')<=params['annual_share_change']<=D('.2') and 0<params['terminal_pe']<=100:
                value=settlement(eps,price,params['earnings_growth'],params['annual_share_change'],params['terminal_pe'],required)
            values.append((field,D(delta),params,required,value))
    return values


def joint_values(eps,price,baseline,params,rate,bounds=None):
    cells=[]
    for g in ('-.03','0','.03'):
        for pe in ('-.2','0','.2'):
            changed=deepcopy(params)
            changed['revenue_growth']=[str(D(v)+D(g)) for v in params['revenue_growth']]
            changed['terminal_pe']=str(D(params['terminal_pe'])*(1+D(pe)))
            try:
                value,years,compiled=path_value(eps,price,baseline,changed,rate)
                if bounds:assert bounds[0]<=D(compiled['earnings_growth'])<=bounds[1]
            except AssertionError:value,years,compiled=None,None,None
            cells.append((g,pe,changed,value,years,compiled))
    return cells


def method_blocks(cells,rate):
    if len(cells)!=9 or any(v[3] is None for v in cells):return ['JOINT_SENSITIVITY_INCOMPLETE']
    if any(v[3]['annualized_return'].quantize(D('.00000001'))<rate for v in cells):return ['JOINT_STRESS_RETURN_THRESHOLD_CROSSING']
    return []


def ranks(cards,ranking):
    groups={};out={}
    for c in cards:
        if not c['scenarios']:out[c['symbol']]=[None,None];continue
        base=D(c['scenarios']['base']['annualized_return']);down=D(c['scenarios']['cautious']['total_return']);rate=D(c['assumptions']['required_return'])
        vals=c['sensitivity'];complete=len(vals)==8 and all(v.get('result') for v in vals)
        returns=[D(v['result']['annualized_return']) for v in vals if v.get('result')]
        robust=complete and all(D(v['result']['annualized_return'])>=D(v['required_return']) for v in vals)
        high=bool(returns and max(returns)-min(returns)>D('.05'))
        group=('暂不优先' if c['quality']['major_risks'] or base<rate or down<D(ranking['downside_floor']) else
            '继续观察' if c['quality']['priority_blockers'] or c.get('research_method_blockers') or not complete or (high and not robust) else '优先考虑')
        groups.setdefault(group,[]).append(c)
    for group,items in groups.items():
        items.sort(key=lambda c:(-D(c['scenarios']['base']['annualized_return']),-D(c['scenarios']['cautious']['total_return']),c['symbol']))
        anchor=down_anchor=evidence_anchor=None;rank=0
        for i,c in enumerate(items,1):
            value=D(c['scenarios']['base']['annualized_return']);down=D(c['scenarios']['cautious']['total_return']);evidence=tuple(v['status'] for v in c['quality']['floors'].values())
            if anchor is None or anchor-value>D(ranking['tie_band']) or abs(down_anchor-down)>D(ranking['downside_tie_band']) or evidence!=evidence_anchor:
                anchor,down_anchor,evidence_anchor,rank=value,down,evidence,i
            out[c['symbol']]=[group,rank]
    return out


def verify_operating(path,parent_verifier):
    assert __debug__
    path=Path(path);b=json.loads(path.read_text());p=b['payload'];canonical=lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    assert hashlib.sha256(canonical(p).encode()).hexdigest()==b['artifact_id']
    for relative,expected in p['source_files'].items():
        f=(path.parent/'sources'/relative).resolve();assert (path.parent/'sources').resolve() in f.parents
        assert hashlib.sha256(f.read_bytes()).hexdigest()==expected
    for relative,expected in p['view_hashes'].items():
        f=(path.parent/relative).resolve();assert path.parent.resolve() in f.parents
        assert hashlib.sha256(f.read_bytes()).hexdigest()==expected
    parent_path=path.parent/'sources/baseline/bundle.json';parent=parent_verifier(parent_path)
    assert parent['artifact_id']==p['parent_artifact_id']==p['dossier']['parent_artifact_id']
    old=json.loads(parent_path.read_text())['payload'];oldcards={c['symbol']:c for c in old['result']['cards']}
    manifest=json.loads((parent_path.parent/'sources/inputs/manifest.json').read_text())
    assert datetime.fromisoformat(p['dossier']['research_recorded_at'])>=datetime.fromisoformat(p['request']['decision_at'])
    assert p['result']['decision_at']==old['result']['decision_at']
    checked=0;changes=[]
    original_ranks={c['symbol']:[c['group'],c['rank']] for c in p['result']['cards']}
    with localcontext() as ctx:
        ctx.prec=40
        for card in p['result']['cards']:
            symbol=card['symbol'];oc=oldcards[symbol]
            assert card['quality']==oc['quality'] and card['cross_checks']==oc['cross_checks']
            context=card.get('calibration_evidence')
            if context:
                from zoneinfo import ZoneInfo
                cutoff=datetime.fromisoformat(p['result']['decision_at'])
                entry=manifest['companies'][symbol]
                inputs=parent_path.parent/'sources/inputs'
                allowed_context={
                    'AAPL:US':'https://www.apple.com/newsroom/2026/11/apple-reports-fourth-quarter-results/',
                    'MSFT:US':'https://www.microsoft.com/en-us/Investor/events/fy-2027/earnings-fy-2027-q1',
                    'NVDA:US':'https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/'}
                for proof in context.get('research_documents',[]):
                    assert proof['source_ref']==allowed_context[symbol]
                    assert proof['known_at']==proof['fetched_at']
                    assert datetime.fromisoformat(proof['known_at'])<=cutoff
                    assert proof['publication_clock']=='UNKNOWN_MUTABLE_PAGE_CAPTURE_CLOCK_ONLY'
                    receipt=next(r for r in manifest['requests'] if r.get('url')==proof['source_ref'] and r.get('sha256')==proof['source_sha256'])
                    assert receipt['fetched_at']==proof['fetched_at'] and receipt['owner']=='ISSUER_IR_RESEARCH_CONTEXT_NOT_GAAP'
                    assert receipt['http_status']==200 and receipt['final_url']==proof['source_ref']
                    assert hashlib.sha256((inputs/receipt['path']).read_bytes()).hexdigest()==proof['source_sha256']
                for proof in context['disclosures']+context['historical_pe']['samples']:
                    assert datetime.fromisoformat(proof['published_at'])<=datetime.fromisoformat(proof['fetched_at'])<=cutoff
                    assert any(r['sha256']==proof['source_sha256'] and r['url']==proof.get('source_ref') for r in entry['originals'])
                price=json.loads((inputs/entry['price']['path']).read_text())['Time Series (Daily)']
                splits=json.loads((inputs/entry['splits']['path']).read_text())['data']
                for sample in context['historical_pe']['samples']:
                    public=datetime.fromisoformat(sample['published_at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
                    day=min(d for d in price if d>public)
                    assert sample['price_date']==day
                    closed=datetime.fromisoformat(day+'T16:00:00').replace(tzinfo=ZoneInfo('America/New_York'))
                    assert closed.weekday()<5 and closed<=min(cutoff,datetime.fromisoformat(sample['price_fetched_at']))
                    assert not any(sample['start']<=s['effective_date']<=day and D(s['split_factor'])!=1 for s in splits)
                    assert sample['price_source_sha256']==entry['price']['sha256'] and sample['split_source_sha256']==entry['splits']['sha256']
                    close(sample['price'],D(price[day]['4. close']))
                    close(sample['pe'],D(price[day]['4. close'])/D(sample['diluted_eps']))
            unit=p['dossier']['companies'][symbol].get('return_unit_qualification','NO_KNOWN_UNRESOLVED_DISTRIBUTION')
            if unit!='NO_KNOWN_UNRESOLVED_DISTRIBUTION':
                assert not card['scenarios'] and card['rank'] is None and card['comparison_status']=='NOT_COMPARABLE'
                assert card['failure_reason']==unit
                assert card['assumptions'] is None and not {'conditional_value_range','formula','assumption_context'} & card.keys()
                assert card['unexecuted_operating_profile']['status']=='UNEXECUTED_NOT_CURRENT_VALUATION'
            if not card['scenarios']:assert card['comparison_status']=='NOT_COMPARABLE';continue
            base=card['operating_baseline'];eps=D(str(card['cross_checks']['diluted_eps_ttm']['values']['diluted_eps_ttm']));price=D(str(card['cross_checks']['observed_price']['values']['observed_price']));rate=D(card['assumptions']['required_return'])
            assert base['eps']==oc['cross_checks']['diluted_eps_ttm']
            source_rows=[r for r in old['assessments'][symbol]['verified_field_records']+old['assessments'][symbol]['historical_field_records'] if r['record_id'] in base['eps']['evidence_refs']]
            starts=[r['fiscal_period_start'] for r in source_rows];ends=[r['period'] for r in source_rows]
            assert base['start']==min(starts) and base['period']==max(ends)
            facts=oc['quality']['financial_inputs']['facts'];flow={}
            for field,row in base['flows'].items():
                components=row['components'];assert all(c in facts and c['field']==field for c in components)
                if len(components)==1:
                    assert components[0]['start']==base['start'] and components[0]['period']==base['period'] and components[0]['scope']=='annual'
                    value=D(components[0]['value'])
                else:
                    assert len(components)==3;fy,cur,prior=components
                    assert fy['scope']=='annual' and cur['scope']==prior['scope'] and cur['scope'] in {'quarterly','year_to_date'}
                    assert cur['period']==base['period'] and prior['start']==fy['start']
                    assert (datetime.fromisoformat(cur['start'])-datetime.fromisoformat(fy['period'])).days==1
                    assert (datetime.fromisoformat(base['start'])-datetime.fromisoformat(prior['period'])).days==1
                    assert abs((datetime.fromisoformat(cur['period'])-datetime.fromisoformat(cur['start'])).days-(datetime.fromisoformat(prior['period'])-datetime.fromisoformat(prior['start'])).days)<=7
                    value=D(fy['value'])+D(cur['value'])-D(prior['value'])
                close(row['value'],value);flow[field]=value
            close(base['operating_margin'],flow['operating_income']/flow['revenue']);close(base['net_to_operating_conversion'],flow['net_income']/flow['operating_income'])
            profile=p['dossier']['companies'][symbol];assert card['operating_assumptions']==profile
            for note in profile['source_notes']:
                raw=(parent_path.parent/'sources/inputs'/note['path']).read_bytes()
                assert any(n.get('path')==note['path'] and n.get('url')==note['url'] and n.get('sha256')==note['sha256'] for n in manifest['companies'][symbol]['originals']+manifest.get('requests',[]))
                assert 0<=note['begin']<note['end']<=len(raw)
                assert hashlib.sha256(raw).hexdigest()==note['sha256']
                assert hashlib.sha256(raw[note['begin']:note['end']]).hexdigest()==note['excerpt_sha256']
            for name,result in card['scenarios'].items():
                expected,years,compiled=path_value(eps,price,base,profile[name],rate)
                for field,value in expected.items():close(result[field],value)
                for actual,year in zip(result['years'],years):
                    assert actual['year']==year['year']
                    for field,value in year.items():
                        if field!='year':close(actual[field],value)
                for field,value in compiled.items():close(card['assumptions'][name][field],D(value))
                checked+=1
            expected_eight=eight_values(eps,price,card['assumptions']['base'],rate)
            assert len(card['sensitivity'])==8
            for actual,(field,delta,params,required,value) in zip(card['sensitivity'],expected_eight):
                assert actual['parameter']==field and D(actual['delta'])==delta and D(actual['required_return'])==required
                assert bool(actual['result'])==bool(value)
                if value:
                    for k,v in params.items():close(actual['parameters'][k],v)
                    for k,v in value.items():close(actual['result'][k],v)
                checked+=1
            complete=all(v['result'] for v in card['sensitivity'])
            returns=[D(v['result']['annualized_return']) for v in card['sensitivity'] if v['result']]
            robust=complete and all(D(v['result']['annualized_return'])>=D(v['required_return']) for v in card['sensitivity'])
            assert card['sensitivity_complete']==complete and card['sensitivity_threshold_robust']==robust
            span=max(returns)-min(returns) if returns else None
            assert card['high_sensitivity']==bool(span is not None and span>D('.05'))
            if span is not None:close(card['sensitivity_span'],span)
            bounds=(D(card['assumptions']['cautious']['earnings_growth']),D(card['assumptions']['optimistic']['earnings_growth']))
            cells=joint_values(eps,price,base,profile['base'],rate,bounds);assert len(card['joint_sensitivity'])==9
            assert card['research_method_blockers']==method_blocks(cells,rate)
            assert card['joint_qualification']['complete']==all(v[3] is not None for v in cells)
            assert card['joint_qualification']['all_above_required']==(not method_blocks(cells,rate))
            for actual,(g,pe,params,value,years,compiled) in zip(card['joint_sensitivity'],cells):
                assert actual['revenue_growth_shift']==g and actual['terminal_pe_shift']==pe
                assert bool(actual['result'])==bool(value)
                if value:
                    assert actual['operating_parameters']==params
                    for k,v in value.items():close(actual['result'][k],v)
                    recentered=joint_values(eps,price,base,params,rate,bounds)
                    assert actual['method_blockers_after']==method_blocks(recentered,rate)
                    changed=deepcopy(p['result']['cards']);target=next(c for c in changed if c['symbol']==symbol)
                    target['scenarios']['base']=actual['result'];target['assumptions']=actual['assumptions'];target['sensitivity']=actual['sensitivity'];target['research_method_blockers']=method_blocks(recentered,rate)
                    assert actual['after']==ranks(changed,p['result']['ranking'])
                    if actual['after']!=original_ranks:
                        changes.append(dict(symbol=symbol,revenue_growth_shift=g,terminal_pe_shift=pe,after=actual['after']))
                checked+=1
        expected=ranks(p['result']['cards'],p['result']['ranking'])
        assert expected=={c['symbol']:[c['group'],c['rank']] for c in p['result']['cards']}
    assert p['result']['comparable_count']==sum(bool(c['scenarios']) for c in p['result']['cards'])
    assert p['result']['ranking']['changes']==changes and p['result']['ranking']['stable']==(not changes)
    assert all(c['ranking_unstable']==bool(changes) for c in p['result']['cards'])
    return dict(artifact_id=b['artifact_id'],independent=True,parent_verified=parent['artifact_id'],
        source_hashes_verified=len(p['source_files']),scenario_and_sensitivity_cases=checked,
        calculations_and_joint_ranks='PASS',qualified_owner_authenticity_scope='Parent original verifier; new flow bridge and conditional path arithmetic independently checked')
