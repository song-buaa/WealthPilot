"""PR-A: exact public LI/PDD originals, no valuation forecasts or account access."""
from copy import deepcopy
from datetime import timedelta
import pytest
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build_valuation_inputs, display
from tests.test_sec_real_original import parse, AT


def li():
    source=parse()
    return assess({},symbol='LI:US',research=[source],at=AT,
        disclosure_verifier={source['record_id']:source}.get)


def test_real_li_balance_ads_and_cash_only_bridge_are_independently_qualified():
    a=li();before=deepcopy(a);r=build_valuation_inputs(a)
    assert r['cash_less_disclosed_borrowings']['value']=='32967578000.0'
    assert r['cash_less_disclosed_borrowings']['currency']=='CNY'
    assert r['cash_less_disclosed_borrowings']['period']=='2026-06-30'
    assert 'NOT_DISTRIBUTABLE_CASH' in r['cash_less_disclosed_borrowings']['basis']
    assert r['net_cash'] is None and r['cash_less_disclosed_borrowings']['usable_for_equity_bridge'] is False
    assert r['listed_unit']['ordinary_shares_per_ads']==2
    assert r['listed_unit']['as_of']=='2026-08-26'  # ratio known at filing, not backdated to past quarters
    shares={x['field']:x for x in r['weighted_share_observations'] if x['period']=='2026-06-30'}
    assert shares['diluted_shares']['values']['diluted_shares']==2014197771
    assert shares['diluted_ads_shares']['values']['diluted_ads_shares']==1007098886
    assert r['market_cap'] is None
    assert 'POINT_IN_TIME_ECONOMIC_SHARES_UNVERIFIED' in r['issues']
    assert all(x['publication_links'][0]['document_filename']=='tm2624080d1_ex99-1.htm' for x in a['quality_field_records'])
    assert a==before and not a['qualification']['L3']['allowed']


def test_real_pdd_reuses_the_same_share_parser_and_qualification():
    from tests.test_p1_1_research_quality import official_rows, AT as PDD_AT
    sources=official_rows();proof={s['record_id']:s for s in sources}
    a=assess({},symbol='PDD:US',research=sources,at=PDD_AT,disclosure_verifier=proof.get)
    r=build_valuation_inputs(a)
    assert r['listed_unit']['ordinary_shares_per_ads']==4
    assert r['weighted_share_observations']
    assert not any('Liabilities and Shareholders' in x.get('source_locator','')
                   for x in a['quality_field_records'] if x['field']=='equity')
    assert r['balance'].get('equity') is None  # combined liabilities + equity is not parent equity
    assert all(x['values']['diluted_shares']>1e9 for x in r['weighted_share_observations'])
    assert r['market_cap'] is None


@pytest.mark.parametrize('missing',['cash','current_debt','noncurrent_debt','total_equity'])
def test_missing_balance_component_is_not_zero(missing):
    a=li();a['quality_field_records']=[r for r in a['quality_field_records'] if r['field']!=missing]
    assert build_valuation_inputs(a)['cash_less_disclosed_borrowings'] is None


def test_conflicting_or_stale_balance_fails_but_ratio_keeps_its_own_proof():
    a=li();row=deepcopy(next(r for r in a['quality_field_records'] if r['field']=='cash' and r['period']=='2026-06-30'))
    row['record_id']+=':conflict';row['values']['cash']+=1;a['quality_field_records'].append(row)
    assert build_valuation_inputs(a)['cash_less_disclosed_borrowings'] is None
    assert build_valuation_inputs(a)['listed_unit']['ordinary_shares_per_ads']==2
    a=li();a['as_of']=(AT+timedelta(days=200)).isoformat()
    assert build_valuation_inputs(a)['cash_less_disclosed_borrowings'] is None


def test_self_reported_source_and_non_gaap_table_do_not_gain_authority():
    from research_v2.official_disclosures import parse_valuation_statement_inputs
    from bs4 import BeautifulSoup
    s=parse();forged=deepcopy(s);forged['financial_observations'][0]['value']+=1
    a=assess({},symbol='LI:US',research=[forged],at=AT,disclosure_verifier={s['record_id']:s}.get)
    assert not a['quality_field_records']
    assert not parse_valuation_statement_inputs(BeautifulSoup('<h1>Reconciliation of non-GAAP results</h1><table><tr><td>Cash and cash equivalents</td><td>100</td></tr></table>','html.parser'),'2026-08-26')


def test_display_units_change_presentation_not_values():
    for field,value,unit,currency,expected in [
        ('vehicle_deliveries_quarter',98330,'vehicles',None,'98,330辆'),
        ('revenue_quarter',25666890000,'currency_units','CNY','256.67亿元'),
        ('vehicle_margin_pct',9.4,'percent',None,'9.40%')]:
        row=dict(values={field:value},unit=unit,currency=currency,period='2026-06-30');before=deepcopy(row)
        text=display(row,field)
        assert expected in text and '无币种比率' not in text and row==before


