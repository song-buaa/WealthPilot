from copy import deepcopy

import pytest

from backend.services.market_data.l1_context import project
from backend.services.market_data.l1_research_v2 import ResearchRun
from backend.services.market_data.quality_valuation import foreign_ads_earnings_research


@pytest.fixture(scope='module')
def frozen_pdd():
    from tests.test_s3_foreign_ads import assessment, assumptions

    value = assessment()
    value['conditional_valuation'] = foreign_ads_earnings_research(value, assumptions())
    return value


def prepare(frozen_pdd):
    run = ResearchRun()
    # Exercise the C1 catalog independently of the runner's tokenizer cache.
    # Full-message preflight is covered by test_l1_context_pack.
    groups, _ = project(frozen_pdd)
    run.prepare(groups, frozen_pdd)
    assert len(run.cells) == 3
    assert {cell['ref'] for cell in groups['scenario_catalog']} == run.cells.keys()
    return run


def candidate(run, ref, assumption):
    return dict(key='value', role='supporting', claim_kind='inference', question_ids=['Q4'],
                text_template='盈利能否长期保持仍需检验，条件估值只能在对应研究假设成立时使用。',
                evidence_refs=[next(iter(run.visible))], scenario_cell_refs=[ref], assumption_refs=[assumption])


def ingest(run, *claims):
    run.ingest({'debates': [{'debate_key':'valuation', 'claims':list(claims)}]})


def test_s3_visible_cells_close_ads_receipts_and_preserve_exact_owner_results(frozen_pdd):
    run = prepare(frozen_pdd)
    ref, (name, cell, assumption) = next(iter(run.cells.items()))
    assert cell['result']['annualized_return'] == frozen_pdd['conditional_valuation']['scenarios'][name]['annualized_return']
    assert set(cell['receipts']['ads_ratio']['evidence_refs']) <= cell['dependencies'].keys()
    ingest(run, candidate(run, ref, assumption))
    final = run.qualify(frozen_pdd, narrow=True)
    assert len(final['claims']) == 1 and final['decision_review'] == 'not_assessed'
    assert final['coverage']['Q4']['scenario_cell_refs'] == [ref]
    assert final['conditional_valuation']['sensitivity'] == []


def test_reject_unsent_cross_request_and_mislabeled_assumptions(frozen_pdd):
    run = prepare(frozen_pdd)
    other = prepare(frozen_pdd)
    foreign, (_, _, other_assumption) = next(iter(other.cells.items()))
    ref, (_, _, assumption) = next(iter(run.cells.items()))
    ingest(run, candidate(run, foreign, other_assumption),
        candidate(run, f'cell:{run.nonce}:post_calculated', assumption) | {'key':'future'},
        candidate(run, ref, other_assumption) | {'key':'fake_assumption'},
        candidate(run, ref, assumption) | {'key':'fake_fact', 'claim_kind':'fact'},
        candidate(run, ref, assumption) | {'key':'good'})
    final = run.qualify(frozen_pdd)
    assert len(final['claims']) == 1
    assert final['coverage']['Q4']['status'] == 'limited'
    assert final['diagnostic']['rejected'] == {'scenario_not_visible_or_qualified':2,
        'assumption_reference_invalid':1, 'shape_invalid':1}


@pytest.mark.parametrize('mutation', ['result', 'ads', 'clock', 'origin', 'fx', 'issuer'])
def test_final_requalification_rejects_changed_cells_and_dependent_claims(frozen_pdd, mutation):
    run = prepare(frozen_pdd)
    ref, (name, _, assumption) = next(iter(run.cells.items()))
    claim = candidate(run, ref, assumption)
    ingest(run, claim, claim | dict(key='dependent', role='synthesis', requires=['value'],
        question_ids=['Q1'], scenario_cell_refs=[], assumption_refs=[]))
    assert len(run.qualify(frozen_pdd, narrow=True)['claims']) == 2
    changed = deepcopy(frozen_pdd)
    owner = changed['conditional_valuation']
    if mutation == 'result':
        owner['scenarios'][name]['annualized_return'] = '0.99'
    elif mutation == 'ads':
        owner['input_receipts']['ads_ratio']['ordinary_shares_per_ads'] = 2
    elif mutation == 'clock':
        owner['input_receipts']['decision_at'] = '2020-01-01T00:00:00+00:00'
    elif mutation == 'origin':
        owner['assumptions']['origin'] = 'system_qualified_reference_sensitivity'
    elif mutation == 'issuer':
        owner['symbol'] = 'LI:US'
    else:
        owner['qualified_inputs']['fx_cny_per_usd'] = 7
    final = run.qualify(changed, narrow=True)
    assert ref not in final['scenario_cells'] and not final['claims']
    assert not run.qualify(frozen_pdd)['claims']  # A later adapter cannot restore rejected authority.


