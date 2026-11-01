"""Thin public-company research entry. No account, policy or trading authority."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.utils.symbol import normalize_symbol, parse_symbol


class CompanyPoolRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] = Field(min_length=1, max_length=20)
    purpose: str = Field(min_length=1, max_length=1000)

    @field_validator('symbols')
    @classmethod
    def canonical_symbols(cls, values):
        result = []
        for value in values:
            ticker, market = parse_symbol(value.strip().upper() if ':' in value else value.strip().upper()+':US')
            if market != 'US' or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', ticker):
                raise ValueError('S3_US_EQUITIES_ONLY')
            result.append(normalize_symbol(ticker, market))
        if len(result) != len(set(result)):
            raise ValueError('S3_DUPLICATE_COMPANY')
        return result

    @field_validator('purpose')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('S3_PURPOSE_REQUIRED')
        return value


def private_archive(path, *, create=True):
    root = Path(path).expanduser().absolute()
    # Reject linked ancestors too: operator configuration is not a client path.
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('S3_ARCHIVE_INVALID')
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = root.stat()
    if not root.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('S3_ARCHIVE_INVALID')
    return root


def transport_result(result):
    """Lossless decisions, deduplicated citations; full audit tree stays frozen.

    Never recompute or truncate a gate, scenario, sensitivity, rank or warning.
    Only repeated raw financial input trees and uncited historical catalog rows
    are omitted. Aliases resolve to original Evidence IDs and publication links.
    """
    value = deepcopy(result)
    catalog = {}
    for card in value['cards']:
        quality = card.get('quality', {})
        catalog.update(quality.pop('evidence_catalog', {}))
        quality.pop('gaap_series', None)
        inputs = quality.pop('financial_inputs', {})
        quality['financial_reconciliation'] = {k: inputs[k] for k in
            ('period', 'latest_cash', 'conflicts', 'normalization', 'rejected_refs') if k in inputs}
    references = set()
    def collect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key == 'evidence_refs':
                    references.update(child)
                else:
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    aliases = {ref: 'e'+str(i+1) for i, ref in enumerate(sorted(references))}
    def replace(item):
        if isinstance(item, dict):
            return {k: replace(v) for k,v in item.items()}
        if isinstance(item, list):
            return [replace(v) for v in item]
        return aliases.get(item, item) if isinstance(item, str) else item
    value = replace(value)
    sources, documents = {}, {}
    citations = {}
    for original, alias in aliases.items():
        row = catalog.get(original, {})
        citation = {k: row[k] for k in ('field','period','currency','unit','published_at','fetched_at','historical_only') if k in row}
        citation['record_id'] = original
        links = []
        for link in row.get('publication_links', []):
            source = {k: link[k] for k in ('source_url','source_document_sha256') if k in link}
            key = tuple(sorted(source.items()))
            if key not in documents:
                documents[key] = 'd'+str(len(documents)+1)
                sources[documents[key]] = source
            links.append(dict(source=documents[key], **{k: link[k] for k in
                ('source_locator',) if k in link}))
        citation['publication_links'] = links
        citation['catalog_status'] = 'AVAILABLE' if row else 'REFER_TO_FROZEN_OWNER_ASSESSMENT'
        citations[alias] = citation
    value['evidence_catalog'] = citations
    value['evidence_sources'] = sources
    value['transport_projection'] = 's3-mcp.cited-evidence.v1; full original result remains in immutable bundle'
    return value


def project(bundle, verification):
    from backend.services import quality_valuation_research as store
    payload = bundle['payload']; result = payload['result']
    provenance = payload.get('entry_provenance', {})
    if type(provenance.get('synthetic')) is not bool:
        raise ValueError('S3_ARTIFACT_UNAVAILABLE')
    synthetic = provenance['synthetic']
    prices = {symbol: [{k: record.get('values', {}).get('provenance', {}).get(k, record.get(k))
        for k in ('record_id', 'source_url', 'raw_sha256', 'data_as_of', 'fetched_at', 'price_basis')}
        for record in assessment.get('records', []) if record.get('kind') == 'technical']
        for symbol, assessment in payload['assessments'].items()}
    return dict(synthetic=synthetic, scenario='company_pool_research', status=result['status'],
        issues=result.get('issues', []), queried_at=result['decision_at'],
        conclusion=store.markdown(result).split('## 逐公司判断卡')[0].strip(),
        intent=dict(symbols=provenance['symbols'], purpose=payload['request']['purpose']),
        professional_result=transport_result(result),
        evidence=dict(artifact_id=bundle['artifact_id'], verification=verification,
            entry_provenance=provenance, price_sources=prices, full_result_sha256=store.digest(result), method_files=payload['method_files'], source_manifest_sha256=payload['request']['source_manifest_sha256']),
        data_quality=dict(maturity='RESEARCH_ONLY', comparable_count=result['comparable_count']),
        scope=dict(mode='DETERMINISTIC_RESEARCH_SKILL', holdings_used=False,
            configuration_priority='UNKNOWN_NOT_EVALUATED', company_priority_is_not_portfolio_allocation=True),
        limitations=['Research Only: finite-window US GAAP nonfinancial screening, not complete enterprise quality or investment advice.',
            'Candidate scenario inputs are research assumptions, not verified forecasts, guidance or effective Investment Policy.',
            'Conditional discounted prices are not objective fair value or complete DCF.',
            'No four-agent model review is claimed. Unknown and noncomparable companies remain in the original result.',
            'Frozen reads retain the original decision clock; they are not refreshed market information.'],
        professional_analysis_complete=False, execution_authorized=False, order_created=False)


def run_company_pool(request, *, archive_root, source_root=None, reuse_sources=None, synthetic=False):
    from backend.services import s3_pool_research as engine
    from backend.services import quality_valuation_research as store
    request = CompanyPoolRequest.model_validate(request)
    root = private_archive(archive_root)
    source_mode = 'FROZEN_REQUALIFICATION' if source_root is not None else 'PUBLIC_CAPTURE'
    if source_root is None:
        if synthetic:
            raise ValueError('S3_SYNTHETIC_SOURCE_REQUIRED')
        from backend.services.s3_pool_capture import capture_pool
        source_root = root / '.captures' / uuid.uuid4().hex
        capture_pool(request.symbols, source_root, reuse=reuse_sources)
    source_root = Path(source_root)
    # Capture/load first; never accept a client-supplied or backdated clock.
    decision_at = datetime.now(timezone.utc).isoformat()
    raw = dict(pool_id='chatgpt_company_pool', pool_version='v1', purpose=request.purpose,
        companies={s: None for s in request.symbols}, decision_at=decision_at,
        source_manifest_sha256=store.file_hash(source_root / 'manifest.json'))
    bundle, files = engine.make_bundle(source_root, raw)
    bundle['payload']['entry_provenance'] = dict(version='s3-mcp.entry.v1', synthetic=synthetic,
        source_mode=source_mode, symbols=request.symbols)
    bundle['artifact_id'] = store.digest(bundle['payload'])
    target = store.save_bundle(root, bundle, files)
    verification = engine.replay(target / 'bundle.json')
    return project(bundle, verification)


def read_company_pool(artifact_id, *, archive_root, synthetic=False):
    from backend.services import quality_valuation_research as store
    from backend.services import s3_pool_research as engine
    if not isinstance(artifact_id, str) or not re.fullmatch(r'[a-f0-9]{64}', artifact_id):
        raise ValueError('S3_ARTIFACT_INVALID')
    path = private_archive(archive_root, create=False) / artifact_id / 'bundle.json'
    if path.parent.is_symlink():
        raise ValueError('S3_ARTIFACT_INVALID')
    bundle = store.read_bundle(path)
    if bundle['artifact_id'] != artifact_id:
        raise ValueError('S3_ARTIFACT_INVALID')
    try:
        verification = engine.replay(path)
    except ValueError as error:
        if str(error) != 'METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION':
            raise
        verification = dict(verified=False, integrity_verified=True,
            reason='METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    return project(bundle, verification)
