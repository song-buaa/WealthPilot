from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import json

from backend.services import s3_holder_returns as holder
from backend.services import s3_multimethod as multimethod
from backend.services import s3_relative_value as relative


def date(year, month=1):
    return datetime(year, month, 1, tzinfo=timezone.utc)


def test_purchase_after_eligibility_never_receives_still_unpaid_cash():
    start, end = date(2026), date(2031)
    schedule = [
        dict(eligibility_at=date(2027).isoformat(), paid_at=date(2027,2).isoformat(), per_share_usd='5'),
        dict(eligibility_at=date(2028).isoformat(), paid_at=date(2028).isoformat(), per_share_usd='3'),
    ]
    case = dict(terminal_price='120', implied_revenue_cagr_for_required_return='.10')
    first = holder.settle(case, '100', '.10', start, end, start, schedule)
    refresh = holder.settle(first, '101', '.10', start, end, date(2027), schedule)
    assert first['dividends_cash'] == '8.00000000'
    assert refresh['dividends_cash'] == '3.00000000'
    assert refresh['terminal_price'] == '120'
    assert refresh['terminal_wealth'] == '123.00000000'
    assert refresh['zero_cash_revenue_hurdle_at_origin'] == '.10'
    assert refresh['included_cash_distributions'] == schedule[1:]
    assert Decimal(refresh['conditional_value']) < Decimal(refresh['terminal_wealth'])


def test_no_future_cash_event_outside_fixed_window():
    start, end = date(2026), date(2031)
    bad = [dict(eligibility_at=date(2030).isoformat(), paid_at=date(2032).isoformat(), per_share_usd='1')]
    with pytest.raises(ValueError, match='DISTRIBUTION_REJECTED'):
        holder.settle(dict(terminal_price='100'), '100', '.10', start, end, start, bad)


def test_googs_enterprise_method_does_not_assign_listed_returns():
    sources = {
        'quarter':dict(source_sha256='b74e29c9e29e1287ddfb71da78577fcf7f1d26dd0aad599dbea9b778b465a6b1'),
        'preferred_terms':dict(source_sha256='0564227c40214d75adafe3247980d853f2baaeb04f48f18de073beafbcfb4ff3'),
    }
    card = dict(symbol='GOOG:US', scenarios={})
    multimethod.apply([card], {'GOOG:US':dict(sources=sources, reviewed_at=date(2026).isoformat())}, {}, date(2026),date(2031))
    assert card['holder_return_scope'] == holder.UNKNOWN
    assert card['scenarios'] == {} and 'ordinary' in card['failure_reason'].lower()
    assert len(card['enterprise_research']['cases']) == 3
    profile=json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['GOOG:US']
    assert multimethod.alphabet_operating_cases({'sources': sources, 'reviewed_at':date(2025).isoformat()},
        profile, date(2026)) is None


def test_bounded_results_cannot_enter_formal_rank_or_pairs(monkeypatch):
    def rank(cards, _):
        assert [card['symbol'] for card in cards] == ['FULL:US']
        cards[0].update(rank=1,group='candidate')

    monkeypatch.setattr(relative.engine,'_rank',rank)
    cases = dict(base=dict(annualized_return='.2'), cautious=dict(total_return='.1'))
    cards = [dict(symbol='FULL:US', scenarios=cases, holder_return_scope=holder.FULL),
             dict(symbol='BOUNDED:US', scenarios=cases, holder_return_scope=holder.BOUNDED,
                  relative_return=['stale'], holder_return_scope_reason='OPERATING_ONLY')]
    assert relative.rank_comparisons(cards,SimpleNamespace(tie_band=Decimal('.01'))) == 1
    assert all(c['rank'] is None and not c.get('relative_return') for c in cards)
    assert relative.relative_stress(cards,None)['status'] == 'INSUFFICIENT_COMPARABLE_ISSUERS'


