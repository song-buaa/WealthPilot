"""Thin server-owned P2.2 profile binding; original Owners retain every fact/gate."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import hashlib
import json

from backend.services import quality_valuation_research as archive
from backend.services.market_data import quality_valuation as engine
from backend.services.market_data.financial_research import operating_ttm_bridge
from backend.services.s3_method_research import identity_projection
from research_v2 import sec_disclosures as sec
from research_v2 import official_disclosures as official

VERSION='s3-p3.professional-entry.v1'
BOOK='research_v2/s3_profiles/p22-v1.json'


def registry():
    value=json.loads((archive.ROOT/BOOK).read_text())
    if value['version']!='s3-p3.reviewed-profiles.p22.v1':raise ValueError('PROFESSIONAL_PROFILE_VERSION_REJECTED')
    return value


def signature(baseline):
    return dict(start=baseline['start'],period=baseline['period'],
        flows={k:str(Decimal(str(v['value'])).normalize()) for k,v in baseline['flows'].items()},
        eps=str(Decimal(str(baseline['eps']['values']['diluted_eps_ttm'])).normalize()))


def expected_signature(value):
    return dict(start=value['start'],period=value['period'],
        flows={k:str(Decimal(str(v)).normalize()) for k,v in value['flows'].items()},
        eps=str(Decimal(value['eps']).normalize()))


def capture_context(root,symbols):
    """Explicit public supplement only, using existing SEC and issuer Owners.

    No provider configuration writes, holdings, databases, model or price engine.
    Per-company failures are retained for qualification; other companies continue.
    """
    from backend.services.s3_pool_capture import now
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text());book=registry()
    def store(body,rec,name):
        (root/name).write_bytes(body);rec=dict(rec,path=name)
        manifest['requests'].append(rec);return rec
    for symbol in symbols:
        if symbol not in book['companies']:continue
        entry=manifest['companies'][symbol]
        try:
            issuer,_,_=identity_projection(symbol,
                ((root/entry['directory']['path']).read_bytes(),entry['directory']['fetched_at']),
                ((root/entry['submissions']['path']).read_bytes(),entry['submissions']['fetched_at']))
            filings=sec.discover_filings(issuer,json.loads((root/entry['submissions']['path']).read_text()))
            required={n['url'] for n in book['companies'][symbol]['sources'] if n['role']!='OFFICIAL_IR_CONTEXT'}
            required.update(f['full_url'] for f in [x for x in filings if x['form']=='10-Q'][:3])
            existing={r['url'] for r in entry.get('originals',[])}
            for url in sorted(required-existing):
                filing=next((f for f in filings if f['full_url']==url),None)
                if not filing:continue
                source=sec._fetch(url)
                if source:
                    rec=store(source[0],dict(url=url,sha256=hashlib.sha256(source[0]).hexdigest(),
                        fetched_at=source[1],owner='SEC',status='RAW_NOT_YET_QUALIFIED'),
                        symbol[:-3]+'-'+filing['accession']+'.txt')
                    entry.setdefault('originals',[]).append(rec)
                else:entry.setdefault('professional_capture_failures',[]).append(dict(kind='SEC',accession=filing['accession'],
                    status='SOURCE_UNAVAILABLE',diagnostics=sec.source_diagnostics()[-1:]))
            body,rec=official.fetch_current_issuer_context(issuer['cik'])
            store(body,rec,symbol[:-3]+'-professional-context.html')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            entry.setdefault('professional_capture_failures',[]).append(dict(kind='RESEARCH_CONTEXT',status='SOURCE_UNAVAILABLE',
                http_status=getattr(exc,'code',None),error_class=type(exc).__name__))
        manifest['finished_at']=now()
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2))


def prepare(parent_path):
    """Revalidate current parent facts, latest filings and all reviewed context.

    Identical reviewed parameters are not automatically applicable to a later
    financial window or changed filing/article. Such cards remain UNKNOWN.
    This function is pure/offline so historical read repeats the same checks.
    """
    parent_path=Path(parent_path);parent=archive.read_bundle(parent_path);p=parent['payload']
    root=parent_path.parent/'sources/inputs';manifest=archive._load(root,'manifest.json',p['request']['source_manifest_sha256'])
    book=registry();cutoff=p['request']['decision_at'];profiles={};checks={};failures={}
    context=dict(version=VERSION,profile_version=book['version'],profile_sha256=archive.file_hash(archive.ROOT/BOOK),
        reviewed_artifact_id=book['reviewed_artifact_id'],reviewed_at=book['reviewed_at'],revalidated_at=cutoff,
        scope='REQUALIFIED_REVIEWED_PARAMETERS_NOT_NEW_FORECAST_OR_POLICY',companies=checks,failures=failures,
        annual_eps_snapshots={})
    def read(rec):
        f=archive.safe_file(root,rec['path'])
        if archive.file_hash(f)!=rec['sha256'] or engine.stamp(rec['fetched_at'])>engine.stamp(cutoff):
            raise ValueError('PROFESSIONAL_SOURCE_HASH_OR_CLOCK_REJECTED')
        return f.read_bytes()
    for card in p['result']['cards']:
        symbol=card['symbol']
        try:
            if card['comparison_status']!='COMPARABLE':
                raise ValueError(card.get('failure_reason') or 'P2_PARENT_NOT_COMPARABLE')
            if symbol not in book['companies']:raise ValueError('PROFESSIONAL_PROFILE_NOT_SUPPORTED')
            if engine.stamp(book['reviewed_at'])>engine.stamp(cutoff):
                raise ValueError('PROFESSIONAL_PROFILE_NOT_YET_REVIEWED')
            reviewed=book['companies'][symbol];entry=manifest['companies'][symbol]
            issuer,_,_=identity_projection(symbol,(read(entry['directory']),entry['directory']['fetched_at']),
                (read(entry['submissions']),entry['submissions']['fetched_at']))
            if issuer['cik']!=reviewed['cik']:raise ValueError('PROFESSIONAL_ISSUER_CHANGED_REQUIRES_REVIEW')
            filings=sec.discover_filings(issuer,json.loads(read(entry['submissions'])))
            latest={form:next((f['accession'] for f in filings if f['form']==form),None) for form in ['10-K','10-Q','8-K']}
            if latest!=reviewed['latest_reviewed_filings']:raise ValueError('PROFESSIONAL_NEW_DISCLOSURE_REQUIRES_REVIEW')
            actual=signature(operating_ttm_bridge(p['assessments'][symbol]))
            if actual!=expected_signature(reviewed['financial_signature']):raise ValueError('PROFESSIONAL_FINANCIAL_WINDOW_REQUIRES_REVIEW')
            profile=deepcopy(reviewed['profile']);notes=[];proofs=[]
            for source in reviewed['sources']:
                rows=manifest['requests'] if source['role']=='OFFICIAL_IR_CONTEXT' else entry['originals']
                rec=next((r for r in reversed(rows) if r.get('url')==source['url'] and r.get('path')),None)
                if not rec:raise ValueError('PROFESSIONAL_REVIEWED_SOURCE_UNAVAILABLE')
                body=read(rec)
                if source['role']=='OFFICIAL_IR_CONTEXT':
                    proof=official.verify_current_issuer_context(body,rec,issuer,cutoff)
                    fingerprint=official.research_context_fingerprint(body,issuer['cik'])
                    if fingerprint!=source['visible_context_sha256']:raise ValueError('PROFESSIONAL_CONTEXT_CHANGED_REQUIRES_REVIEW')
                    proof['visible_context_sha256']=fingerprint
                else:
                    filing=next((f for f in filings if f['full_url']==rec['url']),None)
                    proof=sec.verify_filing_envelope((body,rec['fetched_at']),issuer,filing)
                    if not proof or engine.stamp(proof['published_at'])>engine.stamp(cutoff) or rec['sha256']!=source['sha256']:
                        raise ValueError('PROFESSIONAL_REVIEWED_DISCLOSURE_REJECTED')
                proofs.append(proof)
                notes.append(dict(path=rec['path'],sha256=rec['sha256'],url=rec['url'],begin=0,end=len(body),
                    excerpt_sha256=rec['sha256'],summary=source['summary'],role=source['role']))
            profile['source_notes']=notes;facts=card['quality']['financial_inputs']['facts']
            for name,fields in [('revenue',['revenue']),('operating_margin',['revenue','operating_income']),
                                ('conversion',['net_income','operating_income']),('shares',['diluted_shares','sbc','repurchases']),
                                ('terminal_pe',['revenue','operating_income','net_income'])]:
                refs=sorted({ref for row in facts if row['field'] in fields for ref in row['evidence_refs']})
                if not refs:raise ValueError('PROFESSIONAL_CURRENT_FACT_ANCHOR_MISSING')
                profile['notes'][name]['evidence_refs']=refs
            profiles[symbol]=profile
            context['annual_eps_snapshots'][symbol]=reviewed['annual_eps_snapshots']
            checks[symbol]=dict(status='REVALIDATED',financial_signature=actual,latest_filings=latest,evidence=proofs)
        except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
            reason=str(exc) if type(exc) is ValueError and str(exc).isupper() else 'PROFESSIONAL_EVIDENCE_REQUIRES_REVIEW'
            failures[symbol]=reason;checks[symbol]=dict(status='INSUFFICIENT_EVIDENCE',reason=reason,
                capture_diagnostics=manifest['companies'].get(symbol,{}).get('professional_capture_failures',[]))
    return dict(version='s3-p2.research-dossier.v1',parent_artifact_id=parent['artifact_id'],
        research_recorded_at=cutoff,companies=profiles),context


def provenance(parent,context):
    return dict(parent,research_method='professional',professional_entry_version=VERSION,
        assumption_profile_version=context['profile_version'],assumption_profile_sha256=context['profile_sha256'])


def make_bundle(parent_path):
    from backend.services.s3_operating_research import make_bundle as operating
    dossier,context=prepare(parent_path);parent=archive.read_bundle(parent_path)
    return operating(parent_path,dossier,professional_context=context,
        entry_provenance=provenance(parent['payload']['entry_provenance'],context),_frozen_owner=True)