def test_original_freshness_owner_removes_expired_price_and_blocks_c1(frozen_pdd):
    from datetime import datetime, timedelta
    from backend.services.market_data.output_policy import captured_facts

    run = prepare(frozen_pdd)
    ref, (_, _, assumption) = next(iter(run.cells.items()))
    ingest(run, candidate(run, ref, assumption))
    current, _ = captured_facts(frozen_pdd, at=datetime.fromisoformat(frozen_pdd['as_of']) + timedelta(days=90))
    final = run.qualify(current)
    assert not final['scenario_cells'] and not final['claims']
    assert (final['conditional_valuation'] or {}).get('status') != 'CONDITIONAL_RESEARCH_ONLY'


def test_group_format_and_bound_units_do_not_discard_independent_claims(frozen_pdd):
    run = prepare(frozen_pdd)
    from backend.services.market_data.output_policy import _evidence_index

    index = _evidence_index(frozen_pdd)
    ref, row = next((ref, row) for ref, row in index.items()
                    if ref in run.visible and row.get('field') == 'earnings_yield_pct')
    item = dict(key='bound', role='supporting', claim_kind='inference', question_ids=['Q3'],
                text_template='可核验的盈利收益率为{{n1}}%，该参考参数不能直接代表未来回报。',
                evidence_refs=[ref], numbers=[dict(id='n1', evidence_ref=ref, field=row['field'])])
    invalid = item | dict(key='unbound', text_template='可核验的盈利收益率为{{n1}}%，另有{{n2}}个百分点尚待核对。')
    literal = item | dict(key='literal', text_template='可核验的盈利收益率为{{n1}}%，另有5%的门槛。')
    synthesis = item | dict(key='dependent', role='synthesis', question_ids=['Q1'], requires=['unbound'])
    limited_competition = item | dict(key='limited_competition', question_ids=['Q2'],
        text_template='合格盈利收益率为{{n1}}%，不足以证明该企业已建立竞争优势。')
    independent = item | dict(key='independent', numbers=[], question_ids=['Q6'],
                              text_template='应比较同币种、同期限的可核验替代回报，该参数尚缺。')
    run.ingest({'debates': [
        {'debate_key':'long_label_that_should_not_discard_all_its_claims', 'claims':[item, invalid, literal]},
        {'debate_key':'long_label_that_should_not_discard_all_its_claims', 'claims':[synthesis, independent, limited_competition]},
    ]})
    final = run.qualify(frozen_pdd, narrow=True)
    assert len(final['claims']) == 3
    assert final['claims'][0]['paragraph']['text'].count('%') == 1
    assert final['claims'][0]['paragraph']['text_template'].count('%') == 0
    assert final['coverage']['Q1']['status'] == 'rejected'
    assert final['coverage']['Q6']['status'] == 'limited'
    assert final['diagnostic']['rejected'] == {'text_not_safe_qualitative': 2, 'dependency_or_key_invalid': 1}
    assert final['diagnostic']['text_rejection_details'] == {'unbound_numeric_literal': 2}


def test_synthesis_inherits_only_accepted_current_premises(frozen_pdd):
    from backend.services.market_data.l1_research_v2 import SYNTHESIS_TEMPLATE, report

    run = prepare(frozen_pdd)
    ref, (_, _, assumption) = next(iter(run.cells.items()))
    value = candidate(run, ref, assumption)
    other = next(r for r in run.visible if r not in value['evidence_refs'])
    business = dict(key='op', role='supporting', claim_kind='inference', question_ids=['Q3'],
        text_template='收入能否持续变成股东收益，仍需核对本期利润与现金转化。', evidence_refs=[other])
    synthesis = dict(key='choice', role='synthesis', claim_kind='inference', question_ids=['Q1', 'Q6'],
                     text_template=SYNTHESIS_TEMPLATE, decision='lower_priority', requires=['op','value'])
    ingest(run, business, value, synthesis,
           synthesis | dict(key='unsupported', text_template='公司已经签约取得新的客户并将给股东发放现金。'),
           synthesis | dict(key='added_ref', evidence_refs=[next(r for r in run.visible
                                                       if r not in value['evidence_refs'] + [other])]))
    final = run.qualify(frozen_pdd, narrow=True)
    assert len(final['claims']) == 3
    choice = final['claims'][-1]
    assert set(choice['paragraph']['evidence_refs']) == {*value['evidence_refs'], other}
    assert choice['claim_id'] in final['coverage']['Q1']['claim_ids']
    assert choice['claim_id'] in final['coverage']['Q6']['claim_ids']
    assert '降低该长期投资命题的研究优先级' in report(final)
    assert final['diagnostic']['rejected'] == dict(synthesis_requires_decision_contract=1, synthesis_cannot_add_inputs=1)
    changed = deepcopy(frozen_pdd)
    changed['conditional_valuation']['scenarios']['cautious']['annualized_return'] = '0.999'
    assert all(c['role'] != 'synthesis' for c in run.qualify(changed)['claims'])
