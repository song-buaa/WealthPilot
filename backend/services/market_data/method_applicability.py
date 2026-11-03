"""Source-driven S3 applicability; SEC verifies reviewed notice envelopes.

Issuer resolution proves identity, never conversion of EPS between share classes.
Only the existing earnings model is implemented. Other methods stay uncomputed.
"""
from backend.services.market_data.evidence import stamp
from pathlib import Path
import gzip
import hashlib
import json
import zlib

VERSION = 's3-c.method-applicability.v1.3'
APPLICABLE = 'APPLICABLE'
NOT_APPLICABLE = 'NOT_APPLICABLE'
INSUFFICIENT = 'INSUFFICIENT_EVIDENCE'
RETURN_BASIS = 'FIVE_YEAR_USD_LISTED_EQUITY_CASH_DIVIDENDS_NO_REINVESTMENT'
NOTICE_ROOT = Path(__file__).resolve().parents[3] / 'research_v2/qualified_notices'
NOTICE_FILES = ('meta-capital-structure.json', '0001628280-26-066700.txt.gz', '0001193125-26-438017.txt.gz')
NOTICE_MANIFEST_SHA256 = '7063abdc75b89116bf869752bb1d702fff0536b12b841b2d7aa5f943bf430314'


def notice_assets():
    """Existing archive freezes these public reviewed source bytes, not a cache."""
    return tuple('research_v2/qualified_notices/'+name for name in NOTICE_FILES
                 if (NOTICE_ROOT/name).is_file() and not (NOTICE_ROOT/name).is_symlink() and not NOTICE_ROOT.is_symlink())


def capital_structure_qualification(assessment):
    """Bounded reviewed source notice, not a ticker blacklist/event scanner.

    Reuse P2.1's identified unresolved rights. A verified issuer identity binds
    the notice; it neither changes GAAP facts nor invents class conversions.
    Removal requires another reviewed source/bridge version, never a date timer.
    """
    identity=assessment.get('method_identity',{})
    if identity.get('cik')!='0001326801':return None
    at=stamp(assessment.get('as_of'))
    if at and at<stamp('2026-10-21T20:03:35Z'):return None
    result=dict(status=INSUFFICIENT,rule_version='sec.reviewed-capital-structure-notice.v1',
        reason='KNOWN_CAPITAL_STRUCTURE_CHANGE_REQUIRES_ENTITLEMENT_BRIDGE',
        coverage='BOUNDED_REVIEWED_NOTICE_NOT_COMPLETE_CORPORATE_ACTION_SCREEN',evidence=[])
    try:
        from research_v2.sec_disclosures import verify_filing_envelope
        scope=assessment.get('quality_scope',{})
        if (scope.get('source_url')!='https://data.sec.gov/submissions/CIK0001326801.json'
                or scope.get('source_document_sha256')!=identity.get('submissions_sha256')):
            raise ValueError('CAPITAL_STRUCTURE_NOTICE_ISSUER_BINDING_CONFLICT')
        if len(notice_assets())!=len(NOTICE_FILES):raise ValueError('CAPITAL_STRUCTURE_NOTICE_SOURCE_MISSING')
        raw=(NOTICE_ROOT/NOTICE_FILES[0]).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=NOTICE_MANIFEST_SHA256:raise ValueError('CAPITAL_STRUCTURE_NOTICE_MANIFEST_HASH_MISMATCH')
        notice=json.loads(raw)
        if notice['cik']!=identity['cik'] or {f['path'] for f in notice['filings']}!=set(NOTICE_FILES[1:]):
            raise ValueError('CAPITAL_STRUCTURE_NOTICE_IDENTITY_MISMATCH')
        # Later retrieval cannot be represented as qualified at an older clock.
        if not at or any(stamp(f['fetched_at'])>at or stamp(f['filing']['published_at'])>at for f in notice['filings']):
            raise ValueError('CAPITAL_STRUCTURE_NOTICE_NOT_QUALIFIED_AT_DECISION_AT')
        for source in notice['filings']:
            with gzip.open(NOTICE_ROOT/source['path'],'rb') as stream:
                body=stream.read(8_000_001)
            if len(body)>8_000_000:raise ValueError('CAPITAL_STRUCTURE_NOTICE_SOURCE_TOO_LARGE')
            if hashlib.sha256(body).hexdigest()!=source['sha256']:raise ValueError('CAPITAL_STRUCTURE_NOTICE_SOURCE_HASH_MISMATCH')
            proof=verify_filing_envelope((body,source['fetched_at']),{'cik':identity['cik']},source['filing'])
            if not proof:raise ValueError('CAPITAL_STRUCTURE_NOTICE_SEC_ENVELOPE_REJECTED')
            result['evidence'].append(dict(**proof,accession=source['filing']['accession']))
        result.update(unresolved=notice['unresolved'],release_condition=notice['release_condition'],
            manifest_sha256=NOTICE_MANIFEST_SHA256)
    except (OSError,ValueError,KeyError,TypeError,EOFError,zlib.error) as error:
        # Known unresolved rights never silently become "no event" on damage.
        code=str(error)
        result.update(reason=code if type(error) is ValueError and code.isupper() and code.replace('_','').isalnum() and len(code)<=120 else 'CAPITAL_STRUCTURE_NOTICE_SOURCE_UNVERIFIABLE',evidence=[])
    return result


