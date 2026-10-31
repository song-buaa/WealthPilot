"""Source-driven S3-C applicability; no source parsing or financial engine.

Issuer resolution proves identity, never conversion of EPS between share classes.
Only the existing earnings model is implemented. Other methods stay uncomputed.
"""
from backend.services.market_data.evidence import stamp

VERSION = 's3-c.method-applicability.v1'
APPLICABLE = 'APPLICABLE'
NOT_APPLICABLE = 'NOT_APPLICABLE'
INSUFFICIENT = 'INSUFFICIENT_EVIDENCE'
RETURN_BASIS = 'FIVE_YEAR_USD_LISTED_EQUITY_CASH_DIVIDENDS_NO_REINVESTMENT'


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
    classes = identity.get('registered_equity_tickers', [])
    if (identity.get('symbol') != assessment.get('symbol') or identity.get('sec_ticker') not in classes
            or identity.get('sec_ticker') not in identity.get('submissions_tickers', [])
            or not identity.get('directory_sha256') or not identity.get('submissions_sha256')
            or identity.get('submissions_sha256') != scope['source_document_sha256']):
        return result
    # Multiple official listed common classes require a separate, qualified
    # earnings-per-class bridge. A legal conversion ratio alone is insufficient.
    if len(classes) != 1:
        result['earnings_model'] = state(INSUFFICIENT, 'MULTIPLE_LISTED_CLASSES_REQUIRE_QUALIFIED_EPS_BRIDGE')
    else:
        result['earnings_model'] = state(APPLICABLE, 'POSITIVE_GAAP_LISTED_EPS_MODEL_CONDITIONAL_ON_INPUTS_NOT_FORECAST_FACT')
    return result
