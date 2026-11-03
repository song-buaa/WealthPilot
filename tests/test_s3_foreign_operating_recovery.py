"""S3 foreign-listed conditional research on qualified historical owner inputs."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from backend.services.market_data.output_policy import captured_facts
from backend.services.market_data.quality_valuation import (
    foreign_ads_conditional_research, foreign_ads_operating_recovery_research,
)
from backend.services.position_analysis import ResearchIntent, fact_sections
from tests.test_s3_foreign_ads import assessment as pdd_assessment, assumptions as pdd_assumptions
from tests.test_policy_integration import active, session


def recovery_assumptions():
    case = dict(annual_revenue_growth='.03', year_five_ebit_margin='.05',
                annual_cash_after_capital_cny='-3000000000',
                terminal_ev_ebit_multiple='10', year_five_ordinary_shares='2200000000',
                initial_net_cash_cny='25000000000', fx_cny_per_usd='6.65')
    return dict(basis='S3_RESEARCH_ASSUMPTION_NOT_POLICY',
                rationale='Five-year conditional operating recovery, not management guidance or a known forecast.',
                multiple_basis='An illustrative eight to twelve EV/EBIT range requiring separate peer validation.',
                required_return='.10',
                cautious=dict(case, annual_revenue_growth='-.05', year_five_ebit_margin='-.03',
                              terminal_ev_ebit_multiple='8'),
                base=case,
                optimistic=dict(case, annual_revenue_growth='.12', year_five_ebit_margin='.12',
                                terminal_ev_ebit_multiple='12'))


def li_assessment():
    """Same owner-qualified row contract; numbers in this unit test are synthetic."""
    a = deepcopy(pdd_assessment())
    a['symbol'] = 'LI:US'
    for key in ('records', 'verified_field_records', 'historical_field_records', 'quality_field_records'):
        a[key] = [dict(r, symbol='LI:US') for r in a.get(key, [])]
    a['verified_field_records'] = [r for r in a['verified_field_records'] if r.get('field') != 'revenue_annual']
    row = deepcopy(next(r for r in pdd_assessment()['verified_field_records'] if r.get('field') == 'revenue_quarter'))
    row.update(symbol='LI:US', field='revenue_annual', scope='annual',
               period='2025-12-31', fiscal_period_start='2025-01-01',
               values={'revenue_annual':112312511000}, currency='CNY')
    row['publication_links']=[dict(link,source_field='totalRevenue',period_basis='annual',
                                   fiscal_period_start='2025-01-01',fiscal_period_end='2025-12-31',
                                   value=112312511000,unit_scale=1)
                              for link in row['publication_links']]
    a['verified_field_records'].append(row)
    return a


def test_loss_case_stays_na_and_reverse_requirement_is_independently_recomputable():
    a=li_assessment()
    r=foreign_ads_operating_recovery_research(a,recovery_assumptions())
    assert r['status']=='CONDITIONAL_RESEARCH_ONLY' and r['fair_value'] is None
    assert r['scenarios']['cautious']['status']=='NO_POSITIVE_TERMINAL_EBIT'
    assert r['scenarios']['cautious']['terminal_price_usd_per_ads'] is None
    base=r['scenarios']['base']; assumptions=recovery_assumptions()['base']
    revenue=Decimal(r['qualified_inputs']['revenue_cny'])*Decimal('1.03')**6
    cash=Decimal(assumptions['initial_net_cash_cny'])+5*Decimal(assumptions['annual_cash_after_capital_cny'])
    required_equity=Decimal(str(r['qualified_inputs']['price_usd_per_ads']))*Decimal('6.65')/Decimal(str(r['qualified_inputs']['ordinary_shares_per_ads']))*Decimal('2200000000')*Decimal('1.10')**5
    implied=(required_equity-cash)/Decimal('10')/revenue
    assert abs(Decimal(base['implied_year_five_ebit_margin'])-implied)<Decimal('.00000001')
    assert base['terminal_price_usd_per_ads'] is not None
    assert base['unobserved_revenue_bridge_years'] == 1
    assert base['years'][-1]['fiscal_year'] == 2031
    assert 'CURRENT_ECONOMIC_SHARES_UNVERIFIED' in r['limitations']
    assert len(r['input_sha256'])==64


def test_missing_or_conflicting_historical_fact_never_becomes_scenario():
    a=li_assessment();a['verified_field_records']=[r for r in a['verified_field_records'] if r.get('field')!='revenue_annual']
    assert foreign_ads_operating_recovery_research(a,recovery_assumptions())['status']=='INSUFFICIENT_EVIDENCE'
    a=li_assessment();conflict=deepcopy(next(r for r in a['verified_field_records'] if r.get('field')=='revenue_annual'))
    conflict['record_id']+=':conflict';conflict['values']['revenue_annual']+=1000000000
    a['verified_field_records'].append(conflict)
    assert 'QUALIFIED_ANNUAL_REVENUE_MISSING_OR_CONFLICTING' in foreign_ads_operating_recovery_research(a,recovery_assumptions())['missing']
    a=li_assessment();a['verified_field_records'][-1]['publication_links'][0]['period_basis']='quarter'
    assert foreign_ads_operating_recovery_research(a,recovery_assumptions())['status']=='INSUFFICIENT_EVIDENCE'


def test_pdd_condition_survives_fresh_final_projection_but_tampered_receipt_does_not():
    a=pdd_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,pdd_assumptions())
    changed_source=deepcopy(a)
    changed_source['records'][0]['source_url']='https://unverified.example/price'
    recalculated=foreign_ads_conditional_research(changed_source,pdd_assumptions())
    assert recalculated['qualified_inputs']==a['conditional_valuation']['qualified_inputs']
    assert recalculated['input_receipts']['price']!=a['conditional_valuation']['input_receipts']['price']
    at=datetime.fromisoformat(a['as_of'])
    fresh,facts=captured_facts(a,at=at)
    assert facts and fresh['conditional_valuation']['status']=='CONDITIONAL_RESEARCH_ONLY'
    assert '五年条件估值研究' in '\n'.join(fact_sections(fresh))
    changed=deepcopy(a);changed['conditional_valuation']['qualified_inputs']['price_usd_per_ads']=1
    projected,_=captured_facts(changed,at=at)
    assert 'conditional_valuation' not in projected
    changed=deepcopy(a);changed['conditional_valuation']['input_receipts']['ads_ratio']['basis']='forged'
    projected,_=captured_facts(changed,at=at)
    assert 'conditional_valuation' not in projected


def test_reprojection_cannot_shift_conditional_horizon_into_next_year(monkeypatch):
    from backend.services.market_data import evidence
    a=li_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,recovery_assumptions())
    assert a['conditional_valuation']['status']=='CONDITIONAL_RESEARCH_ONLY'
    def unchanged_sources(*args,**kwargs):
        replay=deepcopy(a)
        replay['as_of']=kwargs['at'].isoformat()
        return replay
    monkeypatch.setattr(evidence,'assess',unchanged_sources)
    projected,_=captured_facts(a,at=datetime.fromisoformat('2027-01-01T00:00:00+00:00'))
    assert 'conditional_valuation' not in projected


def test_assumptions_are_validated_and_cannot_claim_fact_or_policy_authority():
    ResearchIntent(conditional_valuation=recovery_assumptions())
    bad=recovery_assumptions();bad['basis']='VERIFIED_FACT'
    try:
        ResearchIntent(conditional_valuation=bad)
    except ValueError:
        pass
    else:
        raise AssertionError('self-asserted authority was accepted')


def test_actual_li_companyfacts_annual_receipt_is_accepted_without_invented_price():
    import gzip,hashlib,json
    from pathlib import Path
    from backend.services.instruments.issuers import resolve_sec_issuer
    from backend.services.market_data.evidence import assess
    from research_v2 import sec_disclosures as sec
    root=Path(__file__).parent/'fixtures/valuation_inputs_sec'
    manifest={r['name']:r for r in json.loads((root/'manifest.json').read_text())}
    def source(name):
        raw=gzip.decompress((root/(name+'.json.gz')).read_bytes())
        assert hashlib.sha256(raw).hexdigest()==manifest[name]['sha256']
        return raw,manifest[name]['fetched_at']
    directory=json.loads(source('directory')[0]);submissions=json.loads(source('LI-submissions')[0])
    issuer=resolve_sec_issuer('LI:US',directory,submissions)
    filings=sec.discover_filings(issuer,submissions)
    rows=sec.parse_companyfacts(source('LI-companyfacts'),issuer,filings,
                                identity_digest=manifest['directory']['sha256'],quality='all')
    proof={r['record_id']:r for r in rows}
    a=assess({},symbol='LI:US',research=rows,
             at=datetime.fromisoformat(manifest['LI-companyfacts']['fetched_at']),
             disclosure_verifier=proof.get)
    result=foreign_ads_operating_recovery_research(a,recovery_assumptions())
    assert 'QUALIFIED_ANNUAL_REVENUE_MISSING_OR_CONFLICTING' not in result['missing']
    assert 'QUALIFIED_USD_ADS_PRICE_REQUIRED' in result['missing']


def test_pdd_original_active_finalizer_delivers_condition_without_trade_authority(active):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind
    a=pdd_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,pdd_assumptions())
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),
                        SimpleNamespace(position_evidence=a,loaded_data=None),out)
    result=out.structured_payload
    assert result['conditionalValuation']['status']=='CONDITIONAL_RESEARCH_ONLY'
    assert result['conditionalValuation']['conditional_value'] is None
    assert len(result['conditionalValuations'])==1
    assert result['execution_authorized'] is False
    assert result['research_delivery_status']=='INCOMPLETE'  # no model explanation
    assert '五年条件估值研究' in out.chat_answer


def test_active_multi_assessment_keeps_each_condition_receipt(active):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind
    a=pdd_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,pdd_assumptions())
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),
                        SimpleNamespace(portfolio_evidence=[a,deepcopy(a)],loaded_data=None),out)
    assert out.structured_payload['conditionalValuation'] is None
    assert len(out.structured_payload['conditionalValuations'])==2
    assert all(x['result']['input_receipts'] for x in out.structured_payload['conditionalValuations'])
