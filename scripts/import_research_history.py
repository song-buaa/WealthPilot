"""Explicit OFFLINE history initialization; never invoked by normal MCP calls.
Manifest contains known metadata, optional local original report paths and hashes.
It does not import or rewrite Job receipts, Artifact payloads or any database.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from backend.services.research_evaluation import SampleStore, digest, now, DIMENSIONS


def import_reference(store, record):
    # Missing historical facts are null; current capture clock is not research time.
    symbol=record['symbol']
    if not isinstance(symbol,str) or len(symbol)>80:raise ValueError('SYMBOL_INVALID')
    source_hash=record['source_record_sha256']
    if not re.fullmatch('[a-f0-9]{64}',source_hash):raise ValueError('SOURCE_HASH_REQUIRED')
    identity=record.get('job_id') or 'historical:'+source_hash
    sid=store.sample_id(identity)
    reports={}
    for target in ('native','reader'):
        report=record.get(target)
        if report:
            path=Path(report['path'])
            if path.stat().st_size>128*1024:
                raise ValueError('HISTORICAL_REPORT_TOO_LARGE_USE_IMMUTABLE_ARTIFACT_REFERENCE')
            raw=path.read_bytes()
            if sha256(raw).hexdigest()!=report['sha256']:raise ValueError('REPORT_HASH_MISMATCH')
            text=raw.decode('utf-8')
            reports[target]=dict(sha256=report['sha256'],text=text,available=bool(text.strip()),
                version=report.get('version','UNKNOWN'),storage='HISTORICAL_REPORT_COPY')
    historical=record.get('assessment_reference')
    if historical:
        # Scores are historical statements, not a new scored assessment without
        # actual per-dimension basis. Preserve missing detail, never interpolate.
        from decimal import Decimal
        total=Decimal(str(historical['reported_total']))
        if not total.is_finite() or not 0<=total<=10:raise ValueError('HISTORICAL_SCORE_INVALID')
        dimensions=historical.get('dimensions')
        if dimensions is not None:
            if set(dimensions)!=set(DIMENSIONS):raise ValueError('HISTORICAL_DIMENSIONS_INVALID')
            values=[Decimal(str(v)) for v in dimensions.values()]
            if any(not v.is_finite() or not 0<=v<=2 for v in values) or sum(values)!=total:
                raise ValueError('HISTORICAL_TOTAL_MISMATCH')
        historical=dict(historical,status='HISTORICAL_REPORTED_NOT_REASSESSED',
                        quality_status='PASS' if total>=8 else 'FAIL')
    sample=dict(schema_version='wealthpilot.research-sample.v1',sample_id=sid,source='explicit_offline_historical_import',
        created_at=now(),symbol=symbol,purpose=record.get('purpose','UNKNOWN'),question=record.get('question'),
        request=None,request_capture='UNKNOWN',request_fingerprint=None,request_id=None,
        job_id=record.get('job_id'),analysis_run_id=record.get('analysis_run_id'),
        code_revision=record.get('code_revision','UNKNOWN'),source_record_sha256=source_hash,
        historical_assessment_reference=historical)
    state=dict(job_state=record.get('job_state','UNKNOWN'),business_status=record.get('business_status','UNKNOWN'),
        research_at=record.get('research_at'),observation_dates=record.get('observation_dates',[]),
        artifact=dict(artifact_id=record.get('artifact_id') or record.get('job_id'),sha256=record.get('artifact_sha256'),
                      availability='REFERENCE_ONLY_NOT_COPIED'),
        reports=reports,report_available=any(r['available'] for r in reports.values()),import_completeness='REPORT_AVAILABLE' if reports else 'METADATA_ONLY')
    with store.files.locked():
        old=store.files.read(f'sample-{sid}.json')
        if old:
            # Creation time is not part of import idempotency.
            sample['created_at']=old['created_at']
            if old!=sample:raise ValueError('HISTORICAL_IMPORT_CONFLICT')
        else:store.files.write(f'sample-{sid}.json',sample,immutable=True)
        previous=store.files.read(f'state-{sid}.json')
        if previous and previous!=state:raise ValueError('HISTORICAL_STATE_CONFLICT')
        if previous is None:store.files.write(f'state-{sid}.json',state)
    return sid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root',required=True)
    parser.add_argument('--manifest',required=True)
    args=parser.parse_args()
    records=json.loads(Path(args.manifest).read_text())
    store=SampleStore(args.artifact_root)
    try:
        result=[import_reference(store,record) for record in records]
        print(json.dumps(dict(imported_sample_ids=result,production_backfill_authorized=False)))
    finally:store.close()


if __name__=='__main__':main()
