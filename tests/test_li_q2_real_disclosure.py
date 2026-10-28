"""Frozen, reduced 2026 Q2 original IR tables; no external IO in these tests."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from research_v2 import official_disclosures as owner
from backend.services.market_data.evidence import assess
from backend.services.market_data.av_fundamentals_service import statement_metrics

FIXTURE = Path(__file__).parent/'fixtures'/'li_official_q2_2026'
META = json.loads((FIXTURE/'manifest.json').read_text())
URL = META['source_url']
FETCHED = META['fetched_at']
AT = datetime.fromisoformat(FETCHED)+timedelta(seconds=1)
EXPECTED = {'totalRevenue':25666890000, 'netIncome':-1705257000,
            'operatingCashflow':15025000, 'capitalExpenditures':1315790000}


def parse(body=None, url=URL):
    return owner.parse_original(body or (FIXTURE/'en.html').read_bytes(),url,FETCHED)


def latest(row):
    return {o['source_field']:o for o in row['financial_observations'] if o['fiscal_period_end']=='2026-06-30'}


@pytest.mark.parametrize('prefix',['','zh-hans/','zh-hant/'])
def test_actual_supported_official_paths_and_navigation(prefix):
    url=URL.replace('/news-releases/','/'+prefix+'news-releases/')
    assert owner.allowed_url(url) and owner.allowed_url(url+'/')
    assert owner.allowed_url('https://ir.lixiang.com/'+prefix+'news/',index=True)
    assert not owner.allowed_url('https://ir.lixiang.com/'+prefix+'news/')


@pytest.mark.parametrize('bad',[
    'https://ir.lixiang.com/news-releases',
    'https://ir.lixiang.com/fr/news-releases/news-release-details/li-auto',
    'https://ir.lixiang.com/zh-hans/arbitrary-page',
    'https://ir.lixiang.com/zh-hans/news-releases/news-release-details/../../secret',
    'https://ir.lixiang.com.evil/news/',
])
def test_path_allowlist_does_not_expand_to_arbitrary_URLs(bad):
    assert not owner.allowed_url(bad,index=True)


@pytest.mark.parametrize('language',['en','zh-hans'])
def test_real_statement_structure_four_fields_and_exact_CNY_not_USD_or_rounded_summary(language):
    body=(FIXTURE/(language+'.html')).read_bytes()
    assert hashlib.sha256(body).hexdigest()==META[language]['sha256']
    url=URL if language=='en' else URL.replace('/news-releases/','/zh-hans/news-releases/')+'/'
    row=parse(body,url);facts=latest(row)
    assert row['published_at']=='2026-08-26'
    assert {key:o['value']*o['unit_scale'] for key,o in facts.items()}==EXPECTED
    assert all(o['currency']=='CNY' and o['unit_scale']==1000 for o in facts.values())
    assert all('source_locator' in o for o in facts.values())
    # Same original repeats net income and OCF; aliases are retained, not rejected.
    assert ';' in facts['netIncome']['source_locator']
    assert ';' in facts['operatingCashflow']['source_locator']
    capex=facts['capitalExpenditures']
    assert capex['reported_value']==-1315790 and capex['reported_unit_scale']==1000
    assert capex['sign_normalization']
    assert EXPECTED['operatingCashflow']-EXPECTED['capitalExpenditures']==-1300765000


def test_real_navigation_discovers_latest_release_without_slug_suffix_or_report_preview(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    calls=[]
    def frozen_fetch(url):
        calls.append(url)
        if url==owner.IR_INDEX:return (FIXTURE/'news.html').read_bytes(),FETCHED
        assert url==URL  # No future-results preview or arbitrary links are read.
        return (FIXTURE/'en.html').read_bytes(),FETCHED
    monkeypatch.setattr(owner,'_fetch',frozen_fetch)
    rows,lookup=owner.collect('LI:US')
    assert calls==[owner.IR_INDEX,URL] and len(rows)==1
    assert latest(rows[0])['netIncome']['value']==-1705257
    assert lookup(rows[0]['record_id'])==rows[0]
    _cache.clear()


def mutate_capex(body, mode):
    soup=BeautifulSoup(body,'html.parser')
    table=next(t for t in soup.find_all('table') if 'Capital expenditures' in t.get_text())
    if mode=='not_cash_flow':
        table.find('td').string='Unknown statement (All amounts in thousands)'
    elif mode=='cumulative':
        node=table.find(string=lambda x:x and 'For the Three Months Ended' in x)
        node.replace_with('For the Six Months Ended')
    elif mode=='units':
        node=table.find(string=lambda x:x and 'All amounts in thousands' in x)
        node.replace_with('Li Auto Inc. Unaudited Condensed Consolidated Statements of Cash Flows (Units unknown)')
    elif mode=='currency':
        for cell in table.find_all(['td','th']):
            if cell.get_text(strip=True)=='RMB':cell.string='US$'
    else:
        row=next(tr for tr in table.find_all('tr') if tr.get_text(' ',strip=True).startswith('Free cash flow'))
        for cell in row.find_all('td'):
            if cell.get_text(strip=True)=='(1,300,765)':cell.string='1,300,765' # Wrong FCF direction.
    return soup.encode()


@pytest.mark.parametrize('mode',['not_cash_flow','cumulative','units','currency','wrong_FCF_sign'])
def test_unclear_outflow_period_currency_or_reconciliation_keeps_capex_UNKNOWN(mode):
    row=parse(mutate_capex((FIXTURE/'en.html').read_bytes(),mode))
    assert row and 'capitalExpenditures' not in latest(row)
    assert latest(row)['totalRevenue']['value']==25666890 # Other fields survive.


def test_real_quarter_enters_original_L1_but_single_release_never_certifies_TTM():
    row=parse();f=latest(row)
    dates=['2026-06-30','2026-03-31','2025-12-31','2025-09-30']
    income={'quarterlyReports':[dict(fiscalDateEnding=d,reportedCurrency='CNY',
        totalRevenue=str(EXPECTED['totalRevenue']),netIncome=str(EXPECTED['netIncome'])) for d in dates]}
    cash={'quarterlyReports':[dict(fiscalDateEnding=d,reportedCurrency='CNY',
        operatingCashflow=str(EXPECTED['operatingCashflow']),capitalExpenditures=str(EXPECTED['capitalExpenditures'])) for d in dates]}
    values,provenance,_=statement_metrics('LI',income,cash,fetched_at=FETCHED)
    raw=dict(symbol='LI:US',source='alphavantage',record_id='frozen-av-contract',fetched_at=FETCHED,field_provenance=provenance,**values)
    result=assess({'fundamentals':raw},symbol='LI:US',research=[row],at=AT,disclosure_verifier={row['record_id']:deepcopy(row)}.get)
    fields={r['field']:r for r in result['verified_field_records']}
    assert set(fields)=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'}
    assert fields['capital_expenditures_quarter']['publication_links'][0]['reported_value']==-1315790
    assert result['qualification']['L1']['allowed']
    assert all(not result['qualification'][l]['allowed'] for l in ('L2','L3','L4'))
    assert raw['field_provenance']['revenue_ttm']['published_at'] is None


def test_capex_cannot_borrow_FCF_from_another_period_block():
    soup=BeautifulSoup((FIXTURE/'en.html').read_bytes(),'html.parser')
    table=next(t for t in soup.find_all('table') if 'Capital expenditures' in t.get_text())
    row=next(tr for tr in table.find_all('tr') if tr.get_text(' ',strip=True).startswith('Free cash flow'))
    heading=soup.new_tag('tr');cell=soup.new_tag('td',colspan='11');cell.string='For the Six Months Ended';heading.append(cell);row.insert_before(heading)
    result=parse(soup.encode())
    assert 'capitalExpenditures' not in latest(result)


def test_untrusted_or_future_publication_in_real_layout_remains_rejected():
    soup=BeautifulSoup((FIXTURE/'en.html').read_bytes(),'html.parser')
    node=soup.select_one('.field--name-field-nir-news-date')
    node.string='2099-08-26'
    assert parse(soup.encode()) is None
    node.decompose()
    assert parse(soup.encode()) is None