def test_claim_states_use_physical_forward_and_do_not_invent_common_weights():
    module = multimethod
    terms = dict(status='COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD',
        published_inputs={'forward_contracts_shares':[40_000_000,6_000_000]},
        evidence={'filing':{}}, derived={'preferred':{}})
    result = module.equity_components('META:US',{'qualification_components':terms})
    assert result['forward_reference_shares'] == 46_000_000
    assert [c['security_package'] for c in result['cases']] == ['A','A_PLUS_ONE_C_PER_ELIGIBLE_A','A']
    assert result['cases'][1]['weights'] == 'UNKNOWN_ECONOMIC_SPLIT'
    assert 'scenarios' not in result and 'ordinary_price' not in result


def test_observed_tesla_asset_overlay_does_not_change_base_projection():
    profiles = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())
    anchor = profiles['TSLA:US']
    source = dict(quarter=dict(source_sha256=anchor['sources']['quarter']))
    card = dict(cross_checks=dict(observed_price=dict(values=dict(observed_price='100'))),
                scenarios=dict(base=dict(terminal_wealth='105', years=[dict(relative_shares='1.05')])))
    results = multimethod.tesla_investment_stress(card,
        dict(sources=source, reviewed_at=date(2026).isoformat()),anchor,date(2026))
    assert results['cases'][0]['terminal_increment_per_share_usd']=='0.00000000'
    assert 0 < Decimal(results['cases'][2]['terminal_increment_per_share_usd']) < 1
    assert card['scenarios']['base']['terminal_wealth'] == '105'

def test_preferred_conversion_rounds_each_share_and_exercises_middle_band():
    terms = dict(conversion_min='2.2520', conversion_max='2.8160', liquidation_usd='1000')
    components = dict(status='COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD',
        published_inputs={'preferred':{'A':terms}},evidence={},
        derived={'preferred':{'A':{'underlying_preferred_shares':9_625_000}}})
    state = multimethod.equity_components('GOOG:US', {'qualification_components':components})
    cases = state['converted_shares_by_hypothetical_vwap']['A']
    assert [c['unadjusted_common_per_preferred'] for c in cases] == ['2.8160','2.3810','2.2520']
    assert cases[1]['unadjusted_common_shares']=='22917125.00000000'


def test_economic_scope_uses_evidence_and_decision_materiality_not_ticker():
    card = dict(symbol='UNRELATED:US',cash_return_contract={'schedule':[]})
    profile = dict(sources=['q'],rationale='verified holder scope')
    ev={'q':{'source_sha256':'q'}}
    multimethod.qualify_economic_scope(card,profile,ev)
    assert card['holder_return_scope'] == holder.FULL
    card['nonoperating_materiality'] = dict(bounded_economic_range=False, decision_threshold_crossed=False)
    multimethod.qualify_economic_scope(card,profile,ev)
    assert card['holder_return_scope'] == holder.BOUNDED
    card['nonoperating_materiality'] = dict(bounded_economic_range=True, decision_threshold_crossed=False,
        relative_comparison_sensitive=False,cautious_downside_sensitive=False)
    multimethod.qualify_economic_scope(card,profile,ev)
    assert card['holder_return_scope'] == holder.FULL
    multimethod.qualify_economic_scope(card,profile,{})
    assert card['holder_return_scope'] == holder.BOUNDED


def test_required_residual_envelope_cannot_be_omitted():
    card = dict(symbol='ANY:US', cash_return_contract={'schedule':[]})
    multimethod.qualify_economic_scope(card,
        dict(sources=['q'], rationale='requires financial ownership bridge', residual_required=True),
        {'q':{'source_sha256':'q'}})
    assert card['holder_return_scope'] == holder.BOUNDED