def applicability(assessment):
    scope = assessment.get('quality_scope', {})
    identity = assessment.get('method_identity', {})
    at = stamp(assessment.get('as_of'))
    def state(status, reason):
        return dict(status=status, reason=reason)
    missing = state(INSUFFICIENT, 'QUALIFIED_ISSUER_SIC_OR_LISTED_CLASS_EVIDENCE_MISSING')
    result = dict(rule_version=VERSION, quality_screen=dict(missing), earnings_model=dict(missing),
                  alternative_method=state(INSUFFICIENT, 'NO_IMPLEMENTED_QUALIFIED_ALTERNATIVE_METHOD'),
                  evidence=scope, listed_identity=identity,
                  scope='LIMITED_US_GAAP_NONFINANCIAL_SCREEN_NOT_FULL_ENTERPRISE_QUALITY',
                  limitations=['GAAP_NOT_NORMALIZED', 'MATERIAL_ONE_OFFS_AND_FORWARD_EXPECTATIONS_NOT_VERIFIED',
                               'BUSINESS_SEGMENTS_AND_LONG_TERM_DURABILITY_REQUIRE_SEPARATE_EVIDENCE'])
    try:
        sic = int(scope['sic'])
        fetched = stamp(scope.get('fetched_at'))
        if (not at or not fetched or fetched > at or not scope.get('source_document_sha256')
                or not scope.get('source_url', '').startswith('https://data.sec.gov/submissions/CIK')):
            return result
    except (ValueError, KeyError, TypeError):
        return result
    if scope.get('issuer_type') != 'domestic':
        result['quality_screen'] = state(NOT_APPLICABLE, 'US_GAAP_DOMESTIC_SCOPE_NOT_ESTABLISHED')
        return result
    if 6000 <= sic <= 6999:
        reason = 'FINANCIAL_INSURANCE_SCOPE_GAAP_INVESTMENT_GAINS_NOT_OPERATING_EARNINGS'
        result['quality_screen'] = state(NOT_APPLICABLE, reason)
        result['earnings_model'] = state(NOT_APPLICABLE, reason)
        result['alternative_method']['reason'] = 'OPERATING_EARNINGS_SEGMENTS_INVESTMENT_ASSETS_FLOAT_AND_CLASS_BRIDGE_REQUIRED'
        return result
    if not 1000 <= sic <= 8999 or 4900 <= sic <= 4999:
        result['quality_screen'] = state(NOT_APPLICABLE, 'OUTSIDE_BOUNDED_NONFINANCIAL_SCOPE')
        return result
    result['quality_screen'] = state(APPLICABLE, 'DOMESTIC_NONFINANCIAL_SIC_OBSERVED_WINDOW_ONLY')
    classes = identity.get('registered_tickers',identity.get('registered_equity_tickers', []))
    if (identity.get('symbol') != assessment.get('symbol') or identity.get('sec_ticker') not in classes
            or identity.get('sec_ticker') not in identity.get('submissions_tickers', [])
            or not identity.get('directory_sha256') or not identity.get('submissions_sha256')
            or identity.get('submissions_sha256') != scope['source_document_sha256']):
        return result
    # The SEC directory lists securities, not proven common classes. Multiple
    # registrations need a consumer class adapter; no inference from their count.
    if len(classes) != 1:
        result['earnings_model'] = state(INSUFFICIENT, 'MULTIPLE_REGISTERED_SECURITIES_REQUIRE_QUALIFIED_EPS_ADAPTER')
    else:
        result['earnings_model'] = state(APPLICABLE, 'POSITIVE_GAAP_LISTED_EPS_MODEL_CONDITIONAL_ON_INPUTS_NOT_FORECAST_FACT')
    notice=capital_structure_qualification(assessment)
    if notice:
        result['return_unit_qualification']=notice
        result['earnings_model']=state(INSUFFICIENT,notice['reason'])
    return result
