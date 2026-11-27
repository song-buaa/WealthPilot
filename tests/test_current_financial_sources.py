from datetime import datetime, timezone

from bs4 import BeautifulSoup

from backend.services.market_data.av_fundamentals_service import current_income_records
from backend.services.market_data.financial_research import build
from backend.services.market_data.output_policy import _evidence_index
from research_v2 import sec_disclosures as sec
from research_v2.official_disclosures import parse_nvidia_supplement, parse_tesla_summary_eps


AT = datetime(2026, 11, 27, 13, tzinfo=timezone.utc)
FETCHED = '2026-11-27T12:00:00+00:00'
ISSUER = {'issuer_type':'domestic', 'symbol':'XYZ:US', 'sec_ticker':'XYZ', 'cik':'0000000001'}
PERIODS = [('2025-10-01', '2025-12-31'), ('2026-01-01', '2026-03-31'),
           ('2026-04-01', '2026-06-30'), ('2026-07-01', '2026-09-30')]


def capture():
    return {'INCOME_STATEMENT': {
        'payload': {'symbol':'XYZ', 'quarterlyReports': [
            {'fiscalDateEnding':end, 'reportedCurrency':'USD', 'totalRevenue':'123',
             'operatingIncome':'59', 'netIncome':'40', 'reportedEPS':'5.00'} for _,end in PERIODS]},
        'diagnostics': {'status':'available','fetched_at':FETCHED},
    }}


def test_current_vendor_valuation_does_not_enter_pit_or_verified_store():
    per = {end: {'start':start,'refs':['verified-span'],'known_at':FETCHED} for start,end in PERIODS}
    raw = capture()
    records = current_income_records(raw, ISSUER, per, at=AT)
    assert len(records) == 12
    assert {x['field'] for x in records} == {'revenue_quarter', 'operating_income_quarter',
                                           'net_income_attributable_parent_quarter'}
    assert all(r['published_at'] is None and r['known_at']==FETCHED for r in records)
    a = dict(symbol=ISSUER['symbol'], as_of=AT.isoformat(), verified_field_records=[],
             current_financial=dict(latest_period=PERIODS[-1][1],vendor_records=records))
    result = next(x for x in build(a,current=True)['records'] if x['field']=='revenue_ttm')
    assert result['values']['revenue_ttm'] == 492
    assert result['qualification']=='CURRENT_RESEARCH_ONLY'
    assert build(a)['records'] == [] and _evidence_index(a) == {}
    assert result['record_id'] in _evidence_index(a,current=True)
    a['as_of'] = '2026-10-10T10:00:00+00:00'
    assert not build(a,current=True)['records']
    a['as_of'] = '2027-06-01T10:00:00+00:00'
    assert not build(a,current=True)['records']
    raw['INCOME_STATEMENT']['payload']['quarterlyReports'][-1]['reportedCurrency']='None'
    assert len(current_income_records(raw,ISSUER,per,at=AT))==9


def test_current_structured_complete_skips_originals_and_future_earnings(monkeypatch):
    rows = [dict(fetched_at=FETCHED,published_at=FETCHED,
        record_id='sec:'+end,official_disclosure={'accession':end},
        financial_observations=[dict(period_basis='quarterly',currency='USD',fiscal_period_start=start,
            fiscal_period_end=end,source_field=field,accounting_basis=basis,value=1,unit_scale=1)
            for field,basis in (('dilutedEPSOrdinary','diluted_eps_ordinary'),('operatingCashflow','operating_cash_flow'))])
        for start,end in PERIODS]
    filings = [dict(form='10-Q',report_date=end,published_at=FETCHED,accession=end,filed=end)
               for _,end in PERIODS]
    submissions={'filings':{'recent':{'accessionNumber':[]}}}
    current = {'at':AT,'capture':{**capture(),'EARNINGS':{
        'payload':dict(symbol='XYZ',quarterlyEarnings=[dict(fiscalDateEnding='2026-10-31',reportedDate='2026-11-05')]),
        'diagnostics':dict(status='available',fetched_at='2026-11-28T12:00:00+00:00')}}}
    monkeypatch.setattr(sec,'_append_original',lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('unexpected original')))
    sec._collect_current_originals(rows,ISSUER,filings,submissions,'proof',current)
    assert current['result']['missing_periods']==[]
    assert not current['result']['pending_latest_release']
    assert current['result']['original_attempts']==0