def test_class_c_close_uses_share_observation_not_quote_date(monkeypatch):
    import json
    from research_v2 import sec_disclosures as sec
    from backend.services import s3_method_research as method

    labels=[]
    def source(_,record,url,earliest,cutoff):
        assert record['url']==url
        return json.dumps({'filings':{'recent':{'accessionNumber':['a'], 'filingDate':['2026-11-24'],
            'acceptanceDateTime':['2026-11-24T19:00:00Z']}}}).encode(),cutoff.isoformat()
    def listed(root,entry,symbol,interval,earliest,cutoff):
        labels.append(interval)
        return {'price':'270.98'}
    monkeypatch.setattr(relative,'source',source)
    monkeypatch.setattr(relative,'listed_close',listed)
    monkeypatch.setattr(method,'identity_projection',lambda *a: ({'cik':'0001652044','sec_ticker':'GOOG'},None,None))
    entry=dict(directory={'url':sec.DIRECTORY},submissions={'url':'https://data.sec.gov/submissions/CIK0001652044.json'})
    relative.qualify_class_c_close(None,entry,'GOOG:US','2026-11-27T16:05:00Z',
        datetime(2026,11,26,tzinfo=timezone.utc),datetime(2026,11,28,tzinfo=timezone.utc))
    assert labels==['2026-10-21']
    with pytest.raises(ValueError,match='NEW_FILING'):
        relative.qualify_class_c_close(None,entry,'GOOG:US','2026-11-23T16:05:00Z',
            datetime(2026,11,26,tzinfo=timezone.utc),datetime(2026,11,28,tzinfo=timezone.utc))


def test_modeled_materiality_is_not_investment_stability():
    card=dict(cash_return_contract={'schedule':[]},scenarios={k:{} for k in ('cautious','base','optimistic')})
    bridge=dict(status='MODELED_CONDITIONAL_ORDINARY_SCOPE',
                source_evidence={k:{} for k in ('quarter','preferred_terms','eur_notes','cad_notes')},
                decision_threshold_crossed=True)
    multimethod.qualify_modeled_scope(card,bridge)
    assert card['holder_return_scope']==holder.FULL
    bridge['source_evidence'].pop('cad_notes')
    multimethod.qualify_modeled_scope(card,bridge)
    assert card['holder_return_scope']==holder.BOUNDED


def test_alphabet_overlay_never_pays_hedge_twice():
    from backend.services.s3_equity_bridges import alphabet_option_overlay
    overlay=dict(capped_calls={'C':['21887250','439.7537','527.7974']},
                 unused_atm_authority_usd_m='27600',waymo_facility_usd_m='5000')
    cash=alphabet_option_overlay(Decimal(600),overlay,['1','.75','1','.5','.5','CASH'],Decimal(3000))
    stock=alphabet_option_overlay(Decimal(600),overlay,['1','.75','1','.5','.5','NET_SHARES'],Decimal(3000))
    assert cash['hedge_shares']==stock['hedge_cash']==0
    assert abs(cash['hedge_cash']-stock['hedge_shares']*600)<Decimal('.0000001')
    assert cash['atm_shares']==46 and cash['atm_net_retained']==20700 and cash['waymo_net_claim']==2500
    assert cash['coupon_shares']==Decimal(1500)/582 and cash['coupon_cash']==1500


def test_nvda_forecast_uses_direct_eps_not_missing_parent_income():
    from backend.services.s3_listed_eps import project
    case=project(Decimal('9.31'),Decimal('.60'),
                 [['0']*5,'.60','.20','1','0','20'],Decimal('.20'))
    assert case['years'][-1]['eps']=='9.31000000'
    assert case['terminal_price']=='186.20000000'


def test_nvda_portfolio_uses_normalized_eps_and_counts_the_convertible_once():
    from backend.services import s3_listed_eps

    profile = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['NVDA:US']['listed_eps']
    eps = Decimal('9.31')
    margin = Decimal('239.090') / Decimal('361.808')
    scope = profile['economic_scope']
    case = s3_listed_eps.portfolio_case(eps, margin, profile['parameters']['base'],
                                       Decimal('.20'), scope, 'base')
    v = case['equity_scope_components']
    assert Decimal(v['normalized_research_eps']) == Decimal('8.28095680')
    assert Decimal(v['guarantee_loss_usd_m']) == Decimal('17700')
    assert Decimal(v['pending_acquisition_claim_usd_m']) == Decimal('12400')
    assert Decimal(v['risk_adjusted_assets_usd_m']) == Decimal('121333.25')
    assert Decimal(v['claims_usd_m']) == Decimal('86957')
    assert abs(Decimal(case['terminal_price']) - Decimal('361.0249')) < Decimal('.001')


