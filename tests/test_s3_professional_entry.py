"""P3 integration boundaries; synthetic cases are not actual-company acceptance."""
from copy import deepcopy
import hashlib,json
from pathlib import Path
import pytest
from pydantic import ValidationError
from backend.services import s3_research_entry as entry
from backend.services import s3_professional_research as professional
from backend.services import s3_operating_research as operating
from tests.test_s3_product_adapter import failed_sources
from tests.test_s3_p2_operating_model import fixture
from research_v2 import official_disclosures as official


def test_default_and_explicit_method_with_no_client_forecast_fields():
    assert entry.CompanyPoolRequest(symbols=['AAPL'],purpose='x').method=='standard'
    assert entry.CompanyPoolRequest(symbols=['AAPL'],purpose='x',method='professional').method=='professional'
    for fields in [dict(method='other'),dict(dossier={}),dict(terminal_pe=100),dict(profile_version='override')]:
        with pytest.raises(ValidationError):entry.CompanyPoolRequest(symbols=['AAPL'],purpose='x',**fields)


def test_profile_registry_exact_approved_parameters_and_no_meta():
    book=professional.registry()
    assert set(book['companies'])=={'AAPL:US','MSFT:US','NVDA:US'}
    assert book['reviewed_artifact_id']=='28a91837df850cf86871f13990e16a06f0fdc40227d9cb0eb6b3e6fada62b811'
    assert book['companies']['AAPL:US']['profile']['base']['terminal_pe']=='24'
    assert book['companies']['MSFT:US']['profile']['base']['revenue_growth']==['0.16','0.14','0.12','0.11','0.10']
    assert book['companies']['NVDA:US']['profile']['base']['terminal_operating_margin']=='0.55'


def test_professional_all_source_failures_still_freeze_and_read(tmp_path,monkeypatch):
    def no_network(*a,**k):raise AssertionError('unexpected capture')
    monkeypatch.setattr(professional,'capture_context',no_network)
    root=failed_sources(tmp_path/'sources',('AAPL:US','META:US'))
    r=entry.run_company_pool(dict(symbols=['AAPL','META'],purpose='synthetic qualification',method='professional'),
        archive_root=tmp_path/'archive',source_root=root,synthetic=True)
    assert r['intent']['method']=='professional'
    assert len(r['professional_result']['cards'])==2 and r['data_quality']['comparable_count']==0
    assert r['evidence']['verification']['verified']
    assert r['professional_result']['assumption_profile_version']==professional.registry()['version']
    reread=entry.read_company_pool(r['evidence']['artifact_id'],archive_root=tmp_path/'archive')
    assert reread==r
    assert not r['execution_authorized'] and not r['order_created'] and not r['scope']['holdings_used']


@pytest.mark.parametrize('all_refused',[False,True])
def test_profile_failure_keeps_cards_and_never_falls_back_to_p1(all_refused):
    q,d,a=fixture()
    failed={'AAA:US':'PROFESSIONAL_NEW_DISCLOSURE_REQUIRES_REVIEW'}
    d['companies'].pop('AAA:US')
    if all_refused:failed['BBB:US']='PROFESSIONAL_PROFILE_NOT_SUPPORTED';d['companies'].pop('BBB:US')
    r=operating.evaluate(q,d,a,failed)
    assert len(r['cards'])==2 and r['comparable_count']==(0 if all_refused else 1)
    c=r['cards'][0]
    assert c['quality']==q['cards'][0]['quality'] and c['failure_reason']==failed['AAA:US']
    assert not c['scenarios'] and c['assumptions'] is None and c['rank'] is None
    assert 'conditional_value_range' not in c


def test_parent_owner_refusal_takes_priority_over_profile_selection():
    q,d,a=fixture();reason='KNOWN_CAPITAL_STRUCTURE_CHANGE_REQUIRES_ENTITLEMENT_BRIDGE'
    c=q['cards'][0];c.update(comparison_status='NOT_COMPARABLE',failure_reason=reason,scenarios={},assumptions=None)
    d['companies'].pop(c['symbol'])
    r=operating.evaluate(q,d,a,{c['symbol']:'PROFESSIONAL_PROFILE_NOT_SUPPORTED'})
    assert r['cards'][0]['failure_reason']==reason and not r['cards'][0]['scenarios']


def test_current_article_fingerprint_ignores_markup_not_changed_economics():
    text='Services verified research context '*80
    a=('<article class="article"><p>'+text+'</p></article>').encode()
    b=('<article class="article"><script>nonce=changed</script><p>'+text+'</p></article>').encode()
    assert official.research_context_fingerprint(a,'320193')==official.research_context_fingerprint(b,'320193')
    assert official.research_context_fingerprint(a,'320193')!=official.research_context_fingerprint(a.replace(b'verified',b'revised'),'320193')
    with pytest.raises(ValueError):official.research_context_fingerprint(b'<html>error</html>','320193')


def test_current_context_fetch_uses_existing_no_redirect_handler(monkeypatch):
    import urllib.error
    calls=[]
    def opener(handler):
        assert isinstance(handler,official._NoRedirect)
        class Client:
            def open(self,request,timeout):
                calls.append(request.full_url)
                assert handler.redirect_request(request,None,302,'redirect',{},'https://other.invalid/') is None
                raise urllib.error.HTTPError(request.full_url,302,'redirect',{},None)
        return Client()
    monkeypatch.setattr(official.urllib.request,'build_opener',opener)
    with pytest.raises(urllib.error.HTTPError):official.fetch_current_issuer_context('320193')
    assert calls==[official.RESEARCH_CONTEXT_URLS['0000320193']]


