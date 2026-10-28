"""No sockets: all operator identities are synthetic test values, never defaults."""
import json
import socket
import ssl
import urllib.error
from unittest.mock import Mock
import pytest
from research_v2 import sec_disclosures as sec


@pytest.fixture(autouse=True)
def isolated_diagnostics():
    token=sec._diagnostics.set(())
    yield
    sec._diagnostics.reset(token)


def identity(monkeypatch):
    monkeypatch.setenv('WEALTHPILOT_SEC_ORGANIZATION','Synthetic Offline Operator')
    monkeypatch.setenv('WEALTHPILOT_SEC_CONTACT_EMAIL','offline-fixture@operator.org')


@pytest.mark.parametrize('org,email',[('',''),('Org',''),('Org','owner@example.com'),('Org','owner@x.invalid'),
    ('Org\r\nInjected: x','owner@operator.org'),('Org','owner@operator.org\r\nX: y'),('Sample','owner@operator.org')])
def test_missing_or_invalid_identity_never_sends(monkeypatch,org,email):
    monkeypatch.setenv('WEALTHPILOT_SEC_ORGANIZATION',org);monkeypatch.setenv('WEALTHPILOT_SEC_CONTACT_EMAIL',email)
    opener=Mock();monkeypatch.setattr(sec.urllib.request,'build_opener',opener)
    assert sec._fetch(sec.DIRECTORY) is None
    opener.assert_not_called()
    assert sec.source_diagnostics()[-1]['category']=='identity_unconfigured'


def test_operator_config_is_read_each_request_and_never_logged(monkeypatch,caplog):
    identity(monkeypatch);response=Mock();response.geturl.return_value=sec.DIRECTORY;response.read.return_value=b'{}';response.status=200
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(sec.urllib.request,'build_opener',lambda *a:opener)
    with caplog.at_level('INFO'):assert sec._fetch(sec.DIRECTORY)[0]==b'{}'
    request=opener.open.call_args.args[0]
    assert request.get_header('User-agent')=='Synthetic Offline Operator offline-fixture@operator.org'
    assert opener.open.call_args.kwargs['timeout']==8
    assert 'offline-fixture' not in caplog.text and 'Operator' not in caplog.text
    monkeypatch.delenv('WEALTHPILOT_SEC_CONTACT_EMAIL');assert sec._fetch(sec.DIRECTORY) is None
    assert opener.open.call_count==1


@pytest.mark.parametrize('error,category',[
    (urllib.error.HTTPError(sec.DIRECTORY,403,'PRIVATE_RESPONSE',{},None),'http_403'),
    (urllib.error.HTTPError(sec.DIRECTORY,429,'PRIVATE_RESPONSE',{},None),'http_error'),
    (urllib.error.URLError(ssl.SSLCertVerificationError('PRIVATE_CERT')),'tls_error'),
    (urllib.error.URLError(socket.gaierror('PRIVATE_DNS')),'dns_error'),
    (urllib.error.URLError(TimeoutError('PRIVATE_TIMEOUT')),'timeout'),
    (urllib.error.URLError(ConnectionRefusedError('PRIVATE_PROXY')),'proxy_connection_error')])
def test_classified_failure_has_one_attempt_no_sensitive_details(monkeypatch,caplog,error,category):
    identity(monkeypatch);monkeypatch.setenv('HTTPS_PROXY','http://PRIVATE_PROXY')
    opener=Mock();opener.open.side_effect=error;monkeypatch.setattr(sec.urllib.request,'build_opener',lambda *a:opener)
    with caplog.at_level('INFO'):assert sec._fetch(sec.DIRECTORY) is None
    assert opener.open.call_count==1
    row=sec.source_diagnostics()[-1];assert row['category']==category and row['request_attempted']
    assert 'PRIVATE' not in str(row)+caplog.text and 'offline-fixture' not in caplog.text


def test_existing_operator_file_propagates_coherent_SEC_identity_only(monkeypatch,tmp_path):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    identity(monkeypatch)
    p=tmp_path/'private-config.json';p.write_text(json.dumps({'WEALTHPILOT_SEC_ORGANIZATION':'New Synthetic Operator','NOT_ALLOWED':'private'}))
    load_analysis_environment(p)
    assert sec.request_identity() is None  # no stale parent email paired with new org
    assert 'NOT_ALLOWED' not in sec.os.environ
    p.write_text(json.dumps({'WEALTHPILOT_SEC_ORGANIZATION':'New Synthetic Operator','WEALTHPILOT_SEC_CONTACT_EMAIL':'offline-fixture@operator.org'}))
    load_analysis_environment(p);assert sec.request_identity().startswith('New Synthetic')


def test_source_failure_keeps_L1_and_returns_explicit_safe_issue(monkeypatch):
    from backend.graph.tools import execute_assess_position_evidence
    from research_v2 import official_disclosures as owner
    def collect(*a):
        sec._diagnostics.set((dict(endpoint='directory',category='http_403',http_status=403,request_attempted=True),))
        return [],{}.get
    monkeypatch.setattr(owner,'collect',collect)
    result=execute_assess_position_evidence(symbol='AAPL:US',market_data={'fundamentals':{}},research=[])
    assert result['qualification']['L1']['allowed']
    assert 'SEC_SOURCE_HTTP_403' in result['issues']
    assert 'AV_INDEPENDENT_SOURCE_VERIFICATION_UNAVAILABLE' in result['issues']
    assert not result['verified_field_records']
    assert all(not result['qualification'][l]['allowed'] for l in ('L2','L3','L4'))


def test_CompanyFacts_access_denial_stops_before_originals(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    from tests.test_sec_financial_sources import directory,submissions,AT
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(submissions()).encode(),AT.isoformat()
        sec._diagnose(url,'http_403',status=403,attempted=True)
    monkeypatch.setattr(sec,'_fetch',fetch)
    assert sec.collect('AAPL:US')[0]==[] and len(calls)==3
    assert sec.source_diagnostics()[-1]['category']=='http_403'
    _cache.clear()


def test_successful_JSON_missing_facts_is_not_reported_as_access_failure(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    from tests.test_sec_financial_sources import directory,submissions,AT
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    def fetch(url):
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(submissions()).encode(),AT.isoformat()
        if '/companyfacts/' in url:return json.dumps({'cik':320193,'facts':{}}).encode(),AT.isoformat()
        return None
    monkeypatch.setattr(sec,'_fetch',fetch)
    assert sec.collect('AAPL:US')[0]==[]
    assert [r['category'] for r in sec.source_diagnostics()]==['data_missing']
    _cache.clear()


def test_legacy_official_fetch_cannot_bypass_SEC_operator_identity(monkeypatch):
    from research_v2 import official_disclosures as owner
    monkeypatch.delenv('WEALTHPILOT_SEC_ORGANIZATION',raising=False)
    monkeypatch.delenv('WEALTHPILOT_SEC_CONTACT_EMAIL',raising=False)
    opener=Mock();monkeypatch.setattr(sec.urllib.request,'build_opener',opener)
    url='https://www.sec.gov/Archives/edgar/data/1791706/000110465926101065/0001104659-26-101065.txt'
    assert owner._fetch(url) is None
    opener.assert_not_called()
    assert sec.source_diagnostics()[-1]['category']=='identity_unconfigured'


def test_removing_SEC_identity_from_operator_file_disables_stale_worker_values(monkeypatch,tmp_path):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    identity(monkeypatch)
    p=tmp_path/'private-config.json';p.write_text('{}')
    load_analysis_environment(p)
    assert sec.request_identity() is None