def test_meta_conditional_stock_dividend_prices_the_actual_holder_package():
    from backend.services.s3_equity_bridges import meta_ordinary_case

    profile = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['META:US']['holder_bridge']
    params = profile['cases']['base']
    standalone = meta_ordinary_case(profile, Decimal(46), params, distribution=False)
    package = meta_ordinary_case(profile, Decimal(46), params, distribution=True)
    assert standalone['terminal_c_per_share'] is None
    assert standalone['terminal_price'] == standalone['terminal_a_per_share']
    assert Decimal(package['projected_c_m']) > Decimal(package['projected_a_and_b_m'])
    a, c = map(Decimal, (package['terminal_a_per_share'], package['terminal_c_per_share']))
    assert abs(Decimal(package['terminal_price']) - a - c) <= Decimal('.00000001')
    assert a > c
    assert Decimal(package['forward_cash_retained_usd_m']) < Decimal(standalone['forward_cash_retained_usd_m'])


def test_meta_price_only_contract_expires_before_potential_class_c_entitlement():
    with pytest.raises(ValueError, match='DISTRIBUTION_ENTITLEMENT_REQUIRES_RESEARCH'):
        relative.qualify_meta_close(None, {}, 'META:US', '2026-11-27', '2026-10-16',
                                    date(2027), datetime(2027, 1, 19, tzinfo=timezone.utc))


def test_joint_requalification_replaces_only_joint_blockers():
    warnings = ['JOINT_SENSITIVITY_INCOMPLETE', 'QUALITY_OR_DOWNSIDE_REMAINS']
    row = {'result': {'annualized_return': '.10'}}
    card = dict(joint_sensitivity=[row]*9, joint_qualification={}, research_method_blockers=warnings)
    holder.qualify_joint_sensitivity(card, '.10')
    assert card['joint_qualification'] == dict(complete=True, all_above_required=True)
    assert card['research_method_blockers'] == warnings[1:]
    card['joint_sensitivity'][-1] = {'result': {'annualized_return': '.099'}}
    holder.qualify_joint_sensitivity(card, '.10')
    assert card['research_method_blockers'] == [warnings[1], 'JOINT_STRESS_RETURN_THRESHOLD_CROSSING']
    card['joint_sensitivity'].pop()
    holder.qualify_joint_sensitivity(card, '.10')
    assert card['research_method_blockers'] == warnings[::-1]


def test_meta_additional_c_is_ratable_and_all_c_changes_only_delivery_legs():
    from backend.services.s3_equity_bridges import meta_ordinary_case

    profile = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['META:US']['holder_bridge']
    base = profile['cases']['base']
    paired = meta_ordinary_case(profile, Decimal(46), base, distribution=True)
    delivered = meta_ordinary_case(profile, Decimal(46), base, distribution=True,
                                   capital=dict(all_c_fraction=1))
    extra = meta_ordinary_case(profile, Decimal(46), base, distribution=True,
                                capital=dict(founder_events=1))
    assert Decimal(paired['projected_a_and_b_m']) - Decimal(delivered['projected_a_and_b_m']) == 46
    assert Decimal(delivered['projected_c_m']) - Decimal(paired['projected_c_m']) == 46
    assert Decimal(delivered['forward_cash_retained_usd_m']) < Decimal(paired['forward_cash_retained_usd_m'])
    assert Decimal(extra['terminal_c_shares_per_original_a']) == 3
    assert extra['ordinary_equity_usd_m'] == paired['ordinary_equity_usd_m']


def test_ant_distinguishes_contract_overlap_and_residual_convertible():
    from backend.services.s3_ant_research import cash_case, contract_cash, opening_bridge

    profile = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['ANT:US']
    facts, settings = profile['reported'], profile['cases']['base']
    assert opening_bridge(facts)['excluded_secondary_gross_bn'] == '1.96283762'
    assert contract_cash(Decimal(80), Decimal(60), Decimal('.5')) == (110, 30)
    converted = cash_case(facts, dict(settings, legacy_note_conversion_fraction='1'))
    claim = cash_case(facts, dict(settings, legacy_note_conversion_fraction='0'))
    assert converted['terminal_retained_note_claim_bn'] == '0.00000000'
    assert Decimal(claim['residual_principal_pv_bn']) < Decimal(facts['remaining_note_nominal_bn'])
    assert Decimal(claim['terminal_retained_note_claim_bn']) > 0
    assert Decimal(converted['model_years'][0]['shares_bn']) > Decimal(claim['model_years'][0]['shares_bn'])
    first = converted['model_years'][0]
    assert Decimal(first['compute_cash_bn']) > max(Decimal(first['core_compute_cash_bn']),
                                                   Decimal(first['contract_due_bn']))
    assert Decimal(converted['post_endpoint_commitment_pv_bn']) > 0


