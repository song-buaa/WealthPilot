"""P1: genuine frozen SEC cells + isolated calculation/output contracts, no network."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import pytest

from tests.test_p0_quality_semantics import pdd_record,AT
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build
from backend.services.market_data.output_policy import _evidence_index,explanation_prompt,explanation_projection
from backend.services.position_analysis import ResearchIntent,research_intent,exit_plan,report


def actual_pdd():
    row=pdd_record()
    return assess({},symbol='PDD:US',research=[row],at=AT,disclosure_verifier={row['record_id']:row}.get)


def metrics(a):return {r['field']:r for r in build(a)['records']}


def test_real_original_comparatives_remain_historical_without_granting_L3():
    a=actual_pdd();m=metrics(a)
    assert m['revenue_yoy_pct']['values']['revenue_yoy_pct']==pytest.approx(8.0521229)
    assert m['net_income_yoy_pct']['values']['net_income_yoy_pct']==pytest.approx(-11.6147493)
    assert m['operating_margin_pct']['values']['operating_margin_pct']==pytest.approx(24.710301)
    assert m['cash_conversion']['values']['cash_conversion']==pytest.approx(.944374954)
    assert m['net_margin_change_pp']['values']['net_margin_change_pp']<0
    assert m['operating_margin_change_pp']['values']['operating_margin_change_pp']<0
    assert all(r['historical_only'] and r['period']=='2025-06-30' for r in a['historical_field_records'])
    assert all(r['period']=='2026-06-30' for r in a['verified_field_records'])
    assert not a['qualification']['L3']['allowed'] and not a['qualification']['L4']['allowed']
    eps=next(r for r in a['verified_field_records'] if r['field']=='diluted_eps_ads_quarter')
    assert eps['values'][eps['field']]==18.45 and eps['ordinary_shares_per_ads']==4
    parent=next(r for r in a['verified_field_records'] if r['field']=='net_income_available_to_ordinary_shareholders_quarter')
    assert parent['accounting_basis']=='net_income_available_to_ordinary_shareholders'
    assert 'pe_ttm' not in m and 'free_cash_flow_ttm' not in m


def test_unverified_card_cannot_grant_historical_or_comparable_facts():
    row=pdd_record()
    a=assess({},symbol='PDD:US',research=[row],at=AT)
    assert not a['verified_field_records'] and not a['historical_field_records'] and not build(a)['records']


@pytest.mark.parametrize('change',['currency','basis','value','missing'])
def test_comparison_requires_unique_prior_period_currency_basis(change):
    a=actual_pdd();prior=next(r for r in a['historical_field_records'] if r['field']=='revenue_quarter')
    if change=='currency':prior['currency']='USD'
    elif change=='basis':prior['accounting_basis']='segment_revenue'
    elif change=='missing':a['historical_field_records'].remove(prior)
    else:
        other=deepcopy(prior);other['values']['revenue_quarter']+=1;other['record_id']+='conflict';a['historical_field_records'].append(other)
    m=metrics(a)
    assert 'revenue_yoy_pct' not in m
    assert 'cash_conversion' in m  # unrelated valid facts survive


def valuation_case():
    # Explicit synthetic scalar fixture exercises the pure arithmetic; it is
    # never used to claim SEC source verification or actual PDD valuation.
    a=actual_pdd();template=next(r for r in a['verified_field_records'] if r['field']=='diluted_eps_ads_quarter')
    a['verified_field_records']=[r for r in a['verified_field_records'] if not r['field'].startswith('diluted_eps')]
    a['historical_field_records']=[r for r in a['historical_field_records'] if not r['field'].startswith('diluted_eps')]
    for period in ['2026-06-30','2026-03-31','2025-12-31','2025-09-30']:
        row=deepcopy(template);row.update(period=period,record_id='synthetic-eps:'+period,historical_only=period<'2026-06-30')
        end=date.fromisoformat(period);row['fiscal_period_start']=end.replace(month=end.month-2,day=1).isoformat()
        row['values']={'diluted_eps_ads_quarter':18.,'currency':'CNY'}
        a['historical_field_records' if row['historical_only'] else 'verified_field_records'].append(row)
    a['technical_levels']={'day':dict(record_id='synthetic-price',currency='USD',end_at='2026-10-28',bars=[{'close':100.}])}
    a['research_fx']=dict(rate=7.,rate_date='2026-10-28',fetched_at=AT.isoformat(),quality='historical',source='provider',from_currency='USD',to_currency='CNY')
    return a


def test_same_basis_four_quarters_FX_and_ADS_valuation_not_annualized_single_Q():
    a=valuation_case();m=metrics(a)
    assert m['diluted_eps_ttm']['values']['diluted_eps_ttm']==72
    assert m['pe_ttm']['values']['pe_ttm']==pytest.approx(700/72)
    assert m['earnings_yield_pct']['values']['earnings_yield_pct']==pytest.approx(72/7)
    scenarios=build(a)['valuation_sensitivity'];assert len(scenarios)==9
    assert all('not forecast' in s['basis'] for s in scenarios)


@pytest.mark.parametrize('bad',['missing_quarter','ratio_change','fx_fallback','fx_date','currency','loss'])
def test_incomplete_valuation_degrades_locally(bad):
    a=valuation_case()
    if bad=='missing_quarter':a['historical_field_records']=[r for r in a['historical_field_records'] if r['period']!='2025-12-31']
    elif bad=='ratio_change':next(r for r in a['historical_field_records'] if r['field']=='diluted_eps_ads_quarter')['ordinary_shares_per_ads']=8
    elif bad=='fx_fallback':a['research_fx']['quality']='fallback'
    elif bad=='fx_date':a['research_fx']['rate_date']='2026-01-01'
    elif bad=='currency':a['research_fx']['to_currency']='HKD'
    else:
        for r in a['verified_field_records']+a['historical_field_records']:
            if r['field']=='diluted_eps_ads_quarter':r['values'][r['field']]=-18
    assert 'pe_ttm' not in metrics(a) and 'revenue_yoy_pct' in metrics(a)


def candidate(a):
    row=metrics(a)['revenue_yoy_pct'];ref=row['record_id']
    return dict(topic='business',basis='inference',text='经营可比数据表明{{n1}}，但利润与现金转化是否持续改善仍需通过后续正式财报复核。',
                evidence_refs=[ref],numbers=[dict(id='n1',evidence_ref=ref,field='revenue_yoy_pct')])


def test_numeric_binding_survives_final_recheck_with_trace_and_no_free_number():
    a=actual_pdd();item=candidate(a)
    accepted,diag=explanation_projection([item],a)
    assert diag['accepted_count']==1 and '8.0521%' in accepted[0]['text']
    assert '2026-06-30' in accepted[0]['text'] and accepted[0]['sources'][0]['derived_from']
    assert explanation_projection(accepted,a)[0]==accepted
    bad=deepcopy(accepted);bad[0]['text']=bad[0]['text'].replace('8.0521','88.0000')
    assert not explanation_projection(bad,a)[0]


@pytest.mark.parametrize('attack',['free_number','wrong_field','unbound_ref','invisible','order'])
def test_numeric_and_execution_injections_still_rejected(attack):
    a=actual_pdd();item=candidate(a)
    if attack=='free_number':item['text']+='预计增长99%。'
    elif attack=='wrong_field':item['numbers'][0]['field']='made_up_profit'
    elif attack=='unbound_ref':item['numbers'][0]['evidence_ref']='fake'
    elif attack=='invisible':item['text']+='\u200b'
    else:item['text']+='立即挂单买入。'
    assert not explanation_projection([item],a)[0]


def test_L1_exit_plan_uses_user_research_goal_and_deadline_without_L3():
    a=actual_pdd();a['research_intent']=research_intent(dict(research_context=dict(objective='exit',review_date='2026-11-20',reference_quantity=150,
        tranche_fractions=[.2,.3,.3,.2],historical_price_bands=[(80,83),(86,90)])))
    p=exit_plan(a,at=AT);rp=p['research_plan']
    assert not a['qualification']['L3']['allowed']
    assert [r['fraction_of_reference_position'] for r in rp['stages']]==[.2,.3,.3,.2]
    assert p['review_deadline']=='2026-11-20' and rp['non_executable']
    assert all(r['quantity'] is None and not r['execution_authorized'] for r in rp['stages'])
    assert rp['historical_bands_review']['status']=='user_assumptions_not_current_price_evidence'
    text=report(a,p,'watch');assert '2026-11-20' in text and '仅为用户假设' in text
    assert '采用默认七日' not in text and '2026-11-05' not in text
    assert not p['execution_authorized'] and not p['order_created']


def test_explicit_question_overrides_user_history_not_assistant_inventions():
    intent=research_intent(dict(research_question='我决定长期退出，2026年11月20日前主动复核。',research_history=[
        dict(role='assistant',content='继续持有，2026-11-01前复核'),dict(role='user',content='继续持有，2026-11-02前复核')]))
    assert intent['objective']=='exit' and intent['review_date']=='2026-11-20'
    assert not intent['formal_target_confirmed'] and not intent['holdings_verified']


def test_invalid_user_assumptions_and_no_memory_fabrication():
    with pytest.raises(ValueError):ResearchIntent(tranche_fractions=[.8,.8])
    with pytest.raises(ValueError):ResearchIntent(review_date='2026-02-31')
    intent=research_intent({});assert intent['objective']=='evaluate' and intent['reference_quantity'] is None


def test_qualified_daily_band_is_L1_research_not_L3_order_trigger():
    from tests.test_v42_evidence import trusted_technical
    a=assess({'technical':trusted_technical('LI:US')},symbol='LI:US',at=AT)
    # The original fixture clock may differ; use its own verified day contract.
    from tests.test_v42_evidence import AT as TECH_AT
    a=assess({'technical':trusted_technical('LI:US')},symbol='LI:US',at=TECH_AT)
    p=exit_plan(a,at=TECH_AT)
    assert p['research_plan']['price_observation'] and p['price_trigger'] is None
    assert not p['execution_authorized'] and p['research_plan']['non_executable']


@pytest.mark.parametrize('start',['2026-04-10',None,'2026-01-01'])
def test_incompatible_or_unknown_start_cannot_produce_margin_or_cash_ratio(start):
    a=actual_pdd()
    for r in a['verified_field_records']:
        if r['field'] in {'operating_income_quarter','operating_cash_flow_quarter'}:r['fiscal_period_start']=start
    m=metrics(a)
    assert 'operating_margin_pct' not in m and 'cash_conversion' not in m
    assert 'net_margin_pct' in m and 'revenue_yoy_pct' in m


def test_leap_day_comparison_is_unknown_not_crash():
    a=actual_pdd()
    for r in a['verified_field_records']:
        r.update(period='2024-02-29',fiscal_period_start='2023-12-01')
    m=metrics(a)
    assert 'revenue_yoy_pct' not in m


def test_overlapping_or_missing_EPS_starts_block_TTM_not_other_facts():
    a=valuation_case()
    next(r for r in a['historical_field_records'] if r['field']=='diluted_eps_ads_quarter')['fiscal_period_start']='2026-01-10'
    assert 'diluted_eps_ttm' not in metrics(a) and 'revenue_yoy_pct' in metrics(a)


def test_EPS_companyfacts_locator_preserves_actual_json_unit():
    import json
    from tests.test_sec_financial_sources import facts
    from research_v2 import sec_disclosures as sec
    source,issuer,filings=facts()
    raw=json.loads(source[0]);filing=filings[0]
    raw['facts'].setdefault('us-gaap',{})['EarningsPerShareDiluted']={'units':{'USD/shares':[
        dict(val=1.2,start='2026-04-01',end='2026-06-30',accn=filing['accession'],form=filing['form'],filed=filing['filed'])]}}
    rows=sec.parse_companyfacts((json.dumps(raw).encode(),source[1]),issuer,filings,identity_digest='a'*64)
    obs=next(o for r in rows for o in r['financial_observations'] if o['source_field']=='dilutedEPSOrdinary')
    assert '/units/USD~1shares/0' in obs['source_locator']


def test_research_only_vocabulary_retains_observation_not_free_numeric_orders():
    a=actual_pdd();p=candidate(a)
    p.update(scope='research_only',topic='exit_rationale',text='在长期全部退出的研究目标下，价格观察只用于比较等待和减仓的机会成本，不代表交易授权。')
    p.pop('numbers')
    good,_=explanation_projection([p],a);assert good and good[0]['scope']=='research_only'
    for text in ['当前价格观察为99美元，可以研究减仓条件但不执行。','根据证据立即卖出并执行下单。']:
        assert not explanation_projection([dict(p,text=text)],a)[0]


def test_bound_prose_survives_original_output_and_legacy_explain_projection():
    from backend.agents.contracts import ExpressionOutput
    from backend.services.market_data.output_policy import enforce
    from decision_engine.llm_engine import LLMResult
    a=actual_pdd();llm=LLMResult('HOLD',[],[],[])
    out=ExpressionOutput(llm_result=llm,structured_payload=dict(decisionType='watch',professional_explanation=[candidate(a)]))
    enforce(out,a)
    assert '8.0521%' in out.chat_answer and any('8.0521%' in x for x in out.llm_result.reasoning)
    first=deepcopy(out.structured_payload['professional_explanation']);enforce(out,a)
    assert out.structured_payload['professional_explanation']==first
    assert not out.actionable and not out.structured_payload['exitPlan']['execution_authorized']


def test_real_three_month_headers_establish_period_start_for_all_table_fields():
    from bs4 import BeautifulSoup
    from tests.test_p0_quality_semantics import pdd_original
    from research_v2.official_disclosures import parse_statement_tables
    obs=parse_statement_tables(BeautifulSoup(pdd_original(),'html.parser'),'2026-08-25',require_statements=True)
    eps=[o for o in obs if o['source_field']=='dilutedEPSADS']
    assert len(eps)==2 and all(o['fiscal_period_start']==o['fiscal_period_end'][:4]+'-04-01' for o in eps)
    assert all(o['reported_unit_scale']==1 and o['unit_scale']==1 for o in eps)
    assert all('three months ended' in o['period_start_basis'] for o in obs)


@pytest.mark.parametrize('amount,accepted',[('10',False),('(10)',True)])
def test_operating_loss_sign_is_not_inverted(amount,accepted):
    from bs4 import BeautifulSoup
    from research_v2.official_disclosures import parse_statement_tables
    html='<h2>Consolidated statements of income (in millions)</h2><table><tr><td></td><td>Three months ended June 30, 2026</td></tr><tr><td></td><td>USD</td></tr><tr><td>Loss from operations</td><td>'+amount+'</td></tr></table>'
    obs=parse_statement_tables(BeautifulSoup(html,'html.parser'),'2026-08-25',require_statements=True,currencies=('USD',))
    assert bool(obs)==accepted
    if accepted:assert obs[0]['value']==-10


def test_LI_quarter_cash_shortfall_uses_real_OCF_and_verified_positive_capex():
    from tests.test_sec_real_original import parse,AT as LI_AT
    row=parse();a=assess({},symbol='LI:US',research=[row],at=LI_AT,disclosure_verifier={row['record_id']:row}.get)
    m=metrics(a)
    assert m['free_cash_flow_quarter']['values']['free_cash_flow_quarter']==-1300765000
    assert len(m['free_cash_flow_quarter']['evidence_refs'])==2
    assert 'cash_conversion' not in m  # loss-denominator ratio is not a quality score


@pytest.mark.parametrize('question,expected',[
    ('不要减仓，继续持有','hold'),('不要退出，继续持有','hold'),
    ('不再长期持有，决定逐步退出','exit'),('考虑减仓，也可能继续持有','evaluate'),
    ('不想退出','evaluate'),('不要再减仓','evaluate'),('不要再减仓，继续持有','hold'),
    ('Do not reduce my position','evaluate'),("Don't reduce; keep holding",'hold'),
    ('不要清仓','evaluate'),('Do not sell my position','evaluate')])
def test_current_negation_and_ambiguity_never_resurrect_old_exit(question,expected):
    intent=research_intent(dict(research_question=question,research_history=[dict(role='user',content='我决定逐步退出')]))
    assert intent['objective']==expected


def test_review_date_only_does_not_supply_a_default_objective_over_user_question():
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    req=AnalysisRequest(question='我决定逐步退出',research_context={'review_date':'2026-11-20'})
    received=AnalysisRequest.model_validate_json(req.model_dump_json())  # actual parent -> child JSON contract
    intent=research_intent(dict(research_context=received.research_context.model_dump(),research_question=received.question))
    assert intent['objective']=='exit' and intent['review_date']=='2026-11-20'
    explicit=AnalysisRequest(question=req.question,research_context=dict(review_date='2026-11-20',objective='evaluate'))
    assert req.model_dump(mode='json')!=explicit.model_dump(mode='json')  # original canonical idempotency fingerprint distinguishes them
    assert research_intent(dict(research_context=explicit.research_context.model_dump(),research_question=explicit.question))['objective']=='evaluate'


def test_supported_calendar_quarter_start_requires_explicit_duration_and_boundary():
    from research_v2.official_disclosures import _calendar_quarter_start
    assert _calendar_quarter_start('Three months ended June 30, 2026','2026-06-30')=='2026-04-01'
    assert _calendar_quarter_start('Three months ended September 28, 2026','2026-09-28') is None
    assert _calendar_quarter_start('Three months ended January 31, 2026','2026-01-31') is None
    assert _calendar_quarter_start('Year ended June 30, 2026','2026-06-30') is None


def test_zero_capex_keeps_retained_unknown_policy_not_false_FCF():
    from tests.test_sec_real_original import parse,AT as LI_AT
    row=parse();a=assess({},symbol='LI:US',research=[row],at=LI_AT,disclosure_verifier={row['record_id']:row}.get)
    next(r for r in a['verified_field_records'] if r['field']=='capital_expenditures_quarter')['values']['capital_expenditures_quarter']=0
    assert 'free_cash_flow_quarter' not in metrics(a)


def test_PDD_ordinary_shareholder_income_never_asserts_parent_equivalence():
    a=actual_pdd()
    assert 'parent_income_yoy_pct' not in metrics(a)
    assert 'ordinary_income_yoy_pct' in metrics(a)
    assert not any(r.get('accounting_basis')=='net_income_attributable_parent' for r in a['verified_field_records'])


def test_qualified_quote_can_support_valuation_without_becoming_daily_evidence():
    a=valuation_case();a['technical_levels']=None
    a['records'].append(dict(kind='quote',symbol='PDD:US',quality='valid',record_id='synthetic-qualified-quote',
        data_as_of='2026-10-28',values={'current_price':100.,'currency':'USD'}))
    m=metrics(a);assert 'pe_ttm' in m
    p=dict(topic='technical_trend',scope='research_only',basis='inference',
        text='该日线观察价格为{{n1}}，需要结合量能复核趋势持续性，不表示执行指令。',
        evidence_refs=[m['observed_price']['record_id']],numbers=[dict(id='n1',evidence_ref=m['observed_price']['record_id'],field='observed_price')])
    assert not explanation_projection([p],a)[0]
    a['records'][-1]['values']['currency']=None
    assert 'pe_ttm' not in metrics(a) and 'revenue_yoy_pct' in metrics(a)


def test_actual_PDD_expenses_keep_sign_and_reconcile_operating_margin_driver():
    a=actual_pdd();m=metrics(a)
    actual={r['field']:r['values'][r['field']] for r in a['verified_field_records']}
    assert actual['cost_of_revenue_reported_quarter']==-48017000000
    assert actual['sales_marketing_reported_quarter']==-29668000000
    assert actual['general_admin_reported_quarter']==-2342000000
    assert actual['research_development_reported_quarter']==-4567000000
    assert actual['income_tax_reported_quarter']==-6092000000
    effects=[m[f+'_burden_change_pp']['values'][f+'_burden_change_pp'] for f in ['cost_of_revenue','sales_marketing','general_admin','research_development']]
    assert sum(effects)==pytest.approx(-m['operating_margin_change_pp']['values']['operating_margin_change_pp'])
    assert effects[0]<0 and all(v>0 for v in effects[1:])


def test_unreconciled_expense_sign_or_amount_does_not_certify_expense_ratios():
    a=actual_pdd()
    expense=next(r for r in a['verified_field_records'] if r['field']=='sales_marketing_reported_quarter')
    expense['values'][expense['field']]+=1
    m=metrics(a)
    assert 'sales_marketing_burden_pct' not in m
    assert 'revenue_yoy_pct' in m and 'cash_conversion' in m


@pytest.mark.parametrize('ident',['n9','n20','n100'])
def test_numeric_ids_may_be_global_while_bindings_per_paragraph_stay_bounded(ident):
    a=actual_pdd();p=candidate(a)
    p['text']=p['text'].replace('{{n1}}','{{'+ident+'}}');p['numbers'][0]['id']=ident
    assert explanation_projection([p],a)[0]


def test_bound_numbers_cannot_smuggle_extra_unverified_advice_fields():
    a=actual_pdd();p=candidate(a);p['numbers'][0].update(order_quantity=98765,limit_price=888)
    accepted,_=explanation_projection([p],a)
    assert accepted and set(accepted[0]['numbers'][0])=={'id','evidence_ref','field'}
    assert '98765' not in str(accepted) and '888' not in str(accepted)


def test_facts_only_recomputes_metric_cache_and_never_renders_hypothetical_prices():
    from backend.services.market_data.output_policy import facts_only_result
    a=actual_pdd();a['financial_research']={'records':[{'values':{'pe_ttm':98765}}],
        'valuation_sensitivity':[{'hypothetical_price':88888}]}
    current,facts,text=facts_only_result(a,at=AT)
    assert '98765' not in str(current['financial_research']) and not current['financial_research']['valuation_sensitivity']
    assert '88888' not in text and '估值敏感性' not in text
    later=facts_only_result(a,at=AT.replace(year=2027))
    assert not later[0]['financial_research']['records']  # historical facts do not renew current calculations
