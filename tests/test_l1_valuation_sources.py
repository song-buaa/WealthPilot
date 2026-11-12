from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from backend.services import position_analysis
from backend.services.market_data.l1_context import project
from backend.services.market_data.l1_research_v2 import ResearchRun
from backend.services.market_data.quality_valuation import (
    ANCHORED_ORIGIN, NYU_COUNTRY_RISK, REFERENCE_ORIGIN, TREASURY_COLUMN, TREASURY_CURVE,
)
from research_v2.sec_disclosures import QUALITY_CONCEPTS
from tests.test_s3_foreign_ads import AT, assessment


@pytest.fixture
def sources():
    treasury = f'<tr><td><time>11/02/2026</time></td><td headers="{TREASURY_COLUMN}">4.60</td></tr>'.encode()
    risk = b'''<p>Last updated: January 5, 2026</p><table>
    <tr><td>Country</td><td>Country Risk Premium</td><td>Equity Risk Premium</td></tr>
    <tr><td>China</td><td>0.91%</td><td>5.14%</td></tr>
    <tr><td>United States</td><td>0.23%</td><td>4.46%</td></tr></table>'''
    url = TREASURY_CURVE + '?type=daily_treasury_yield_curve'
    fetched = '2026-11-03T00:00:00+00:00'
    treasury_hash = sha256(treasury).hexdigest()
    return {
        'treasury': dict(status='VERIFIED', source_sha256=treasury_hash, original=treasury, source_url=url,
            known_at='2026-11-02T22:00:00+00:00', fetched_at=fetched,
            record=dict(symbol='PDD:US', record_id=f'research:treasury:{treasury_hash}',
                        source='public_primary_research', source_url=url, period='2026-11-02',
                        data_as_of='2026-11-02', published_at='2026-11-02', fetched_at=fetched,
                        content='Official daily Treasury yield curve.')),
        'country_risk': dict(status='VERIFIED', source_sha256=sha256(risk).hexdigest(), original=risk,
            source_url=NYU_COUNTRY_RISK, known_at='2026-01-05T20:00:00+00:00', fetched_at=fetched),
    }


def cash_bridge():
    # Three comparable years with one statement basis. Only the source-owner
    # contract is synthetic. Valuation still reuses the qualified market fixture.
    fields = dict(cfo=100, productive_asset_payments=3, merchant_float_change=5, sbc=4,
                  net_income=95, diluted_shares=100)
    bases = {name.removeprefix('quality_'): basis for name, basis in QUALITY_CONCEPTS.values()}
    fetched = '2026-11-02T22:00:00+00:00'
    published = '2026-04-29T10:15:54+00:00'
    proof = dict(record_id='sec:fixture:20-f', document_type='20-F',
                 source_document_sha256='e'*64, source_url='https://www.sec.gov/Archives/fixture.txt',
                 published_at=published, fetched_at=fetched)
    return [
        dict(field=field, symbol='PDD:US', quality='valid', kind='quality_financial_field',
             source='official_disclosure', record_id=f'cash:{year}:{field}', accounting_basis=bases[field],
             fiscal_period_start=f'{year}-01-01', period=f'{year}-12-31', scope='annual',
             currency=None if field == 'diluted_shares' else 'CNY',
             unit='shares' if field == 'diluted_shares' else 'currency_units',
             published_at=published, fetched_at=fetched, publication_links=[proof], values={field: amount})
        for year in (2023, 2024, 2025) for field, amount in fields.items()
    ]


