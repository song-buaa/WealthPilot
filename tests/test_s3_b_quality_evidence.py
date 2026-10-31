"""Synthetic rule boundaries; real-company acceptance stays in frozen artifacts."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from backend.services.market_data.quality_valuation import compare
from backend.services.market_data.financial_research import build_quality
from backend.services.market_data.evidence import assess_quality_facts
from research_v2 import sec_disclosures as sec
from tests.test_s3_quality_valuation import assessment, request, params
from tests.test_sec_financial_sources import facts, AT as SOURCE_AT, DIGEST


def quality_assessment(symbol='AAA:US'):
    a=assessment(symbol)
    a['quality_scope']=dict(issuer_type='domestic',sic='3571',source_document_sha256='a'*64,source_url='https://data.sec.gov/synthetic',fetched_at='2026-10-01T00:00:00+00:00')
    a['quality_field_records']=[]
    bases={key.removeprefix('quality_'):basis for key,basis in sec.QUALITY_CONCEPTS.values()}
    def add(field,value,start=None,end='2026-06-30',scope='instant'):
        i=len(a['quality_field_records'])
        row=dict(record_id=f'synthetic-quality-{symbol}-{i}',symbol=symbol,field=field,
            values={field:value},quality='valid',is_mock=False,is_seed=False,
            accounting_basis=bases[field],scope=scope,period=end,fiscal_period_start=start,
            currency=None if field in {'shares','diluted_shares'} else 'USD',
            unit='shares' if field in {'shares','diluted_shares'} else 'currency_units',
            published_at='2026-08-01T00:00:00+00:00',fetched_at='2026-10-01T00:00:00+00:00',
            publication_links=[dict(source_document_sha256='a'*64,source_url='https://www.sec.gov/synthetic',
                published_at='2026-08-01T00:00:00+00:00',fetched_at='2026-10-01T00:00:00+00:00')])
        a['quality_field_records'].append(row)
    for f,v in dict(assets=1000,liabilities=400,equity=600,current_assets=500,current_liabilities=200,
                    cash=200,cash_including_restricted=210,noncurrent_debt=100,current_debt=50).items():add(f,v)
    add('equity',500,end='2025-12-31')
    for f,v in dict(net_income=100,cfo=150,capex=20,sbc=5,repurchases=10,receivables_change=5,
                    inventory_change=2,payables_change=3,diluted_shares=100,operating_income=120).items():
        add(f,v,start='2026-01-01',scope='year_to_date')
    for start,end,value in [('2025-01-01','2025-03-31',100),('2025-04-01','2025-06-30',110),
                            ('2026-01-01','2026-03-31',120),('2026-04-01','2026-06-30',140)]:
        add('revenue',value,start,end,'quarterly');add('operating_income',value*.2,start,end,'quarterly')
    return a


def card(a=None, assumption=None):
    r=request();r['companies']={'AAA:US':assumption or params('.3','40')}
    return compare(r,{'AAA:US':a or quality_assessment()})['cards'][0]


def test_all_four_evidence_paths_remove_only_resolved_blockers_and_are_repeatable():
    a=quality_assessment();before=deepcopy(a)
    c=card(a)
    assert c==card(a) and a==before
    assert all(f['status']=='通过' for f in c['quality']['floors'].values())
    assert c['quality']['priority_blockers']==[]
    assert c['group']=='优先考虑'
    assert c['high_sensitivity'] and c['sensitivity_threshold_robust']
    assert c['quality']['normalization']['status']=='UNKNOWN'
    assert c['quality']['capital_efficiency']['roic']['status']=='UNKNOWN'
    assert not c['execution_authorized']
    assert all(f.get('rule_version') for k,f in c['quality']['floors'].items() if k!='data_trust')


def test_all_three_groups_reachable_without_changing_original_thresholds():
    assert card(assumption=params('.3','40'))['group']=='优先考虑'
    a=quality_assessment();a['quality_field_records']=[r for r in a['quality_field_records'] if r['field']!='sbc']
    assert card(a)['group']=='继续观察'
    assert card(assumption=params('0','5'))['group']=='暂不优先'
    # All real/research thresholds unchanged; only test evidence and economics vary.


@pytest.mark.parametrize('fault',['missing_cash','missing_sbc','missing_prior','future','link_future','currency','unit','period','conflict','stale'])
def test_missing_or_invalid_evidence_never_becomes_quality_pass(fault):
    a=quality_assessment();rows=a['quality_field_records']
    if fault.startswith('missing_'):
        field={'missing_cash':'cash','missing_sbc':'sbc','missing_prior':'revenue'}[fault]
        a['quality_field_records']=[r for r in rows if r['field']!=field]
    elif fault=='future':rows[0]['published_at']='2027-01-01T00:00:00+00:00'
    elif fault=='link_future':rows[0]['publication_links'][0]['fetched_at']='2027-01-01T00:00:00+00:00'
    elif fault=='currency':rows[0]['currency']='CNY'
    elif fault=='unit':rows[0]['unit']='million_USD'
    elif fault=='period':rows[0]['fiscal_period_start']='2026-01-01'
    elif fault=='conflict':
        duplicate=deepcopy(rows[0]);duplicate['values']['assets']=2000;duplicate['record_id']='other';rows.append(duplicate)
    else:
        a['as_of']='2027-10-30T00:00:00+00:00'
        # Evaluate Owner directly so the base common-clock rejection doesn't hide freshness.
        assert not build_quality(a)['fresh'];return
    c=card(a)
    assert c['group']!='优先考虑' and c['quality']['priority_blockers']
    assert any(v['status']=='暂无法判断' for v in c['quality']['floors'].values())


def test_observed_cash_warning_and_unknown_capex_are_both_preserved():
    a=quality_assessment()
    for row in a['quality_field_records']:
        if row['field']=='cfo':row['values']['cfo']=50
    a['quality_field_records']=[r for r in a['quality_field_records'] if r['field']!='capex']
    q=card(a)['quality']
    assert q['floors']['profit_cash_support']['status']=='警示'
    assert 'fcf' not in q['financial_inputs']['latest_cash']
    assert q['fundamental_deterioration']['status']=='NOT_ESTABLISHED'


def test_confirmed_two_quarter_operating_losses_block_high_returns_without_claiming_cause():
    a=quality_assessment()
    for r in a['quality_field_records']:
        if r['scope']=='quarterly' and r['period'].startswith('2026'):
            r['values'][r['field']]=50 if r['field']=='revenue' else -10
    c=card(a)
    assert c['scenarios']['base'] and c['group']=='暂不优先'
    assert c['quality']['major_risks']
    assert c['classification_reasons']['fundamental_deterioration']['status']=='CONFIRMED_OBSERVED_OPERATING_DETERIORATION'
    assert not c['classification_reasons']['research_thresholds_not_met']


def test_a_short_decline_is_warning_not_established_durable_deterioration():
    a=quality_assessment()
    for r in a['quality_field_records']:
        if r['field']=='revenue' and r['period']=='2026-06-30' and r['scope']=='quarterly':r['values']['revenue']=80
    c=card(a)
    assert c['quality']['floors']['core_business_change']['status']=='警示'
    assert not c['quality']['major_risks']
    assert c['classification_reasons']['fundamental_deterioration']['status']=='NOT_ESTABLISHED'


def test_cash_direction_period_roe_and_partial_company_failure():
    a=quality_assessment();q=card(a)['quality'];cash=q['financial_inputs']['latest_cash']
    assert Decimal(cash['fcf']['value'])==130
    assert Decimal(cash['fcf_less_sbc_sensitivity']['value'])==125
    roe=q['capital_efficiency']['roe']
    assert Decimal(roe['average_equity']['value'])==550
    assert abs(Decimal(roe['ratio']['value'])-Decimal(100)/550)<Decimal('.00000001')
    r=request();a2=quality_assessment('BBB:US');a2['symbol']='WRONG:US'
    result=compare(r,{'AAA:US':a,'BBB:US':a2})
    assert result['cards'][0]['scenarios'] and not result['cards'][1]['scenarios']


def test_owner_opt_in_uses_existing_parser_identity_and_registry_not_fake_proofs():
    source,issuer,filings=facts()
    raw=json.loads(source[0]);accn=filings[0]['accession'];form=filings[0]['form'];filed=filings[0]['filed']
    raw['facts']['us-gaap']['Assets']={'units':{'USD':[dict(accn=accn,form=form,filed=filed,end='2026-06-30',val=100)]}}
    raw['facts']['us-gaap']['NetCashProvidedByUsedInOperatingActivities']['units']['USD'][0]['start']='2026-01-01'
    src=(json.dumps(raw).encode(),source[1])
    rows=sec.parse_companyfacts(src,issuer,filings,identity_digest=DIGEST,quality=True)
    assert rows
    registry=assess_quality_facts(rows,symbol=issuer['symbol'],at=SOURCE_AT,disclosure_verifier={r['record_id']:r for r in rows}.get)
    assert any(r['scope']=='instant' for r in registry)
    assert any(r['scope']=='year_to_date' for r in registry)
    assert not assess_quality_facts(rows,symbol=issuer['symbol'],at=SOURCE_AT,disclosure_verifier=None)
    base=sec.parse_companyfacts(src,issuer,filings,identity_digest=DIGEST)
    assert all(o['period_basis'] in {'quarterly','annual'} for r in base for o in r['financial_observations'])


@pytest.mark.parametrize('sic',[None,'6020','4911'])
def test_inapplicable_sector_or_missing_scope_never_qualifies(sic):
    a=quality_assessment();a['quality_scope']['sic']=sic
    c=card(a)
    assert c['group']!='优先考虑'
    assert c['quality']['applicability']['status']=='UNKNOWN_OR_NOT_APPLICABLE'


def test_independent_verifier_rejects_fabricated_quality_floor(tmp_path):
    import runpy
    from pathlib import Path
    from backend.services.quality_valuation_research import digest,save_bundle,view_hashes,method_hashes
    verify=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/s3_independent_recompute.py'))['verify']
    r=request();r['companies']={'AAA:US':params('.3','40')};a=quality_assessment()
    payload=dict(request=r,assessments={'AAA:US':a},method_files=method_hashes(),source_files={},result=compare(r,{'AAA:US':a}))
    payload['view_hashes']=view_hashes(payload['result'])
    b=dict(payload=payload,artifact_id=digest(payload));p=save_bundle(tmp_path/'good',b,{})
    assert verify(p/'bundle.json')['quality_cash_periods_verified']==1
    altered=deepcopy(payload);altered['result']['cards'][0]['quality']['floors']['financial_sustainability']['status']='警示'
    altered['view_hashes']=view_hashes(altered['result']);bad=dict(payload=altered,artifact_id=digest(altered));p=save_bundle(tmp_path/'bad',bad,{})
    with pytest.raises(AssertionError,match='quality floor differs'):verify(p/'bundle.json')


def test_display_rounding_cannot_upgrade_cash_or_margin_boundary():
    a=quality_assessment()
    for r in a['quality_field_records']:
        if r['field']=='cfo':r['values']['cfo']='99.99999999999'
    c=card(a)
    assert c['quality']['financial_inputs']['latest_cash']['cash_conversion']['value']=='1.00000000'
    assert c['quality']['floors']['profit_cash_support']['status']=='警示'
    a=quality_assessment()
    for r in a['quality_field_records']:
        if r['field']=='operating_income' and r['scope']=='quarterly' and r['period'].startswith('2026'):
            revenue=120 if r['period']=='2026-03-31' else 140
            r['values']['operating_income']=str(Decimal(revenue)*Decimal('.14999999999'))
    assert card(a)['quality']['floors']['core_business_change']['status']=='警示'


def test_current_debt_total_conflicting_with_disclosed_components_is_unknown():
    a=quality_assessment()
    template=deepcopy(next(r for r in a['quality_field_records'] if r['field']=='current_debt'))
    for field,value,basis in [('current_long_term_debt',50,'current_long_term_debt'),('commercial_paper',10,'commercial_paper')]:
        r=deepcopy(template);r.update(record_id='synthetic-'+field,field=field,accounting_basis=basis,values={field:value})
        a['quality_field_records'].append(r)
    c=card(a)
    assert c['quality']['floors']['financial_sustainability']['status']=='暂无法判断'
    assert c['group']!='优先考虑'
