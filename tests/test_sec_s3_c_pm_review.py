"""Synthetic boundary cases. Real SEC source qualification is frozen externally."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest

from research_v2 import sec_disclosures as sec
from backend.services.instruments.issuers import resolve_sec_issuer
from backend.services.market_data.evidence import assess
from tests.test_sec_financial_sources import submissions, identity

FETCHED='2026-09-01T12:00:00+00:00'


def fixture(ticker='CCC'):
    directory={str(i):dict(cik_str=12345,ticker=t,title='Synthetic class issuer') for i,t in enumerate(('AAA','CCC','PRF'))}
    sub=dict(cik=12345,name='Synthetic class issuer',tickers=['AAA','CCC','PRF'],filings=dict(recent=dict(
        accessionNumber=['0000012345-26-000001'],form=['10-Q'],filingDate=['2026-08-26'],
        reportDate=['2026-06-30'],primaryDocument=['report.htm'],acceptanceDateTime=['2026-08-25T22:00:00Z'])))
    issuer=resolve_sec_issuer(ticker+':US',directory,sub);filing=sec.discover_filings(issuer,sub)[0]
    def context(key,member,start='2026-04-01',extra=''):
        return f'''<xbrli:context id="{key}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000012345</xbrli:identifier><xbrli:segment>
<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">{member}</xbrldi:explicitMember>{extra}</xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>'''
    html='''<html xmlns:us-gaap="http://fasb.org/us-gaap/2026" xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:acme="http://acme.test/2026" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>'''
    for key,member,title,t in [('ca','us-gaap:CommonClassAMember','Class A Common Stock, par value 1','AAA'),
            ('cc','acme:CapitalClassCMember','Class C Capital Stock, par value 1','CCC'),
            ('cp','acme:SeriesPreferredMember','Depositary Shares, Preferred Stock','PRF')]:
        html+=context(key,member)
        html+=f'<ix:nonNumeric name="dei:TradingSymbol" contextRef="{key}">{t}</ix:nonNumeric><ix:nonNumeric name="dei:Security12bTitle" contextRef="{key}">{title}</ix:nonNumeric>'
    html+=context('qa','us-gaap:CommonClassAMember')+context('qc','us-gaap:CommonClassCMember')
    html+='''<xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="qa" unitRef="eps" decimals="2">7</ix:nonFraction>
<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="qc" unitRef="eps" decimals="2">5</ix:nonFraction></body></html>'''
    body=f'''<SEC-HEADER><ACCEPTANCE-DATETIME>20260825180000
ACCESSION NUMBER: {filing['accession']}
CONFORMED SUBMISSION TYPE: 10-Q
CENTRAL INDEX KEY: 0000012345
FILED AS OF DATE: 20260826
</SEC-HEADER><DOCUMENT><TYPE>10-Q
<SEQUENCE>1
<FILENAME>report.htm
<TEXT>{html}</TEXT></DOCUMENT>'''.encode()
    return issuer,filing,body


def parsed(ticker='CCC',change=None):
    issuer,filing,body=fixture(ticker)
    if change:body=change(body)
    record=sec.parse_filing((body,FETCHED),issuer,filing,identity_digest='a'*64)
    return record,[o for o in (record or {}).get('financial_observations',[]) if o['source_field']=='dilutedEPSOrdinary']


def test_direct_classes_keep_distinct_values_and_refs_through_original_evidence():
    a,aeps=parsed('AAA');c,ceps=parsed('CCC')
    assert [r['value'] for r in aeps]==[7] and [r['value'] for r in ceps]==[5]
    assert a['record_id']!=c['record_id'] and ':listed:CCC' in c['record_id']
    assert ceps[0]['listed_class_proof']['basis']=='DIRECT_OFFICIAL_CLASS_EPS_NOT_CONVERSION'
    assessment=assess({},symbol='CCC:US',research=[c],at=datetime.fromisoformat(FETCHED),disclosure_verifier={c['record_id']:c}.get)
    eps=[r for r in assessment['verified_field_records'] if r['field']=='diluted_eps_ordinary_quarter']
    assert len(eps)==1 and eps[0]['values']['diluted_eps_ordinary_quarter']==5


def test_depositary_preferred_cannot_receive_common_eps():
    record,eps=parsed('PRF');assert not eps


@pytest.mark.parametrize('change',[
    lambda b:b.replace(b'http://fasb.org/us-gaap/2026',b'https://untrusted.test/us-gaap/2026'),
    lambda b:b.replace(b'http://xbrl.sec.gov/dei/2026',b'https://untrusted.test/dei/2026'),
    lambda b:b.replace(b'http://www.xbrl.org/2013/inlineXBRL',b'https://untrusted.test/inlineXBRL'),
    lambda b:b.replace(b'http://xbrl.org/2006/xbrldi',b'https://untrusted.test/dimensions'),
    lambda b:b.replace(b'0000012345</xbrli:identifier>',b'0000099999</xbrli:identifier>'),
    lambda b:b.replace(b'contextRef="qc"',b'contextRef="qa"'),
    lambda b:b.replace(b'Class C Capital Stock',b'Class A Capital Stock'),
    lambda b:b.replace(b'xbrli:shares</xbrli:measure>',b'iso4217:USD</xbrli:measure>'),
    lambda b:b.replace(b'2026-04-01</xbrli:startDate>',b'2026-09-30</xbrli:startDate>'),
    lambda b:b.replace(b'20260825180000',b'20260825180001'),
    lambda b:b.replace(b'FILED AS OF DATE: 20260826',b'FILED AS OF DATE: 20260827'),
    lambda b:b.replace(b'us-gaap:StatementClassOfStockAxis',b'acme:DivisionAxis'),
    lambda b:b.replace(b'</xbrli:segment>',b'<xbrldi:explicitMember dimension="acme:DivisionAxis">acme:RetailMember</xbrldi:explicitMember></xbrli:segment>'),
])
def test_invalid_identity_class_unit_period_namespace_or_clock_is_not_eps(change):
    _,eps=parsed(change=change);assert not eps


def test_flags_are_not_proofs_and_unsegmented_eps_is_not_a_class_bridge():
    issuer,filing,body=fixture()
    # No verified formal body: a caller-provided qualification label is ignored.
    candidate=dict(source_field='dilutedEPSOrdinary',value=99,listed_class_proof={'basis':'DIRECT_OFFICIAL_CLASS_EPS_NOT_CONVERSION'})
    assert sec._record(issuer,filing,[candidate],b'{}',filing['full_url'],FETCHED,identity_digest='a'*64) is None
    # Remove the only matching class EPS fact. Entity-wide/unsegmented fallback
    # must not be borrowed from a different class even if the numbers match.
    body=body.replace(b'contextRef="qc" unitRef="eps"',b'contextRef="missing" unitRef="eps"')
    row=sec.parse_filing((body,FETCHED),issuer,filing,identity_digest='a'*64)
    assert not row or not any(o['source_field']=='dilutedEPSOrdinary' for o in row['financial_observations'])


def test_late_header_has_two_independent_clocks_and_pit_bound():
    issuer,filing,body=fixture()
    assert filing['accepted_at']=='2026-08-25T22:00:00+00:00'
    assert filing['published_at']=='2026-08-26T23:59:59.999999-04:00'
    assert sec.verify_filing_envelope((body,FETCHED),issuer,filing)
    assert sec.parse_filing((body,'2026-08-26T18:00:00+00:00'),issuer,filing,identity_digest='a'*64) is None
    row=sec.parse_filing((body,FETCHED),issuer,filing,identity_digest='a'*64)
    earlier=datetime(2026,8,27,tzinfo=timezone.utc)
    a=assess({},symbol='CCC:US',research=[row],at=earlier,disclosure_verifier={row['record_id']:row}.get)
    assert not a['verified_field_records'] and not a['historical_field_records']
    changed=deepcopy(filing);changed['accepted_at']='2026-08-26T01:00:00+03:00'
    assert sec.verify_filing_envelope((body,FETCHED),issuer,changed) is None


@pytest.mark.parametrize('filed,accepted',[('2026-08-29','2026-08-29T15:00:00Z'),
    ('2026-09-07','2026-09-07T15:00:00Z'),('2026-08-26','2026-08-26T11:57:57+03:00'),
    ('2026-08-26','2026-08-26T11:57:57'),('2026-08-26','2026-08-26T11:57:57-00:00')])
def test_nonbusiness_or_non_utc_submissions_are_rejected(filed,accepted):
    sub=submissions();sub['filings']['recent']['filingDate']=[filed];sub['filings']['recent']['acceptanceDateTime']=[accepted]
    assert not sec.discover_filings(identity()[0],sub)


def test_missing_acceptance_is_never_midnight_and_normal_same_day_is_unchanged():
    issuer,_=identity();sub=submissions()
    ordinary=sec.discover_filings(issuer,sub)[0]
    assert ordinary['published_at']=='2026-08-26T11:57:57+00:00'
    sub['filings']['recent'].pop('acceptanceDateTime')
    unknown=sec.discover_filings(issuer,sub)[0]
    assert unknown['accepted_at'] is None and unknown['published_at']=='2026-08-26T23:59:59.999999-04:00'
    sub['filings']['recent']['filingDate']=['2026-08-29']
    assert not sec.discover_filings(issuer,sub)


def test_federal_holiday_rollover_uses_official_next_business_day():
    sub=submissions();sub['filings']['recent']['filingDate']=['2026-09-08']
    sub['filings']['recent']['acceptanceDateTime']=['2026-09-04T22:00:00Z']
    rows=sec.discover_filings(identity()[0],sub)
    assert len(rows)==1 and rows[0]['published_at']=='2026-09-08T23:59:59.999999-04:00'
    # A non-next official day is not inferred to be a temporary closure.
    sub['filings']['recent']['filingDate']=['2026-09-09']
    assert not sec.discover_filings(identity()[0],sub)


def test_owner_rederives_value_even_when_caller_passes_a_forged_proof():
    issuer,filing,body=fixture()
    bogus=dict(source_field='dilutedEPSOrdinary',value=99,listed_class_proof={'ticker':'CCC','basis':'DIRECT_OFFICIAL_CLASS_EPS_NOT_CONVERSION'})
    row=sec._record(issuer,filing,[bogus],body,filing['full_url'],FETCHED,identity_digest='a'*64)
    assert [o['value'] for o in row['financial_observations'] if o['source_field']=='dilutedEPSOrdinary']==[5]


@pytest.mark.parametrize('change',[
    lambda b:b.replace(b'<xbrli:divide>',b'<evil:divide xmlns:evil="http://evil.test">').replace(b'</xbrli:divide>',b'</evil:divide>'),
    lambda b:b.replace(b'<xbrli:identifier',b'<evil:identifier xmlns:evil="http://evil.test"').replace(b'</xbrli:identifier>',b'</evil:identifier>'),
    lambda b:b.replace(b'<xbrli:startDate>',b'<evil:startDate xmlns:evil="http://evil.test">').replace(b'</xbrli:startDate>',b'</evil:startDate>'),
    lambda b:b.replace(b'<xbrli:unitNumerator>',b'<evil:unitNumerator xmlns:evil="http://evil.test">').replace(b'</xbrli:unitNumerator>',b'</evil:unitNumerator>'),
    lambda b:b.replace(b'<xbrli:measure>iso4217:USD',b'<evil:measure xmlns:evil="http://evil.test">iso4217:USD').replace(b'iso4217:USD</xbrli:measure>',b'iso4217:USD</evil:measure>'),
    lambda b:b.replace(b'Class C Capital Stock, par value 1',b'Class C Capital Stock, Preferred Stock'),
    lambda b:b.replace(b'id="cc"',b'id="ca"'),
    lambda b:b.replace(b'<xbrli:unit id="eps">',b'<xbrli:unit id="eps"><xbrli:measure>iso4217:USD</xbrli:measure>'),
])
def test_class_proof_rejects_spoofed_xbrl_structure_and_security_titles(change):
    _,eps=parsed(change=change);assert not eps


def test_cover_context_cannot_bind_a_future_security_identity():
    def change(body):
        start=body.index(b'<xbrli:context id="cc"');end=body.index(b'</xbrli:context>',start)
        return body[:start]+body[start:end].replace(b'2026-06-30',b'2099-06-30')+body[end:]
    _,eps=parsed(change=change);assert not eps


@pytest.mark.parametrize('change',[
    lambda b:b.replace(b'contextRef="qc" unitRef="eps"',b'contextRef="qc" unitRef="eps" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="1"'),
    lambda b:b.replace(b'contextRef="qc" unitRef="eps"',b'contextRef="qc" unitRef="eps" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:nil="true"'),
    lambda b:b.replace(b'contextRef="qc" unitRef="eps"',b'contextRef="qc" unitRef="eps" xmlns:xsi="http://evil.test" xsi:nil="false"'),
    lambda b:b.replace(b'contextRef="qc" unitRef="eps"',b'contextRef="qc" unitRef="eps" xmlns:ixt="http://evil.test" format="ixt:num-dot-decimal"'),
    lambda b:b.replace(b'>5</ix:nonFraction>',b'>5,000</ix:nonFraction>'),
    lambda b:b.replace(b'>5</ix:nonFraction>',b'>5 000</ix:nonFraction>'),
])
def test_class_eps_requires_non_nil_untransformed_decimal_literal(change):
    _,eps=parsed(change=change);assert not eps