@pytest.mark.parametrize('problem', [None, 'missing', 'stale', 'hash', 'wrong_session', 'future', 'bridge'])
def test_prepare_binds_before_catalog_and_reuses_request_receipts(monkeypatch, tmp_path, sources, problem):
    a = assessment()
    annual = cash_bridge()
    if problem == 'bridge':
        annual.pop()
    # Match the captured three-year sample's single qualified comparison.
    a['quality_field_records'] = [
        row for row in a['quality_field_records']
        if not (row['field'] == 'diluted_shares' and row['period'] == '2025-06-30'
                and '25-082502' in row['record_id'])
    ] + annual
    if problem == 'missing':
        sources.pop('treasury')
    elif problem == 'stale':
        sources['treasury'] = {'status': 'STALE', 'reason': 'STALE_CAPTURE'}
    elif problem == 'hash':
        sources['country_risk']['original'] += b' '
    elif problem == 'wrong_session':
        sources['treasury']['record']['period'] = '2026-11-01'
    elif problem == 'future':
        sources['treasury']['known_at'] = '2026-11-04T00:00:00+00:00'
    from backend.services.market_data import l1_valuation

    root = tmp_path.resolve()
    directory = root / 'PDD:US' / '2026-11-02'
    directory.mkdir(parents=True)
    manifest = dict(symbol='PDD:US', observation_date='2026-11-02')
    for key, (field, filename) in l1_valuation.SOURCE_FILES.items():
        if key not in sources:
            continue
        item = dict(sources[key])
        original = item.pop('original', None)
        if original:
            path = directory / filename
            path.write_bytes(original)
            path.chmod(0o600)
        manifest[field] = item
    path = directory / 'manifest.json'
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    monkeypatch.setenv(l1_valuation.SOURCE_ROOT_ENV, str(root))
    reads = []
    original_reader = l1_valuation.read_inputs

    def read(symbol, observed):
        reads.append((symbol, observed))
        return original_reader(symbol, observed)

    monkeypatch.setattr(l1_valuation, 'read_inputs', read)
    monkeypatch.setattr('backend.skills.invoke_skill', lambda *args, **kwargs: deepcopy(a))
    monkeypatch.setattr('backend.services.allocation_service.get_targets', list)
    loaded = SimpleNamespace(research_evidence=[], positions=[
        dict(symbol='PDD:US', is_virtual=problem == 'missing'),
    ])
    run = SimpleNamespace(loaded_data=loaded, market_data=SimpleNamespace(symbol='PDD:US'), position_evidence={})
    plan = SimpleNamespace(portfolio_id=1, planner_trace={})
    question = '请评估是否值得新建仓，并研究估值与长期回报。'
    first = position_analysis.prepare(plan, run, at=AT, options=dict(
        enable_l1_research_v2=True, research_question=question))
    assert len(reads) == 1 and reads[0][:2] == ('PDD:US', '2026-11-02')
    assert first['l1_holding_context'] == dict(
        stored_position='unknown' if problem == 'missing' else 'present',
        snapshot='unverified', entry_counterfactual=True)
    origin = REFERENCE_ORIGIN if problem else ANCHORED_ORIGIN
    assert first['conditional_valuation']['proposal_origin'] == origin
    statuses = first['l1_valuation_sources']
    assert all(v['status'] == 'VERIFIED' for v in statuses.values()) == (problem is None)
    run.position_evidence = first
    later = position_analysis.prepare(plan, run, at=datetime.fromisoformat(a['as_of']),
                                      options=dict(research_question=question))
    assert len(reads) == 1
    assert later['conditional_valuation'] == first['conditional_valuation']
    if problem is None:
        assert first['conditional_valuation'].get('owner_cash_model'), first['conditional_valuation'].get('missing')
        groups, _ = project(first)
        research = ResearchRun()
        research.prepare(groups, first)
        assert {c['label'] for c in groups['scenario_catalog']} == {'OWNER_CASH_CONDITIONAL_NOT_FORECAST'}
        assert len(research.qualify(later)['scenario_cells']) == 3
        changed = deepcopy(later)
        changed['country_risk_assumption_source']['source_sha256'] = 'f'*64
        changed['conditional_valuation']['owner_cash_model']['risk_source'] = changed['country_risk_assumption_source']
        assert not research.qualify(changed)['scenario_cells']
