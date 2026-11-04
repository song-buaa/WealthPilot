"""S3 foreign-listed conditional research on qualified historical owner inputs."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import pytest

from backend.services.market_data.output_policy import captured_facts, prompt_context
from backend.services.market_data.quality_valuation import (
    foreign_ads_conditional_research, foreign_ads_operating_recovery_research,
    propose_foreign_ads_conditional_research,
)
from backend.services.position_analysis import ResearchIntent, fact_sections, research_intent
from tests.test_s3_foreign_ads import AT, assessment as pdd_assessment, assumptions as pdd_assumptions
from tests.test_policy_integration import active, session


@pytest.fixture
def finalizer_clock(monkeypatch):
    """Control only the final evidence recheck; never refresh frozen receipts."""
    from backend.services.investment_policy import store

    def set_clock(at=AT):
        monkeypatch.setattr(store, 'utc_now', lambda: at.isoformat())

    return set_clock


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
    a['conditional_valuation']=r
    assert '条件回报差异与盈利持续性' not in prompt_context(a)
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
    assert base['economic_consistency']['status']=='UNVERIFIED_HYPOTHESIS_BRIDGE'
    assert base['unobserved_revenue_bridge_years'] == 1
    assert base['years'][-1]['fiscal_year'] == 2031
    assert 'CURRENT_ECONOMIC_SHARES_UNVERIFIED' in r['limitations']
    assert len(r['input_sha256'])==64


def test_natural_long_term_study_uses_qualified_market_references_not_user_hypotheses():
    a=pdd_assessment()
    intent=research_intent({'research_question':'长期研究PDD的五年条件回报与利润持续性，不执行交易。'})
    assert intent['valuation_research_requested']
    assert intent['sources']['conditional_valuation']=='system_evidence_bound_reference_or_unknown'
    r=propose_foreign_ads_conditional_research(a)
    assert r['status']=='CONDITIONAL_RESEARCH_ONLY'
    assert r['proposal_origin']=='system_evidence_bound_reference'
    assert r['assumptions']['origin']=='system_qualified_reference_sensitivity'
    assert r['qualified_inputs']['eps_cny_per_ads']==63.14
    assert r['proposal_basis']['pe_ttm']['evidence_refs']
    assert r['conditional_value'] is None and not r['execution_authorized']
    assert 'CURRENT_PE_IS_NOT_AN_INDEPENDENT_TERMINAL_MULTIPLE' in r['limitations']
    a['conditional_valuation']=r
    projected,_=captured_facts(a,at=datetime.fromisoformat(a['as_of']))
    assert projected['conditional_valuation']['proposal_origin']=='system_evidence_bound_reference'
    sections='\n'.join(fact_sections(projected))
    assert '观察收益率参考（非用户要求回报）' in sections
    assert sections.index('五年条件估值研究') < sections.index('字段事实')
    assert '条件回报差异与盈利持续性' in prompt_context(projected)


def test_natural_study_missing_fact_keeps_reverse_formula_and_no_return():
    a=pdd_assessment()
    a['research_fx']={'quality':'unavailable'}
    r=propose_foreign_ads_conditional_research(a)
    assert r['status']=='HYPOTHESES_REQUIRED'
    assert 'fx_rate' in r['missing']
    assert r['fair_value'] is None and not r['execution_authorized']
    assert not r.get('scenarios')


def test_loss_making_natural_study_requests_recovery_inputs_not_positive_pe():
    a=li_assessment()
    intent=research_intent({'research_question':'仅研究理想汽车的长期经营恢复、当前价格隐含条件及持有逻辑；没有新增交易授权。'})
    assert intent['valuation_research_requested']
    a['verified_field_records']=[r for r in a['verified_field_records'] if r['field']!='diluted_eps_ads_quarter']
    for row in a['verified_field_records']:
        if row['field']=='operating_income_quarter':
            row['values']['operating_income_quarter']=-1000000000
    r=propose_foreign_ads_conditional_research(a)
    assert r['status']=='HYPOTHESES_REQUIRED'
    assert r['method_version']=='s3-foreign-ads-recovery-reverse.v1'
    assert 'CURRENT_ECONOMIC_SHARES' in r['missing']
    assert 'COMPLETE_NET_CASH' in r['missing']
    assert 'LOSS_MAKING_PERIOD_PE_NOT_APPLICABLE' in r['limitations']
    assert r['fair_value'] is None and not r['execution_authorized']


def test_qualified_nonpositive_eps_uses_recovery_reverse_study(monkeypatch):
    a=li_assessment()
    from backend.services.market_data import financial_research
    real_build=financial_research.build
    for eps in (0,-1.25):
        def with_nonpositive_eps(assessment):
            result=deepcopy(real_build(assessment))
            result['records'].append({'field':'diluted_eps_ttm','quality':'valid',
                                      'values':{'diluted_eps_ttm':eps}})
            result['records'].append({'field':'operating_margin_pct','quality':'valid',
                                      'values':{'operating_margin_pct':-5}})
            return result
        monkeypatch.setattr(financial_research,'build',with_nonpositive_eps)
        r=propose_foreign_ads_conditional_research(a)
        assert r['method_version']=='s3-foreign-ads-recovery-reverse.v1'
        assert r['status']=='HYPOTHESES_REQUIRED'
        assert r['fair_value'] is None


def test_natural_proposal_is_removed_when_growth_or_price_receipt_changes(monkeypatch):
    from backend.services.market_data import evidence
    a=pdd_assessment();a['conditional_valuation']=propose_foreign_ads_conditional_research(a)
    at=datetime.fromisoformat(a['as_of'])
    original_assess=evidence.assess
    for change in ('growth','price_receipt'):
        def changed_assess(*args,**kwargs):
            fresh=original_assess(*args,**kwargs)
            if change=='growth':
                row=next(r for r in fresh['verified_field_records']
                         if r['field']=='revenue_quarter' and r['period']=='2026-06-30')
                row['values']['revenue_quarter']*=1.01
            else:
                row=next(r for r in fresh['records'] if r['kind']=='technical')
                row['source_url']='https://changed.example/price'
            return fresh
        monkeypatch.setattr(evidence,'assess',changed_assess)
        projected,_=captured_facts(a,at=at)
        assert 'conditional_valuation' not in projected


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


def test_pdd_original_active_finalizer_delivers_condition_without_trade_authority(active, finalizer_clock):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind
    a=pdd_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,pdd_assumptions())
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    finalizer_clock()
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


def test_natural_pdd_reference_survives_original_active_finalizer(active, finalizer_clock):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind
    a=pdd_assessment();a['conditional_valuation']=propose_foreign_ads_conditional_research(a)
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    finalizer_clock()
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),
                        SimpleNamespace(position_evidence=a,loaded_data=None),out)
    result=out.structured_payload
    assert result['conditionalValuation']['proposal_origin']=='system_evidence_bound_reference'
    assert result['conditionalValuation']['conditional_value'] is None
    assert result['execution_authorized'] is False
    assert result['research_delivery_status']=='INCOMPLETE'


def test_active_multi_assessment_keeps_each_condition_receipt(active, finalizer_clock):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind
    a=pdd_assessment();a['conditional_valuation']=foreign_ads_conditional_research(a,pdd_assumptions())
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    finalizer_clock()
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),
                        SimpleNamespace(portfolio_evidence=[a,deepcopy(a)],loaded_data=None),out)
    assert out.structured_payload['conditionalValuation'] is None
    assert len(out.structured_payload['conditionalValuations'])==2
    assert all(x['result']['input_receipts'] for x in out.structured_payload['conditionalValuations'])


@pytest.mark.parametrize('mode', ['explicit', 'natural', 'multi'])
def test_active_finalizer_removes_condition_when_frozen_price_expires(active, finalizer_clock, mode):
    from types import SimpleNamespace
    from backend.agents.contracts import ExpressionOutput
    from backend.services.investment_policy.consumers import finalize_output
    from backend.services.investment_policy.runtime import bind

    a=pdd_assessment()
    a['conditional_valuation']=(propose_foreign_ads_conditional_research(a) if mode=='natural'
                                else foreign_ads_conditional_research(a,pdd_assumptions()))
    assert a['conditional_valuation']['status']=='CONDITIONAL_RESEARCH_ONLY'
    original=deepcopy(a)
    # The next US session has closed; the frozen Nov 2 close no longer covers it.
    expired_at=datetime.fromisoformat('2026-11-04T00:26:57+00:00')
    fresh,_=captured_facts(a,at=expired_at)
    assert 'TECHNICAL_DAY_MISSING_TRADING_SESSIONS' in fresh['issues']
    assert 'conditional_valuation' not in fresh
    assert foreign_ads_conditional_research(fresh,pdd_assumptions())['status']=='INSUFFICIENT_EVIDENCE'
    finalizer_clock(expired_at)
    executed=(SimpleNamespace(portfolio_evidence=[a,deepcopy(a)],loaded_data=None) if mode=='multi'
              else SimpleNamespace(position_evidence=a,loaded_data=None))
    out=ExpressionOutput(structured_payload={'professional_explanation':[]})
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),executed,out)
    assert out.structured_payload['conditionalValuation'] is None
    assert out.structured_payload['conditionalValuations']==[]
    assert out.structured_payload['execution_authorized'] is False
    assert out.actionable is False
    assert a==original  # Rechecking cannot refresh or rewrite the source receipt.