def test_delivered_active_report_does_not_use_incomplete_header(active):
    from types import SimpleNamespace as NS
    from tests.test_policy_integration import session
    from backend.services.investment_policy.runtime import bind
    from backend.services.investment_policy.consumers import finalize_output
    from backend.agents.contracts import ExpressionOutput
    a=li();metric=next(r for r in a['verified_field_records'] if r['field']=='revenue_quarter')
    candidate=dict(topic='business',basis='inference',scope='research_only',
        text='收入证据支持经营研究，但盈利修复的持续性仍需核验。',evidence_refs=[metric['record_id']])
    out=ExpressionOutput(structured_payload={'professional_explanation':[candidate]})
    with bind(session()):
        finalize_output(NS(portfolio_id=7),NS(position_evidence=a,loaded_data=None),out)
        assert out.structured_payload['professional_explanation']
        assert '专业推断未完成' not in out.chat_answer
        assert '不构成交易授权' in out.chat_answer
        out.structured_payload={'professional_explanation':[]}
        finalize_output(NS(portfolio_id=7),NS(position_evidence=a,loaded_data=None),out)
        assert '专业推断未完成' in out.chat_answer


from tests.test_policy_integration import active  # reuse isolated policy fixture, never production


def test_historical_or_negated_ads_mention_never_proves_current_ratio():
    from tests.test_sec_real_original import original
    body=original()
    assert b'Each ADS represents two Class A ordinary shares' in body
    for prefix in [b'Historically, before the depositary ratio changed, ',b'It is not true that ']:
        changed=body.replace(b'Each ADS represents two Class A ordinary shares',prefix+b'each ADS represents two Class A ordinary shares')
        source=parse(changed)
        a=assess({},symbol='LI:US',research=[source],at=AT,disclosure_verifier={source['record_id']:source}.get)
        assert not build_valuation_inputs(a)['listed_unit']
        assert build_valuation_inputs(a)['cash_less_disclosed_borrowings']  # independent balance facts survive


def test_quality_financial_facts_cannot_certify_unconditional_competitive_advantage():
    from backend.services.market_data.output_policy import explanation_projection
    a=li();ref=next(r['record_id'] for r in a['quality_field_records'] if r['field']=='cash')
    candidate=dict(topic='competition',basis='inference',scope='research_only',
        text='公司现金储备证明长期竞争优势已经确立，行业领先地位将持续增强。',evidence_refs=[ref])
    assert not explanation_projection([candidate],a)[0]
    candidate['text']='若现金投入能够形成持续的产品回报，竞争优势假设才可能获得支持。'
    assert explanation_projection([candidate],a)[0]


def test_old_balance_is_history_and_partial_bridge_never_becomes_net_cash():
    a=li()
    old=[r for r in a['quality_field_records'] if r['field']=='cash' and r['period']=='2025-12-31']
    assert old and all(r['historical_only'] for r in old)
    assert all('历史基线' in display(r,'cash') for r in old)
    r=build_valuation_inputs(a)
    assert r['net_cash'] is None
    assert r['cash_less_disclosed_borrowings']['debt_coverage']=='PARTIAL_DISCLOSED_BORROWINGS'
    # Unknown convertible/other financing debt is never asserted to be zero.
    assert r['cash_less_disclosed_borrowings']['usable_for_equity_bridge'] is False


def test_old_reporting_currency_does_not_authorize_a_new_mixed_currency_period():
    a=li()
    latest=next(r for r in a['quality_field_records'] if r['field']=='assets' and r['period']=='2026-06-30')
    latest.pop('currency_role',None)
    translated=deepcopy(latest);translated['currency']='USD';translated['record_id']+=':translated'
    a['quality_field_records'].append(translated)
    r=build_valuation_inputs(a)
    assert r['net_cash'] is None and r['cash_less_disclosed_borrowings'] is None
    assert 'BALANCE_CURRENCY_MISSING_OR_AMBIGUOUS' in r['issues']


def test_final_fact_recheck_keeps_complete_quality_owner_context():
    from backend.services.market_data.output_policy import captured_facts
    a,_=captured_facts(li(),at=AT)
    r=build_valuation_inputs(a)
    assert r['listed_unit']['ordinary_shares_per_ads']==2
    assert r['cash_less_disclosed_borrowings'] and r['net_cash'] is None


