"""Versioned research judgments over source bytes captured by the existing SEC owner."""
import hashlib
import json
from pathlib import Path

from backend.services import quality_valuation_research as archive
from backend.services.market_data.quality_valuation import stamp
from backend.services.instruments.issuers import resolve_sec_issuer
from research_v2 import sec_disclosures as sec

BOOK = 'research_v2/s3_profiles/relative-v1.json'
VERSION = 's3-relative-reviewed-dossier.v1'
DIMENSIONS = ('business_model', 'competitive_advantage', 'growth_runway', 'earnings_and_cash',
              'capital_allocation', 'certainty_risks_and_counterevidence')


def registry():
    book = json.loads((archive.ROOT / BOOK).read_text())
    if book['version'] != VERSION:
        raise ValueError('S3_RELATIVE_DOSSIER_VERSION_REJECTED')
    return book


def _receipt(root, row, cutoff):
    raw = archive.safe_file(root, row['path']).read_bytes()
    fetched = stamp(row['fetched_at'])
    if not fetched or fetched > cutoff or hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise ValueError('S3_QUALITY_SOURCE_HASH_OR_CLOCK_REJECTED')
    return raw, row['fetched_at']


def _prospectus_proof(filing, submissions, body, row):
    # Qualitative source only. The SEC statement Owner continues to qualify
    # financial facts and does not accept a prospectus as a quarterly statement.
    recent = submissions['filings']['recent']
    index = recent['accessionNumber'].index(filing['accession'])
    if (recent['form'][index] != '424B4'
            or recent['primaryDocument'][index] != row['url'].rsplit('/', 1)[-1]
            or sec.allowed_url(row['url']) is False
            or not body or b'<html' not in body[:4000].lower()):
        return None
    return dict(source_ref=row['url'], source_sha256=row['sha256'],
                fetched_at=row['fetched_at'], published_at=recent['acceptanceDateTime'][index],
                scope='SEC_PRIMARY_PROSPECTUS_WITH_SUBMISSIONS_CLOCK_QUALITATIVE_ONLY')


def _filing_url(cik, source):
    accession = source['accession']
    name = source.get('primary') or accession + '.txt'
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{name}")


def capture(root, manifest, symbols):
    """Record only the finite source list needed by this dossier."""
    root = Path(root)
    companies = registry()['companies']
    count = 0
    for symbol in symbols:
        profile = companies.get(symbol)
        entry = manifest['companies'].get(symbol)
        if not profile or not entry:
            continue
        for source in profile['sources'].values():
            url = _filing_url(profile['cik'], source)
            if any(r.get('url') == url and r.get('path') for r in manifest['requests']):
                continue
            count += 1
            raw = sec._fetch(url)
            if not raw:
                entry.setdefault('quality_source_failures', []).append(
                    dict(url=url, diagnostics=sec.source_diagnostics()[-1:]))
                continue
            body, at = raw
            filename = symbol[:-3]+'-research-'+source['accession']+('.html' if source.get('primary') else '.txt')
            (root / filename).write_bytes(body)
            receipt = dict(url=url, path=filename, sha256=hashlib.sha256(body).hexdigest(), fetched_at=at,
                           owner='SEC', status='RAW_NOT_YET_QUALIFIED')
            manifest['requests'].append(receipt)
            if not source.get('primary'):
                entry.setdefault('originals', []).append(receipt)
    manifest['relative_quality_sec_owner_calls'] = count


def quality_cards(root, manifest, cutoff, book):
    cutoff = stamp(cutoff)
    result = {}
    for symbol, profile in book['companies'].items():
        if symbol not in manifest['companies']:
            continue
        entry = manifest['companies'][symbol]
        try:
            reviewed = stamp(profile['reviewed_at'])
            if not reviewed or reviewed > cutoff:
                raise ValueError('S3_QUALITY_PROFILE_NOT_YET_RECORDED')
            if (entry['directory']['url'] != sec.DIRECTORY or entry['submissions']['url'] !=
                    'https://data.sec.gov/submissions/CIK'+profile['cik']+'.json'):
                raise ValueError('S3_QUALITY_ISSUER_SOURCE_REJECTED')
            directory = _receipt(root, entry['directory'], cutoff)
            sub = _receipt(root, entry['submissions'], cutoff)
            submissions = json.loads(sub[0])
            issuer = resolve_sec_issuer(symbol, json.loads(directory[0]), submissions,
                                        allow_registration_only=True)
            if not issuer or issuer['cik'] != profile['cik']:
                raise ValueError('S3_QUALITY_ISSUER_MISMATCH')
            filings = {row['accession']: row for row in sec.discover_filings(issuer, submissions)}
            evidence = {}
            for label, expected in profile['sources'].items():
                url = _filing_url(profile['cik'], expected)
                row = next(r for r in manifest['requests'] if r.get('url') == url and r.get('path'))
                if row['sha256'] != expected['sha256']:
                    raise ValueError('S3_QUALITY_REVIEWED_BYTES_CHANGED')
                body, fetched = _receipt(root, row, cutoff)
                if expected.get('primary'):
                    proof = _prospectus_proof(expected, submissions, body, row)
                else:
                    filing = filings[expected['accession']]
                    proof = sec.verify_filing_envelope((body, fetched), issuer, filing)
                published = stamp(proof['published_at']) if proof else None
                if not published or not published <= min(reviewed, stamp(fetched), cutoff):
                    raise ValueError('S3_QUALITY_DISCLOSURE_NOT_QUALIFIED')
                evidence[label] = dict(proof, accession=expected['accession'], locator=expected['locator'])
            dimensions = profile['dimensions']
            if set(dimensions) != set(DIMENSIONS) or any(
                not all(item.get(k) for k in ('fact','judgment','counterevidence','evidence'))
                or not set(item['evidence']) <= evidence.keys() for item in dimensions.values()
            ):
                raise ValueError('S3_QUALITY_DIMENSION_EVIDENCE_MISSING')
            result[symbol] = dict(status='EVIDENCE_BOUND_RESEARCH_JUDGMENTS_NOT_VALUATION_QUALIFICATION',
                                  version=book['version'], reviewed_at=profile['reviewed_at'],
                                  dimensions=dimensions, sources=evidence,
                                  valuation_method=profile['valuation_method'],
                                  equity_bridge=profile['equity_bridge'],
                                  valuation_blocker=profile.get('valuation_blocker'))
        except (KeyError, TypeError, ValueError, OSError, StopIteration) as exc:
            from backend.services.s3_pool_research import _error_code
            result[symbol] = dict(status='INSUFFICIENT_EVIDENCE',
                                  reason=_error_code(exc, 'S3_QUALITY_PROFILE_SOURCE_UNAVAILABLE'))
    return result