def test_public_professional_capture_never_reuses_old_mutable_metadata(tmp_path,monkeypatch):
    from backend.services import s3_pool_capture
    seen=[]
    def capture(symbols,root,*,reuse=None):
        seen.append(reuse);Path(root).parent.mkdir(parents=True,exist_ok=True)
        failed_sources(root,tuple(symbols))
    monkeypatch.setattr(s3_pool_capture,'capture_pool',capture)
    monkeypatch.setattr(professional,'capture_context',lambda *a:None)
    r=entry.run_company_pool(dict(symbols=['AAPL'],purpose='synthetic live-branch capture boundary',method='professional'),
        archive_root=tmp_path/'archive',reuse_sources=tmp_path/'old-metadata')
    assert seen==[None] and r['intent']['method']=='professional'


@pytest.mark.parametrize('fault',[None,'new_filing','financial_value','future_source','changed_original','issuer','future_profile'])
def test_current_profile_requires_same_reviewed_evidence_window(tmp_path,monkeypatch,fault):
    q,d,a=fixture();symbol='AAA:US';root=tmp_path/'sources/inputs';root.mkdir(parents=True)
    raw=b'{}';document=b'synthetic immutable official source, never company acceptance'
    (root/'metadata.json').write_bytes(raw);(root/'original.txt').write_bytes(document)
    rec=dict(path='metadata.json',sha256=hashlib.sha256(raw).hexdigest(),fetched_at='2026-01-01T00:00:00Z')
    original=dict(path='original.txt',sha256=hashlib.sha256(document).hexdigest(),url='https://www.sec.gov/synthetic',fetched_at=rec['fetched_at'])
    manifest=dict(schema_version='s3-d.sources.v1',requests=[original],companies={symbol:dict(directory=rec.copy(),submissions=rec.copy(),originals=[original])})
    if fault=='future_source':manifest['companies'][symbol]['directory']['fetched_at']='2030-01-01T00:00:00Z'
    (root/'manifest.json').write_text(json.dumps(manifest))
    from backend.services.market_data.financial_research import operating_ttm_bridge
    profile=deepcopy(d['companies'][symbol]);profile.pop('source_notes')
    for note in profile['notes'].values():note.pop('evidence_refs')
    expected=professional.signature(operating_ttm_bridge(a[symbol]))
    reviewed=dict(cik='0000000123',financial_signature=expected,profile=profile,annual_eps_snapshots={},
        latest_reviewed_filings={'10-K':'K','10-Q':'Q','8-K':'E'},sources=[dict(url=original['url'],sha256=original['sha256'],summary='Synthetic qualified source context for contract tests only.',role='CURRENT_DISCLOSURE_CONTEXT')])
    if fault=='financial_value':reviewed['financial_signature']['eps']='999'
    if fault=='changed_original':reviewed['sources'][0]['sha256']='a'*64
    book=dict(version='synthetic-review',reviewed_artifact_id='a'*64,reviewed_at=rec['fetched_at'],companies={symbol:reviewed})
    if fault=='future_profile':book['reviewed_at']='2030-01-01T00:00:00Z'
    monkeypatch.setattr(professional,'registry',lambda:book)
    monkeypatch.setattr(professional,'identity_projection',lambda *a:(dict(cik='0000000999' if fault=='issuer' else '0000000123'),{},{}))
    fs=[dict(form=f,accession=('K2' if fault=='new_filing' and f=='10-K' else acc),full_url=original['url']) for f,acc in [('10-K','K'),('10-Q','Q'),('8-K','E')]]
    monkeypatch.setattr(professional.sec,'discover_filings',lambda *a:fs)
    monkeypatch.setattr(professional.sec,'verify_filing_envelope',lambda *a:dict(source_ref=original['url'],source_sha256=original['sha256'],published_at=rec['fetched_at'],fetched_at=rec['fetched_at']))
    # The fixture's qualification catalog is synthetic; add an existing ref for
    # the share-anchor contract without claiming an actual issuer observation.
    refs=list(q['cards'][0]['quality']['evidence_catalog'])[:1]
    q['cards'][0]['quality']['financial_inputs']['facts'].append(dict(field='diluted_shares',evidence_refs=refs))
    parent=dict(artifact_id='b'*64,payload=dict(result=q,assessments=a,request=dict(decision_at=q['decision_at'],source_manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())))
    monkeypatch.setattr(professional.archive,'read_bundle',lambda path:parent)
    dossier,context=professional.prepare(tmp_path/'bundle.json')
    assert context['companies']['BBB:US']['reason']=='PROFESSIONAL_PROFILE_NOT_SUPPORTED'
    if fault:
        assert symbol not in dossier['companies'] and symbol in context['failures']
    else:
        assert context['companies'][symbol]['status']=='REVALIDATED'
        assert dossier['companies'][symbol]['base']==d['companies'][symbol]['base']


def test_all_refused_professional_artifact_has_standalone_recomputation(tmp_path):
    import subprocess,sys
    root=failed_sources(tmp_path/'sources',('META:US',))
    result=entry.run_company_pool(dict(symbols=['META'],purpose='synthetic independent refusal',method='professional'),
        archive_root=tmp_path/'archive',source_root=root,synthetic=True)
    bundle=tmp_path/'archive'/result['evidence']['artifact_id']/'bundle.json'
    output=subprocess.check_output([sys.executable,str(professional.archive.ROOT/'scripts/s3_independent_recompute.py'),str(bundle)],text=True)
    assert json.loads(output)['calculations_and_joint_ranks']=='PASS'