def test_tesla_machine_text_uses_gaap_q4_column_and_certified_start():
    header = ('F I N A N C I A L S U M M A R Y (Unaudited) ($ in millions, except percentages and per share data) '
              'Q4-2024 Q1-2025 Q2-2025 Q3-2025 Q4-2025 YoY Total automotive revenues 123 '
              'EPS attributable to common stockholders, diluted (GAAP) (1) 1.01 2.01 3.01 4.01 5.01 -10% '
              'EPS attributable to common stockholders, diluted (non-GAAP) (1) (3) 2.01 3.01 4.01 5.01 6.01 -9% '
              'Net cash provided by operating activities')
    proof={'2025-12-31':dict(start='2025-10-01',prior_ref='q3',annual_ref='annual',proof_available_at=FETCHED)}
    result=parse_tesla_summary_eps(BeautifulSoup(header,'html.parser'),proof)
    assert len(result)==1 and result[0]['value']==5.01
    assert not parse_tesla_summary_eps(BeautifulSoup(header.replace(' Q3-2025',''),'html.parser'),proof)
    assert not parse_tesla_summary_eps(BeautifulSoup(header,'html.parser'),{})


def test_nvda_supplement_does_not_mix_eps_weights_or_nine_month_column():
    html = """
    <p>CONDENSED CONSOLIDATED STATEMENTS OF INCOME</p>
    <p>(In millions, except per share data)</p><p>(Unaudited)</p>
    <table>
    <tr><td></td><td colspan="2">Three Months Ended</td><td colspan="2">Nine Months Ended</td></tr>
    <tr><td></td><td colspan="2">October 25,</td><td colspan="2">October 25,</td></tr>
    <tr><td></td><td colspan="2">2026</td><td colspan="2">2026</td></tr>
    <tr><td>Revenue</td><td>$</td><td>111</td><td>$</td><td>300</td></tr>
    <tr><td>Net income per share:</td></tr>
    <tr><td>Diluted</td><td>$</td><td>2.71</td><td>$</td><td>9.11</td></tr>
    <tr><td>Weighted average shares used in per share computation:</td></tr>
    <tr><td>Diluted</td><td></td><td>10</td><td></td><td>12</td></tr>
    </table>
    """
    proof = {('2026-10-25','quarterly'): {'start':'2026-07-27','ref':'prior-period','at':FETCHED}}
    result = parse_nvidia_supplement(BeautifulSoup(html,'html.parser'),proof)
    fields = {row['source_field']:row for row in result}
    assert fields['dilutedEPSOrdinary']['value'] == 2.71
    assert fields['quality_diluted_shares']['value'] == 10
    assert fields['quality_diluted_shares']['unit'] == 'shares'
    assert all(row['period_basis']=='quarterly' for row in result)
    assert not parse_nvidia_supplement(BeautifulSoup(html,'html.parser'),{})


def test_current_same_basis_official_fact_wins_and_preserves_disagreement():
    per = {end: {'start':start,'refs':['verified-span'],'known_at':FETCHED} for start,end in PERIODS}
    vendor = current_income_records(capture(),ISSUER,per,at=AT)
    official = dict(next(r for r in vendor if r['period']==PERIODS[-1][1] and r['field']=='revenue_quarter'),
        record_id='official-fact', source='official_disclosure', values={'revenue_quarter':125})
    a = dict(symbol=ISSUER['symbol'], as_of=AT.isoformat(), verified_field_records=[official],
             current_financial=dict(latest_period=PERIODS[-1][1],vendor_records=vendor))
    result = build(a,current=True)
    ttm = next(r for r in result['records'] if r['field']=='revenue_ttm')
    assert ttm['values']['revenue_ttm'] == 494 and 'official-fact' in ttm['evidence_refs']
    assert 'AV_OFFICIAL_FIELD_DISAGREEMENT:revenue_quarter:2026-09-30' in result['limitations']


