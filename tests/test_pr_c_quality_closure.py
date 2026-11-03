import gzip,hashlib,json
from pathlib import Path
from datetime import datetime,timezone
from research_v2 import sec_disclosures as sec
from backend.services.market_data.evidence import assess


def test_real_monthly_originals_keep_issuer_month_and_source_closure():
    root=Path(__file__).parent/'fixtures/monthly_deliveries'
    for meta in json.loads((root/'manifest.json').read_text()):
        raw=gzip.decompress((root/meta['file']).read_bytes())
        assert hashlib.sha256(raw).hexdigest()==meta['sha256']
        row=sec.parse_filing((raw,meta['fetched_at']),meta['issuer'],meta['filing'],identity_digest=meta['identity_digest'])
        assert row
        result=assess({},symbol=meta['symbol'],research=[row],disclosure_verifier={row['record_id']:row}.get,at=datetime.now(timezone.utc))
        monthly=[x for x in result['verified_field_records'] if x['field']=='vehicle_deliveries_month']
        assert len(monthly)==1
        assert monthly[0]['values']['vehicle_deliveries_month']==(29025 if meta['symbol']=='LI:US' else 41256)
        assert monthly[0]['scope']=='monthly'
        assert monthly[0]['symbol']==meta['symbol']


def test_source_display_keeps_full_trace_in_structured_sources():
    from backend.services.market_data.output_policy import render_explanation
    sources=[dict(evidence_ref='official:'+('a'*200),source='official_disclosure',data_as_of='2026-06-30')]
    paragraph=dict(topic='business',text='经营分析内容',sources=sources)
    rendered=render_explanation([paragraph])
    assert 'a'*200 not in rendered
    assert sources[0]['evidence_ref']=='official:'+('a'*200)
    assert 'official_disclosure / 2026-06-30' in rendered
