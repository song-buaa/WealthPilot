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