def test_current_operating_bridge_consumes_exact_current_quarters_without_touching_pit():
    from copy import deepcopy
    from decimal import Decimal
    import pytest
    from backend.services.market_data.financial_research import build_quality, operating_ttm_bridge
    from tests.test_s3_p2_operating_model import sources

    a = sources()
    a['quality_field_records'] = [r for r in a['quality_field_records']
        if not (r['field'] == 'net_income' and r['period'] == '2026-06-30')]
    baseline_quality = build_quality(a)
    baseline_eps = build(a)['records']
    windows = [('2025-07-01','2025-09-30'), ('2025-10-01','2025-12-31'),
               ('2026-01-01','2026-03-31'), ('2026-04-01','2026-06-30')]
    issuer = {**ISSUER, 'symbol':a['symbol'], 'sec_ticker':'AAA'}
    observed = '2026-10-01T00:00:00+00:00'
    statements = {'INCOME_STATEMENT': {
        'payload': {'symbol':'AAA', 'quarterlyReports': [
            {'fiscalDateEnding':end, 'reportedCurrency':'USD', 'netIncome':str(40+i)}
            for i, (_,end) in enumerate(windows)]},
        'diagnostics': {'status':'available','fetched_at':observed}}}
    records = current_income_records(statements, issuer,
        {end:dict(start=start, refs=['exact-fiscal-source'], known_at=observed) for start,end in windows},
        at=datetime.fromisoformat(a['as_of']))
    assert len(records) == 4
    a['current_financial'] = dict(latest_period=windows[-1][1],vendor_records=records)

    bridge = operating_ttm_bridge(a,current=True)
    net = bridge['flows']['net_income']
    assert Decimal(net['value']) == Decimal(166)
    assert bridge['qualification'] == net['qualification'] == 'CURRENT_RESEARCH_ONLY'
    assert [r['source_locator'] for r in net['components']] == [r['source_locator'] for r in records]
    assert all(r['known_at'] == observed for r in net['components'])
    assert build(a)['records'] == baseline_eps and build_quality(a) == baseline_quality
    with pytest.raises(ValueError, match='OPERATING_BRIDGE_PERIOD_OR_INPUT_MISSING'):
        operating_ttm_bridge(a)

    for mutation in (dict(currency='CNY'), dict(known_at='2027-06-01T00:00:00Z'),
                     dict(fiscal_period_start='2026-04-02'), dict(accounting_basis='consolidated_net_income')):
        invalid = deepcopy(a)
        invalid['current_financial']['vendor_records'][-1].update(mutation)
        assert 'net_income' in operating_ttm_bridge(invalid,current=True)['missing_fields']
    duplicate = deepcopy(a)
    duplicate['current_financial']['vendor_records'].append(
        dict(records[-1], record_id='conflict', values={records[-1]['field']:99}))
    assert 'net_income' in operating_ttm_bridge(duplicate,current=True)['missing_fields']


def test_nvidia_dividend_retains_announcement_and_scheduled_dates_only():
    from research_v2.official_disclosures import parse_nvidia_dividend_declaration

    phrase = ('NVIDIA will pay its next quarterly cash dividend of $0.25 per share on December 23, 2026, '
              'to all shareholders of record on December 2, 2026.')
    parse = lambda markup: parse_nvidia_dividend_declaration(BeautifulSoup(markup,'html.parser'),'2026-11-17')
    event = parse(phrase)
    assert event['status'] == 'DECLARED'
    assert event['amount_per_share'] == '0.25' and event['currency'] == 'USD'
    assert event['announced_on'] == '2026-11-17'
    assert event['record_date'] == '2026-12-02' and event['scheduled_payment_date'] == '2026-12-23'
    assert event['ex_date'] is event['actual_paid_on'] is None
    assert parse(phrase.replace('will pay','paid')) is None
    assert parse(phrase.replace('December 2,','December 24,')) is None
    assert parse(phrase + phrase) is None
