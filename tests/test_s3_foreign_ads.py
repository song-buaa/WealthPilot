"""Real frozen PDD SEC and AV data, with an actual provider FX receipt."""
import hashlib,json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from copy import deepcopy
from backend.services.execution_plan.kline_provider import decode_av_daily
from backend.services.market_data.technical_evidence import from_bars
from backend.services.market_data.evidence import assess
from backend.services.market_data.quality_valuation import foreign_ads_earnings_research
from tests.test_p1_1_research_quality import official_rows

ROOT=Path(__file__).parent/'fixtures/s3_foreign_ads'
AT=datetime.fromisoformat('2026-11-03T04:00:00+00:00')

def assessment():
    receipt=json.loads((ROOT/'receipt.json').read_text())
    raw=(ROOT/'PDD-compact-daily.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==receipt['price']['sha256']
    bars=decode_av_daily(json.loads(raw),'PDD:US',count=300,strict_identity=True)
    tech=from_bars('PDD:US',bars,at=datetime.fromisoformat(receipt['price']['fetched_at']))
    rows=official_rows();proof={r['record_id']:r for r in rows}
    a=assess({'technical':asdict(tech)},symbol='PDD:US',research=rows,at=AT,disclosure_verifier=proof.get)
    a['research_fx']=receipt['fx']
    return a

def assumptions():
    case=dict(earnings_growth='.05',annual_share_change='-.01',terminal_pe='10',dividend_payout='0')
    return dict(basis='S3_RESEARCH_ASSUMPTION_NOT_POLICY',rationale='Five-year research scenario only; not a management forecast.',
                required_return='.10',cautious=dict(case,earnings_growth='-.05',terminal_pe='8'),
                base=case,optimistic=dict(case,earnings_growth='.15',terminal_pe='12'))

def test_real_pdd_adss_eps_price_fx_enter_original_s3_five_year_arithmetic():
    r=foreign_ads_earnings_research(assessment(),assumptions())
    assert r['status']=='CONDITIONAL_RESEARCH_ONLY' and not r['execution_authorized']
    q=r['qualified_inputs']
    assert q['eps_cny_per_ads']==63.14 and q['price_usd_per_ads']==81.68
    assert q['ordinary_shares_per_ads']==4 and q['fx_cny_per_usd']==6.6483
    assert len(q['evidence_refs'])>=6 and r['conditional_value'] is None
    assert 'conditional_value' not in r['scenarios']['base']
    assert 'margin_to_conditional_value' not in r['scenarios']['base']
    assert r['input_receipts']['decision_at']==AT.isoformat()
    assert r['input_receipts']['ads_ratio']['evidence_refs']
    assert r['input_receipts']['fx']['details']['rate_date']=='2026-11-02'
    eps=Decimal('63.14')/Decimal('6.6483')
    terminal=eps*Decimal('1.05')**5/Decimal('.99')**5*Decimal(10)
    assert abs(Decimal(r['scenarios']['base']['terminal_price'])-terminal)<Decimal('.00000001')
    assert abs(Decimal(r['scenarios']['base']['annualized_return'])-((terminal/Decimal('81.68'))**(Decimal(1)/5)-1))<Decimal('.00000001')

def test_missing_or_mismatched_unit_fx_never_produces_scenario():
    a=assessment();a['research_fx']={'quality':'unavailable'}
    assert foreign_ads_earnings_research(a,assumptions())['status']=='INSUFFICIENT_EVIDENCE'
    a=assessment();a['research_fx']['to_currency']='HKD'
    assert foreign_ads_earnings_research(a,assumptions())['status']=='INSUFFICIENT_EVIDENCE'
    a=assessment();a['quality_field_records']=[x for x in a['quality_field_records'] if x['field']!='ads_ratio']
    assert foreign_ads_earnings_research(a,assumptions())['status']=='INSUFFICIENT_EVIDENCE'


def test_treasury_anchor_rejects_wrong_bytes_wrong_source_and_ambiguous_rows():
    import pytest
    from backend.services.market_data.quality_valuation import (
        TREASURY_COLUMN, TREASURY_CURVE, bind_treasury_terminal_anchor)

    cell = (f'<tr><td><time>11/02/2026</time></td><td headers="{TREASURY_COLUMN}">'
            '4.60</td></tr>')
    original = f'<table>{cell}</table>'.encode()
    repeated = f'<table>{cell * 2}</table>'.encode()
    a = assessment()
    day = '2026-11-02'

    def add(body, source=TREASURY_CURVE):
        ref = 'research:treasury:' + hashlib.sha256(body).hexdigest()
        a['records'].append(dict(record_id=ref, quality='valid', kind='research', symbol='PDD:US',
            source='public_primary_research', source_url=source + '?type=daily_treasury_yield_curve',
            period=day, published_at=day, data_as_of=day, fetched_at='2026-11-03T00:00:00+00:00'))
        return ref

    ref = add(original)
    with pytest.raises(ValueError, match='TREASURY_SOURCE_RECEIPT_REQUIRED'):
        bind_treasury_terminal_anchor(a, original=original+b' ', record_id=ref)
    bind_treasury_terminal_anchor(a, original=original, record_id=ref)
    assert Decimal(a['treasury_terminal_anchor']['usd_five_year_cmt']) == Decimal('.046')
    wrong_source = original + b' '
    with pytest.raises(ValueError, match='TREASURY_SOURCE_IDENTITY_INVALID'):
        bind_treasury_terminal_anchor(a, original=wrong_source, record_id=add(wrong_source, 'https://example.com/'))
    a['records'] = [r for r in a['records'] if not r.get('record_id', '').startswith('research:treasury:')]
    with pytest.raises(ValueError, match='TREASURY_OBSERVATION_MISSING_OR_AMBIGUOUS'):
        bind_treasury_terminal_anchor(a, original=repeated, record_id=add(repeated))


def test_country_risk_assumption_requires_known_vintage_and_reconciling_columns():
    import pytest
    from backend.services.market_data.quality_valuation import (
        NYU_COUNTRY_RISK, bind_country_risk_assumption_source)

    source = b'''<p>Last updated: January 5, 2026</p><table>
    <tr><td>Country</td><td>Country Risk Premium</td><td>Equity Risk Premium</td></tr>
    <tr><td>China</td><td>0.91%</td><td>5.14%</td></tr>
    <tr><td>United States</td><td>0.23%</td><td>4.46%</td></tr></table>'''
    a = {'as_of': '2026-11-10T10:00:00+00:00'}
    capture = {'source_url': NYU_COUNTRY_RISK, 'fetched_at': '2026-11-10T09:00:00+00:00'}
    bind_country_risk_assumption_source(a, original=source, **capture)
    risk = a['country_risk_assumption_source']
    assert Decimal(risk['mature_equity_premium']) == Decimal('.0423')
    assert Decimal(risk['china_equity_country_premium']) == Decimal('.0091')
    assert a.keys() == {'as_of', 'country_risk_assumption_source'}
    with pytest.raises(ValueError, match='COUNTRY_RISK_RECONCILIATION_FAILED'):
        bind_country_risk_assumption_source(a, original=source.replace(b'5.14%', b'6.14%'), **capture)
    with pytest.raises(ValueError, match='COUNTRY_RISK_VINTAGE_INELIGIBLE'):
        bind_country_risk_assumption_source(a, original=source.replace(b'2026', b'2025'), **capture)
