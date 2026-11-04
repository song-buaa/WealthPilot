"""Synthetic qualification boundaries; actual issuer research is frozen separately."""
from copy import deepcopy
import hashlib
import pytest
from research_v2.official_disclosures import verify_current_issuer_context, RESEARCH_CONTEXT_URLS
from tests.test_s3_p2_operating_model import fixture
from backend.services.s3_operating_research import evaluate


@pytest.mark.parametrize('fault',[None,'future','naive','hash','issuer','url','owner','empty','http','redirect'])
def test_issuer_context_is_current_capture_not_historical_gaap(fault):
    raw=b'<html>real source replaced by synthetic test boundary</html>'*40
    r=dict(url=RESEARCH_CONTEXT_URLS['0000789019'],sha256=hashlib.sha256(raw).hexdigest(),
           fetched_at='2026-11-03T09:00:00Z',owner='ISSUER_IR_RESEARCH_CONTEXT_NOT_GAAP')
    r.update(http_status=200,final_url=r['url'])
    issuer=dict(cik='789019')
    if fault=='future':r['fetched_at']='2026-11-04T09:00:00Z'
    if fault=='naive':r['fetched_at']='2026-11-03T09:00:00'
    if fault=='hash':r['sha256']='a'*64
    if fault=='issuer':issuer['cik']='320193'
    if fault=='url':r['url']+='?redirect=bad'
    if fault=='owner':r['owner']='SEC'
    if fault=='empty':raw=b''
    if fault=='http':r['http_status']=403
    if fault=='redirect':r['final_url']='https://untrusted.invalid/page'
    if fault:
        with pytest.raises(ValueError,match='ISSUER_RESEARCH_CONTEXT_REJECTED'):
            verify_current_issuer_context(raw,r,issuer,'2026-11-03T10:00:00Z')
    else:
        p=verify_current_issuer_context(raw,r,issuer,'2026-11-03T10:00:00Z')
        assert p['known_at']==p['fetched_at'] and 'published_at' not in p
        assert p['publication_clock']=='UNKNOWN_MUTABLE_PAGE_CAPTURE_CLOCK_ONLY'


@pytest.mark.parametrize('all_refused',[False,True])
def test_method_owner_refusal_with_no_parameters_does_not_break_pool(all_refused):
    q,d,a=fixture();before=deepcopy(q)
    for c in q['cards'][:2 if all_refused else 1]:
        c.update(assumptions=None,scenarios={},comparison_status='NOT_COMPARABLE',
                 assumption_context=None,group=None,rank=None)
    r=evaluate(q,d,a)
    assert r['comparable_count']==(0 if all_refused else 1)
    c=r['cards'][0]
    assert c['quality']==before['cards'][0]['quality']
    assert not c['scenarios'] and c['rank'] is None and c['group'] is None
    assert c['assumptions'] is None and 'conditional_value_range' not in c
    assert not r['execution_authorized'] and not r['order_created']