@pytest.mark.parametrize('form', ['424B4', '424B5'])
def test_reviewed_primary_filing_remains_hash_bound_after_transport_rotation(tmp_path, monkeypatch, form):
    import hashlib
    from backend.services import s3_relative_profiles as profiles

    digest = lambda body: hashlib.sha256(body).hexdigest()
    at = date(2026)
    issuer = {'cik': '0000000001'}
    url = 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/form.htm'
    accession = '0000000001-26-000001'
    prefix = b'<html><body>Reviewed disclosure: 123.'
    ending = b'</body></html>\n</TEXT>\n</DOCUMENT>\n'
    def response(path):
        return prefix+b'<script type="text/javascript"  src="'+path+b'"></script>'+ending

    old = response(b'/old/a/b/c/d/e/f')
    new = response(b'/new/a/b/c/d/e/f')
    expected = dict(accession=accession, locator='Reviewed term', primary='form.htm', form=form,
                    sha256=digest(old), reviewed_without_trailing_sec_script_sha256=digest(prefix+ending))
    sub = json.dumps({'filings':{'recent':{'accessionNumber':[accession], 'form':[form],
                     'primaryDocument':['form.htm'], 'acceptanceDateTime':['2025-12-31T18:00:00Z']}}}).encode()
    def save(name, body, source):
        (tmp_path/name).write_bytes(body)
        return dict(path=name, url=source, fetched_at=at.isoformat(), sha256=digest(body))
    entry = {'directory':save('directory.json', b'{}', profiles.sec.DIRECTORY),
             'submissions':save('submissions.json', sub, 'https://data.sec.gov/submissions/CIK0000000001.json')}
    row = save('form.htm', new, url)
    dimensions = {k:dict(fact='fact', judgment='judgment', counterevidence='limit', evidence=['reviewed'])
                  for k in profiles.DIMENSIONS}
    dossier = dict(cik=issuer['cik'], reviewed_at=at.isoformat(), sources={'reviewed':expected},
                   dimensions=dimensions, valuation_method='proof', equity_bridge={})
    book = dict(version='proof', companies={'AAA:US':dossier})
    manifest = dict(companies={'AAA:US':entry}, requests=[row])
    monkeypatch.setattr(profiles, 'resolve_sec_issuer', lambda *args, **kwargs: issuer)
    monkeypatch.setattr(profiles.sec, 'discover_filings', lambda *args: [])
    def qualify():
        return profiles.quality_cards(tmp_path, manifest, at, book)['AAA:US']

    proof = qualify()
    assert proof['sources']['reviewed']['source_sha256'] == digest(new)
    assert proof['sources']['reviewed']['reviewed_source_sha256'] == digest(old)
    assert multimethod._review_sources(proof, {'sources':{'reviewed':digest(old)}}, at)
    manifest['requests'][0] = save('form.htm', new.replace(b'123', b'124'), url)
    assert qualify()['reason'] == 'S3_QUALITY_REVIEWED_BYTES_CHANGED'
    manifest['requests'][0] = save('form.htm', new.replace(b'</script>', b'</script><span/>'), url)
    assert qualify()['reason'] == 'S3_QUALITY_REVIEWED_BYTES_CHANGED'
    manifest['requests'][0] = dict(save('form.htm', new, url), sha256=digest(old))
    assert qualify()['reason'] == 'S3_QUALITY_SOURCE_HASH_OR_CLOCK_REJECTED'
    manifest['requests'][0] = save('form.htm', new, url)
    manifest['requests'][0]['fetched_at'] = date(2026,2).isoformat()
    assert qualify()['reason'] == 'S3_QUALITY_SOURCE_HASH_OR_CLOCK_REJECTED'