def test_real_companyfacts_instant_shares_replay_with_original_fetch_clocks():
    import gzip,hashlib,json
    from pathlib import Path
    from datetime import datetime
    from backend.services.instruments.issuers import resolve_sec_issuer
    from research_v2 import sec_disclosures as sec
    root=Path(__file__).parent/'fixtures/valuation_inputs_sec'
    meta={r['name']:r for r in json.loads((root/'manifest.json').read_text())}
    def read(name):
        body=gzip.decompress((root/(name+'.json.gz')).read_bytes())
        assert hashlib.sha256(body).hexdigest()==meta[name]['sha256']
        return body,meta[name]['fetched_at']
    directory=json.loads(read('directory')[0])
    at=max(datetime.fromisoformat(r['fetched_at']) for r in meta.values())
    for ticker in ['LI','PDD']:
        issuer=resolve_sec_issuer(ticker+':US',directory,json.loads(read(ticker+'-submissions')[0]))
        filings=sec.discover_filings(issuer,json.loads(read(ticker+'-submissions')[0]))
        sources=sec.parse_companyfacts(read(ticker+'-companyfacts'),issuer,filings,
            identity_digest=meta['directory']['sha256'],quality='all')
        proof={s['record_id']:s for s in sources}
        a=assess({},symbol=ticker+':US',research=sources,at=at,disclosure_verifier=proof.get)
        r=build_valuation_inputs(a)
        assert r['reported_point_share_observations']
        assert all(x['scope']=='instant' and x['unit']=='shares' for x in r['reported_point_share_observations'])
        assert r['market_cap'] is None and r['net_cash'] is None
        # CompanyFacts USD convenience currency + CNY without original column
        # role is not a complete current reporting-currency or equity bridge.
        assert 'BALANCE_CURRENCY_MISSING_OR_AMBIGUOUS' in r['issues']
        assert all(x['fetched_at']==meta[ticker+'-companyfacts']['fetched_at'] for x in a['quality_field_records'])


def test_fresh_sec_originals_reuse_existing_bytes_with_actual_new_fetch_receipts():
    import gzip,hashlib,json
    from pathlib import Path
    from datetime import datetime
    from research_v2 import sec_disclosures as sec
    root=Path(__file__).parent/'fixtures/valuation_inputs_sec'
    manifest={r['name']:r for r in json.loads((root/'manifest.json').read_text())}
    for receipt in json.loads((root/'original-receipts.json').read_text()):
        body=gzip.decompress((root/receipt['fixture_file']).read_bytes())
        assert hashlib.sha256(body).hexdigest()==receipt['sha256']
        source=sec.parse_filing((body,receipt['fetched_at']),receipt['issuer'],receipt['filing'],
            identity_digest=manifest['directory']['sha256'])
        a=assess({},symbol=receipt['issuer']['symbol'],research=[source],
            at=datetime.fromisoformat(receipt['fetched_at']),disclosure_verifier={source['record_id']:source}.get)
        r=build_valuation_inputs(a)
        assert r['listed_unit']['ordinary_shares_per_ads']==(2 if receipt['symbol']=='LI' else 4)
        assert r['weighted_share_observations']
        assert all(x['fetched_at']==receipt['fetched_at'] for x in a['quality_field_records'])
        assert r['net_cash'] is None and r['market_cap'] is None
        if receipt['symbol']=='LI':assert r['cash_less_disclosed_borrowings']['value']=='32967578000.0'
        # Production-shaped source combination: preserve the exact original
        # column role alongside the same issuer's CompanyFacts convenience USD.
        ticker=receipt['symbol']
        sub=json.loads(gzip.decompress((root/(ticker+'-submissions.json.gz')).read_bytes()))
        cf_body=gzip.decompress((root/(ticker+'-companyfacts.json.gz')).read_bytes())
        cf=sec.parse_companyfacts((cf_body,manifest[ticker+'-companyfacts']['fetched_at']),receipt['issuer'],
            sec.discover_filings(receipt['issuer'],sub),identity_digest=manifest['directory']['sha256'],quality='all')
        rows=cf+[source];proof={x['record_id']:x for x in rows}
        combined=assess({},symbol=receipt['issuer']['symbol'],research=rows,
            at=datetime.fromisoformat(receipt['fetched_at']),disclosure_verifier=proof.get)
        merged=build_valuation_inputs(combined)
        assert merged['currency']=='CNY' and merged['listed_unit']==r['listed_unit']
        assert merged['cash_less_disclosed_borrowings']==r['cash_less_disclosed_borrowings']
        assert merged['net_cash'] is None and merged['market_cap'] is None


def test_tiny_per_share_values_keep_their_currency_and_share_unit():
    row=dict(values={'eps':0.001},unit='currency_per_ordinary_share',currency='CNY',period='2026-06-30')
    text=display(row,'eps')
    assert '0.001000' in text and 'CNY/普通股' in text


@pytest.mark.parametrize('label', ["Total noncontrolling shareholders’ equity", "Total preferred shareholders’ equity"])
def test_equity_subclass_label_never_becomes_parent_equity(label):
    from tests.test_p0_quality_semantics import pdd_original,pdd_record
    body=pdd_original()
    original=b'Total Liabilities and Shareholders&' + b'rsquo;\n    Equity'
    assert original in body
    changed=body.replace(original,label.replace('’','&rsquo;').encode())
    source=pdd_record(changed)
    assert source
    assert not any(o['source_field']=='quality_equity' for o in source['financial_observations'])
