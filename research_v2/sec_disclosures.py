"""Bounded SEC source adapter owned by research_v2, not a financial engine.

Official directory + Submissions establish identity/accessions. CompanyFacts
supplies only entity-wide standard concepts. Restricted filing originals supply
inline-XBRL (or the existing LI table layout); unsupported facts stay unknown.
No AI extraction, asset writes, TTM calculation, redirect or arbitrary URL input.
"""
from copy import deepcopy
from datetime import date, datetime, timezone, time as wall_time, timedelta
from functools import lru_cache
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import threading
import time
import urllib.request
import urllib.error
import ssl
import socket
import os
import logging
import http.client
from contextvars import ContextVar
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from backend.services.instruments.issuers import resolve_sec_issuer, sec_ticker_matches

DIRECTORY = 'https://www.sec.gov/files/company_tickers.json'
FORMS = {'10-K', '10-Q', '20-F', '6-K', '8-K'}
MAX_FILINGS = 4
# Explicit semantic mappings, not label similarity. Unsupported/custom concepts
# remain unknown. NetIncomeLoss is parent-attributable and must not silently
# corroborate AV's unspecified consolidation basis.
CONCEPTS = {
    ('us-gaap', 'RevenueFromContractWithCustomerExcludingAssessedTax'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'Revenues'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'SalesRevenueNet'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'ProfitLoss'): ('netIncome', 'consolidated_net_income'),
    ('us-gaap', 'OperatingIncomeLoss'): ('operatingIncome', 'operating_income'),
    ('us-gaap', 'EarningsPerShareDiluted'): ('dilutedEPSOrdinary', 'diluted_eps_ordinary'),
    ('us-gaap', 'NetIncomeLoss'): ('netIncome', 'net_income_attributable_parent'),
    ('us-gaap', 'NetCashProvidedByUsedInOperatingActivities'): ('operatingCashflow', 'operating_cash_flow'),
    ('us-gaap', 'PaymentsToAcquirePropertyPlantAndEquipment'): ('capitalExpenditures', 'positive_capital_cash_outflow'),
    ('ifrs-full', 'Revenue'): ('totalRevenue', 'total_revenue'),
    ('ifrs-full', 'ProfitLoss'): ('netIncome', 'consolidated_net_income'),
    ('ifrs-full', 'CashFlowsFromUsedInOperatingActivities'): ('operatingCashflow', 'operating_cash_flow'),
    ('ifrs-full', 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'): ('capitalExpenditures', 'positive_capital_cash_outflow'),
}
# Explicit opt-in quality facts, owned by this same SEC adapter. Base collection
# stays unchanged; instant/YTD observations never masquerade as quarter facts.
QUALITY_CONCEPTS = {
    ('us-gaap', 'CashAndCashEquivalentsAtCarryingValue'): ('quality_cash', 'cash_and_equivalents'),
    ('us-gaap', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'): ('quality_cash_including_restricted', 'cash_including_restricted'),
    ('us-gaap', 'Assets'): ('quality_assets', 'assets'),
    ('us-gaap', 'Liabilities'): ('quality_liabilities', 'liabilities'),
    ('us-gaap', 'AssetsCurrent'): ('quality_current_assets', 'current_assets'),
    ('us-gaap', 'LiabilitiesCurrent'): ('quality_current_liabilities', 'current_liabilities'),
    ('us-gaap', 'StockholdersEquity'): ('quality_equity', 'parent_equity'),
    ('us-gaap', 'LongTermDebtCurrent'): ('quality_current_long_term_debt', 'current_long_term_debt'),
    ('us-gaap', 'LongTermDebtNoncurrent'): ('quality_noncurrent_debt', 'noncurrent_debt'),
    ('us-gaap', 'DebtCurrent'): ('quality_current_debt', 'current_debt'),
    ('us-gaap', 'CommercialPaper'): ('quality_commercial_paper', 'commercial_paper'),
    ('us-gaap', 'OperatingLeaseLiability'): ('quality_operating_lease_liability', 'operating_lease_liability'),
    ('us-gaap', 'FinanceLeaseLiability'): ('quality_finance_lease_liability', 'finance_lease_liability'),
    ('us-gaap', 'CommonStockSharesOutstanding'): ('quality_shares', 'ordinary_shares'),
    ('us-gaap', 'WeightedAverageNumberOfDilutedSharesOutstanding'): ('quality_diluted_shares', 'weighted_diluted_shares'),
    ('us-gaap', 'ShareBasedCompensation'): ('quality_sbc', 'share_based_compensation'),
    ('us-gaap', 'PaymentsForRepurchaseOfCommonStock'): ('quality_repurchases', 'positive_repurchase_cash_outflow'),
    ('us-gaap', 'NetIncomeLoss'): ('quality_net_income', 'net_income_attributable_parent'),
    ('us-gaap', 'OperatingIncomeLoss'): ('quality_operating_income', 'operating_income'),
    ('us-gaap', 'RevenueFromContractWithCustomerExcludingAssessedTax'): ('quality_revenue', 'total_revenue'),
    ('us-gaap', 'Revenues'): ('quality_revenue', 'total_revenue'),
    ('us-gaap', 'SalesRevenueNet'): ('quality_revenue', 'total_revenue'),
    ('us-gaap', 'NetCashProvidedByUsedInOperatingActivities'): ('quality_cfo', 'operating_cash_flow'),
    ('us-gaap', 'PaymentsToAcquirePropertyPlantAndEquipment'): ('quality_capex', 'positive_capital_cash_outflow'),
    ('us-gaap', 'PaymentsToAcquireProductiveAssets'): ('quality_productive_asset_payments', 'positive_productive_asset_cash_outflow'),
    ('us-gaap', 'IncreaseDecreaseInAccountsReceivable'): ('quality_receivables_change', 'increase_decrease_receivables_as_reported'),
    ('us-gaap', 'IncreaseDecreaseInInventories'): ('quality_inventory_change', 'increase_decrease_inventory_as_reported'),
    ('us-gaap', 'IncreaseDecreaseInAccountsPayable'): ('quality_payables_change', 'increase_decrease_payables_as_reported'),
    ('us-gaap', 'IncomeTaxExpenseBenefit'): ('quality_tax', 'income_tax_expense'),
    ('us-gaap', 'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest'): ('quality_pretax_income', 'pretax_income'),
}
QUALITY_INSTANT = {'quality_finance_lease_liability', 'quality_operating_lease_liability', 'quality_current_debt', 'quality_current_assets', 'quality_current_long_term_debt', 'quality_shares', 'quality_cash_including_restricted', 'quality_commercial_paper', 'quality_noncurrent_debt', 'quality_equity', 'quality_cash', 'quality_current_liabilities', 'quality_assets', 'quality_liabilities'}

_request_lock = threading.Lock()
_last_request = 0.0
_diagnostics = ContextVar('sec_source_diagnostics', default=())
_LOG = logging.getLogger(__name__)
from research_v2.sec_identity import SEC_IDENTITY_KEYS, identity_string


def request_identity():
    """Operator-owned identity only; no invented default or email ownership claim."""
    return identity_string({key:os.getenv(key,'') for key in SEC_IDENTITY_KEYS})


def source_diagnostics():
    return [dict(row) for row in _diagnostics.get()]


def _diagnose(url, category, *, status=None, attempted=False):
    path=urlsplit(url).path if allowed_url(url) else ''
    endpoint=('directory' if url==DIRECTORY else 'submissions' if '/submissions/' in path
              else 'companyfacts' if '/companyfacts/' in path else 'filing' if path else 'rejected')
    row=dict(endpoint=endpoint,category=category,http_status=status,request_attempted=attempted)
    _diagnostics.set((*_diagnostics.get(),row)[-20:])
    # No exception strings, response bodies or operator identity in logs/metadata.
    _LOG.info('SEC source endpoint=%s category=%s http_status=%s request_attempted=%s',endpoint,category,status,attempted)


def _access_failed():
    rows=_diagnostics.get()
    return bool(rows and rows[-1]['category'] not in {'available','cache_hit','data_missing'})


def _transport_error(exc):
    reason=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(reason,ssl.SSLError): return 'tls_error'
    if isinstance(reason,socket.gaierror): return 'dns_error'
    if isinstance(reason,(TimeoutError,socket.timeout)): return 'timeout'
    if isinstance(reason,ConnectionError) and any(os.getenv(k) for k in ('HTTPS_PROXY','https_proxy')):
        return 'proxy_connection_error'
    return 'network_error'



def allowed_url(url):
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
            return False
        if url == DIRECTORY:
            return True
        if p.hostname == 'data.sec.gov':
            return bool(re.fullmatch(r'/submissions/CIK\d{10}(?:-submissions-\d{3})?\.json', p.path)
                or re.fullmatch(r'/api/xbrl/companyfacts/CIK\d{10}\.json', p.path))
        return p.hostname == 'www.sec.gov' and bool(re.fullmatch(
            r'/Archives/edgar/data/[1-9]\d{0,9}/\d{18}/[A-Za-z0-9][A-Za-z0-9_-]*\.(?:htm|html|xml|txt)', p.path))
    except (TypeError, ValueError):
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(url):
    """SEC public reads only, max 4/s per process, no retries or redirects."""
    global _last_request
    if not allowed_url(url):
        _diagnose(url,'url_rejected')
        return None
    identity=request_identity()
    if not identity:
        _diagnose(url,'identity_unconfigured')
        return None
    try:
        with _request_lock:
            time.sleep(max(0, .25 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
            req = urllib.request.Request(url, headers={
                'User-Agent': identity,
                'Accept': 'application/json,text/html,text/plain'})
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=8) as response:
                if response.geturl() != url:
                    _diagnose(url,'redirect_rejected',attempted=True)
                    return None
                status=getattr(response,'status',200)
                if status!=200:
                    _diagnose(url,'http_error',status=status,attempted=True)
                    return None
                body = response.read()
        if not body:
            _diagnose(url,'empty_response',attempted=True)
            return None
        _diagnose(url,'available',status=200,attempted=True)
        return body, datetime.now(timezone.utc).isoformat()
    except urllib.error.HTTPError as exc:
        _diagnose(url,'http_403' if exc.code==403 else 'http_error',status=exc.code,attempted=True)
    except (urllib.error.URLError,OSError,http.client.HTTPException) as exc:
        _diagnose(url,_transport_error(exc),attempted=True)
    return None


def _json(source):
    try:
        value = json.loads(source[0]) if source else None
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError):
        return None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('DATE_UNKNOWN')
    return date.fromisoformat(value)


@lru_cache(maxsize=32)
def _sec_holidays(year):
    # Existing pandas dependency; not the NYSE session calendar. Unexpected SEC
    # closures are not inferred: an inconsistent official next date is refused.
    from pandas.tseries.holiday import USFederalHolidayCalendar
    return frozenset(USFederalHolidayCalendar().holidays(
        start=f"{year}-01-01", end=f"{year}-12-31").date)


def _sec_business_day(day):
    return day.weekday() < 5 and day not in _sec_holidays(day.year)


def _filing_clock(filed, accepted=None):
    """Official filing date + UTC acceptance, conservative when uncertain."""
    if not _sec_business_day(filed):
        return None
    upper = datetime.combine(filed, datetime.max.time(), ZoneInfo('America/New_York')).isoformat()
    if accepted is None:
        return dict(published_at=upper, accepted_at=None,
                    publication_clock_basis='CONSERVATIVE_FILED_DAY_END_MISSING_ACCEPTANCE')
    if not isinstance(accepted,str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)',accepted):
        return None
    clock = datetime.fromisoformat(accepted.replace('Z','+00:00'))
    eastern = clock.astimezone(ZoneInfo('America/New_York'))
    if not _sec_business_day(eastern.date()):
        return None
    if eastern.date() == filed:
        return dict(published_at=clock.isoformat(), accepted_at=clock.isoformat(),
                    publication_clock_basis='EXACT_SUBMISSIONS_ACCEPTANCE')
    following = eastern.date()
    for _ in range(4):
        following += timedelta(days=1)
        if _sec_business_day(following):
            break
    if (following != filed or not 0 < (filed-eastern.date()).days <= 4
            or not wall_time(17,30) < eastern.time() < wall_time(22)):
        return None
    return dict(published_at=upper, accepted_at=clock.isoformat(),
                publication_clock_basis='CONSERVATIVE_FILED_DAY_END_LATE_ACCEPTANCE')


def discover_filings(issuer, submissions):
    """Only exact accession/path/clock metadata from the resolved CIK."""
    if str(submissions.get('cik', '')).zfill(10) != issuer['cik']:
        return []
    filings=submissions.get('filings')
    recent=filings.get('recent') if isinstance(filings,dict) else None
    if not isinstance(recent,dict):
        return []
    required = ('accessionNumber', 'form', 'filingDate', 'reportDate', 'primaryDocument')
    if any(not isinstance(recent.get(k), list) for k in required):
        return []
    if len({len(recent[k]) for k in required}) != 1:
        return []
    rows = []
    for i, accession in enumerate(recent['accessionNumber']):
        try:
            form, document = recent['form'][i], recent['primaryDocument'][i]
            if form not in FORMS or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                continue
            if ((issuer['issuer_type'] == 'domestic' and form not in {'10-K', '10-Q', '8-K'})
                    or (issuer['issuer_type'] == 'foreign_private' and form not in {'20-F', '6-K'})):
                continue
            filed = _day(recent['filingDate'][i])
            # 6-K/8-K report dates describe the event, not necessarily the fiscal
            # quarter. Do not use these to assign a statement period.
            report = recent['reportDate'][i]
            if report:
                _day(report)
            base = f"https://www.sec.gov/Archives/edgar/data/{int(issuer['cik'])}/{accession.replace('-', '')}/"
            if not isinstance(document, str) or not allowed_url(base + document):
                continue
            accepted = recent.get('acceptanceDateTime', [])
            accepted = accepted[i] if isinstance(accepted,list) and len(accepted) == len(recent['form']) else None
            qualification = _filing_clock(filed, accepted)
            if not qualification:
                continue
            published = qualification['published_at']
            locator = (f'Submissions/filings/recent/acceptanceDateTime[{i}]'
                       if qualification['publication_clock_basis']=='EXACT_SUBMISSIONS_ACCEPTANCE' else
                       f'Submissions/filings/recent/filingDate[{i}] '
                       f"(CONSERVATIVE_FILED_DAY_END_NOT_OBSERVED_PUBLICATION_TIME; acceptanceDateTime[{i}]={qualification['accepted_at']})")
            rows.append(dict(accession=accession, form=form, filed=filed.isoformat(), report_date=report,
                **qualification, publication_locator=locator, primary_url=base + document,
                full_url=base + accession + '.txt'))
        except (TypeError, ValueError, IndexError):
            continue
    # Deterministic latest filings, not caller candidates or arbitrary URLs.
    return sorted(rows, key=lambda r: (r['filed'], r['accession']), reverse=True)


def _period(start, end):
    days = (_day(end) - _day(start)).days + 1
    if 70 <= days <= 105:
        return 'quarterly'
    if 350 <= days <= 380:
        return 'annual'
    return None  # YTD/mixed/instant contexts are never quarters.


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _deduplicate(observations):
    groups = {}
    for obs in observations:
        key = (obs['source_field'], obs['fiscal_period_start'], obs['fiscal_period_end'], obs['currency'], obs['accounting_basis'])
        groups.setdefault(key, []).append(obs)
    result = []
    for rows in groups.values():
        # Competing standard tags/contexts with differing values are ambiguous.
        if len({Decimal(str(r['value'])) for r in rows}) == 1:
            row = rows[0]
            row['source_locator'] = '; '.join(r['source_locator'] for r in rows)
            result.append(row)
    return result


def _record(issuer, filing, observations, body, url, fetched_at, *, identity_digest):
    # Issuer identity does not qualify per-listed-class earnings. In particular,
    # a directory alias must not turn a group's Class A EPS into Class B EPS.
    # A later qualified class bridge can extend this Owner; no conversion here.
    registered=issuer.get('registered_tickers',issuer.get('registered_equity_tickers', []))
    multiple=issuer.get('issuer_type')=='domestic' and len(registered)>1
    if multiple:
        observations = [o for o in observations if o.get('source_field') != 'dilutedEPSOrdinary']
        if url==filing.get('full_url'):
            observations += _direct_listed_class_eps(body,issuer,filing,fetched_at)
    if not observations or not isinstance(fetched_at,str):
        return None
    try:
        fetched = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
        published = datetime.fromisoformat(filing['published_at'].replace('Z', '+00:00'))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)  # date-only remains date-only in output
        if fetched.tzinfo is None or published > fetched or _day(filing['filed']) > fetched.date():
            return None
    except (ValueError, TypeError):
        return None
    digest = hashlib.sha256(body).hexdigest()
    result = dict(symbol=issuer['symbol'], source='official_disclosure', source_url=url,
        record_id='official:sec:'+issuer['cik']+':'+filing['accession']+':'+digest+(':listed:'+issuer['sec_ticker'] if multiple else ''),
        published_at=filing['published_at'], data_as_of=filing['published_at'], fetched_at=fetched_at,
        period=max(o['fiscal_period_end'] for o in observations),
        official_disclosure=dict(issuer_id=issuer['issuer_id'], source_url=url, cik=issuer['cik'],
                                 accession=filing['accession'], form=filing['form']),
        financial_observations=_deduplicate(observations),
        content='Independently sourced SEC statement facts; fiscal and filing clocks remain separate.',
        source_verification=dict(document_sha256=digest, publication_locator=filing['publication_locator'],
            issuer_identity=deepcopy(issuer), identity_document_sha256=identity_digest))


    if filing.get('publication_clock_basis') not in {None,'EXACT_SUBMISSIONS_ACCEPTANCE'}:
        result['source_verification'].update(publication_clock_basis=filing['publication_clock_basis'],
            accepted_at=filing.get('accepted_at'),official_filed_date=filing['filed'])
    return result


def parse_companyfacts(source, issuer, filings, *, identity_digest, quality=False):
    """Select actual duration/currency/accession facts, no fiscal-frame guesses."""
    data = _json(source)
    if not data or str(data.get('cik', '')).zfill(10) != issuer['cik']:
        return []
    all_facts=data.get('facts')
    if not isinstance(all_facts,dict):
        return []
    by_accession={f['accession']:f for f in filings}
    by_filing={}
    for (taxonomy, concept), (field, basis) in (QUALITY_CONCEPTS if quality else CONCEPTS).items():
        concepts=all_facts.get(taxonomy)
        definition=concepts.get(concept) if isinstance(concepts,dict) else None
        units=definition.get('units') if isinstance(definition,dict) else None
        if not isinstance(units,dict):
            continue
        for original_unit, rows in units.items():
            currency=original_unit.removesuffix('/shares')
            if quality and original_unit != ('shares' if field in {'quality_shares','quality_diluted_shares'} else 'USD'):
                continue
            if not quality and (field=='dilutedEPSOrdinary') != original_unit.endswith('/shares'):
                continue
            if (currency not in {'USD', 'CNY', 'HKD'} and not (quality and original_unit=='shares')) or not isinstance(rows, list):
                continue
            for i, row in enumerate(rows):
                if not isinstance(row,dict):
                    continue
                try:
                    filing=by_accession.get(row.get('accn'))
                    if not filing or row.get('form')!=filing['form'] or row.get('filed')!=filing['filed']:
                        continue
                    scope=_period(row.get('start'),row.get('end')) if row.get('start') else None
                    if quality and field in QUALITY_INSTANT and not row.get('start'):
                        scope='instant'
                    elif quality and row.get('start') and not scope and 105 < (_day(row['end'])-_day(row['start'])).days < 350:
                        scope='year_to_date'
                    if not scope or _day(row['end'])>_day(filing['filed']):
                        continue
                    if filing['form'] in {'10-Q','10-K','20-F'} and row['end']>filing['report_date']:
                        continue
                    value=_finite(row.get('val'))
                    if value is None or abs(value)>Decimal('1e20') or (field in {'capitalExpenditures','quality_capex','quality_productive_asset_payments','quality_sbc','quality_repurchases'} and value<0):
                        continue
                    by_filing.setdefault(filing['accession'],[]).append(dict(source_field=field,value=float(value),
                        currency=None if original_unit=='shares' else currency,unit='shares' if original_unit=='shares' else 'currency_per_ordinary_share' if field=='dilutedEPSOrdinary' else 'currency_units',unit_scale=1,period_basis=scope,
                        fiscal_period_start=row.get('start'),fiscal_period_end=row['end'],accounting_basis=basis,
                        source_locator=f'/facts/{taxonomy}/{concept}/units/{original_unit.replace("/","~1")}/{i} (accn={filing["accession"]})'))
                except (ValueError,TypeError,KeyError):
                    continue
    records=[]
    # Event-only filings cannot crowd out older financial statements. Each fact
    # is scanned once, rather than re-reading all facts for every Submissions row.
    for filing in filings:
        record=_record(issuer,filing,by_filing.get(filing['accession'],[]),source[0],
            'https://data.sec.gov/api/xbrl/companyfacts/CIK'+issuer['cik']+'.json',source[1],identity_digest=identity_digest)
        if record and record['financial_observations']:
            records.append(record)
            if len(records)==MAX_FILINGS:
                break
    return records


def _namespace(node, prefix):
    for parent in [node, *node.parents]:
        value=parent.get('xmlns:'+prefix) if hasattr(parent,'get') else None
        if value:
            return value
    return None


def _standard_concept(node):
    name=node.get('name','')
    if ':' not in name:
        return None
    prefix,concept=name.split(':',1)
    namespace=_namespace(node,prefix)
    if namespace and re.fullmatch(r'https?://fasb.org/us-gaap/20[0-9]{2}',namespace):
        return CONCEPTS.get(('us-gaap',concept))
    if namespace and re.fullmatch(r'https?://xbrl.ifrs.org/taxonomy/20[0-9]{2}-[0-9]{2}-[0-9]{2}/ifrs-full',namespace):
        return CONCEPTS.get(('ifrs-full',concept))
    return None


def _direct_listed_class_eps(body, issuer, filing, fetched_at):
    """Direct primary-document class EPS only; no class/value conversion.

    Cover DEI binds ticker to a legal stock title and class context. A class-axis
    EPS must match that member or a standard CommonClassXMember matching the
    unique cover Class X title. Unsegmented/company-wide EPS is never a bridge.
    """
    if issuer.get('issuer_type')!='domestic' or filing.get('form') not in {'10-Q','10-K'}:
        return []
    if not verify_filing_envelope((body,fetched_at),issuer,filing):
        return []
    documents=[]
    for i,doc in enumerate(re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>',body.decode('utf-8'),re.S|re.I)):
        kind=re.search(r'<TYPE>([^\r\n<]+)',doc,re.I)
        filename=re.search(r'<FILENAME>([^\r\n<]+)',doc,re.I)
        sequence=re.search(r'<SEQUENCE>(\d+)',doc,re.I)
        if not kind or not filename or not sequence:
            return []
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',filename[1].strip()):return []
        documents.append((i,kind[1].strip(),filename[1].strip(),int(sequence[1]),doc))
    if len({r[2] for r in documents})!=len(documents) or len({r[3] for r in documents})!=len(documents):
        return []
    target=filing['primary_url'].rsplit('/',1)[-1]
    primary=[r for r in documents if r[1]==filing['form'] and r[2]==target and r[3]==1]
    if len(primary)!=1 or not allowed_url(filing['primary_url']) or not target.lower().endswith(('.htm','.html')):
        return []
    index,_,_,_,doc=primary[0]
    soup=BeautifulSoup(doc,'html.parser')
    def local(node,name):return node.name.lower().split(':')[-1]==name
    def qname(node,value):
        if not isinstance(value,str) or ':' not in value:return None
        prefix,name=value.split(':',1)
        return _namespace(node,prefix),name
    def gaap(q):return bool(q and re.fullmatch(r'https?://fasb.org/us-gaap/20\d{2}',q[0] or ''))
    def dei(q):return bool(q and re.fullmatch(r'https?://xbrl.sec.gov/dei/20\d{2}(?:q[1-4])?',q[0] or ''))
    def inline(node):
        name=qname(node,node.name)
        return bool(name and name[0] in {'http://www.xbrl.org/2013/inlineXBRL','http://www.xbrl.org/2008/inlineXBRL'})
    def invalid_nil(node):
        return any(qname(node,key)!=('http://www.w3.org/2001/XMLSchema-instance','nil')
                   or value not in {'false','0'}
                   for key,value in node.attrs.items() if key.split(':')[-1]=='nil')
    def xchild(parent,name):
        if parent is None:return None
        nodes=parent.find_all(lambda t:local(t,name),recursive=False)
        return nodes[0] if len(nodes)==1 and qname(nodes[0],nodes[0].name)==('http://www.xbrl.org/2003/instance',name) else None
    contexts={}
    for node in soup.find_all(lambda t:local(t,'context')):
        key=node.get('id')
        if not isinstance(key,str) or not key:continue
        if key in contexts:contexts[key]=None;continue
        contexts[key]=None
        if qname(node,node.name)!=('http://www.xbrl.org/2003/instance','context'):continue
        entity=xchild(node,'entity');identifier=xchild(entity,'identifier')
        segment=xchild(entity,'segment');period=xchild(node,'period')
        members=segment.find_all(recursive=False) if segment else []
        if (not identifier or identifier.find(recursive=False) or not period
                or identifier.get('scheme') not in {'http://www.sec.gov/CIK','https://www.sec.gov/CIK'}
                or not re.fullmatch(r'0*'+str(int(issuer['cik'])),identifier.get_text(strip=True))
                or node.find(lambda t:local(t,'scenario') or local(t,'typedmember'))
                or len(members)!=1
                or qname(members[0],members[0].name)!=('http://xbrl.org/2006/xbrldi','explicitmember')):continue
        axis=qname(members[0],members[0].get('dimension'))
        member=qname(members[0],members[0].get_text(strip=True))
        if not gaap(axis) or axis[1]!='StatementClassOfStockAxis' or not member or not member[0]:continue
        start=xchild(period,'startdate');end=xchild(period,'enddate');instant=xchild(period,'instant')
        children=period.find_all(recursive=False)
        if not ((start and end and not instant and len(children)==2) or (instant and not start and not end and len(children)==1)):continue
        try:
            start_value=start.get_text(strip=True) if start else None
            end_value=(end or instant).get_text(strip=True)
            if start_value and _day(start_value)>_day(end_value):continue
            if _day(end_value)>_day(filing['filed']):continue
        except (TypeError,ValueError):continue
        contexts[key]=dict(member=member,start=start_value,end=end_value)
    symbols,titles={},{}
    for node in soup.find_all(lambda t:t.name.lower()=='ix:nonnumeric'):
        concept=qname(node,node.get('name'));ctx=node.get('contextref')
        if (not inline(node) or not dei(concept) or not contexts.get(ctx)
                or invalid_nil(node) or node.get('format') is not None):continue
        value=' '.join(node.get_text(' ',strip=True).split())
        if concept[1]=='TradingSymbol':symbols.setdefault(ctx,set()).add(value)
        elif concept[1]=='Security12bTitle':titles.setdefault(ctx,set()).add(value)
    bindings=[]
    for ctx,names in symbols.items():
        if len(names)!=1 or len(titles.get(ctx,set()))!=1:continue
        ticker=next(iter(names));title=next(iter(titles[ctx]));context=contexts[ctx]
        try:
            if not _day(filing['report_date'])<=_day(context['end'])<=_day(filing['filed']):continue
        except (TypeError,ValueError):continue
        if re.search(r'\b(?:preferred|preference|depositary|depository|ADS|ADR|notes?|bonds?|warrants?|units?|debentures?|convertible)\b',title,re.I):continue
        # This is a source title restriction, not an inference from a ticker.
        match=re.fullmatch(r'(?:Class\s+([A-Z0-9]+)\s+)?(?:Common|Capital)\s+(?:Stock|Shares)(?:\s*,.*)?',title,re.I)
        if not match or ticker not in issuer.get('registered_tickers',issuer.get('registered_equity_tickers',[])):continue
        label=match[1].upper() if match[1] else None
        standard=re.fullmatch(r'CommonClass([A-Z0-9]+)Member',context['member'][1]) if gaap(context['member']) else None
        if gaap(context['member']) and not standard and context['member'][1]!='CommonStockMember':continue
        if standard and standard[1]!=label:continue
        bindings.append(dict(ticker=ticker,label=label,member=context['member'],context=ctx,title=title))
    listed=issuer.get('sec_ticker')
    candidates=[b for b in bindings if b['ticker']==listed]
    if len({(b['label'],b['member'],b['title']) for b in candidates})!=1:return []
    binding=candidates[0]
    # A class label/member cannot be bound to conflicting listed securities.
    if any(b['ticker']!=listed and (b['member']==binding['member'] or (binding['label'] and b['label']==binding['label'])) for b in bindings):return []
    units={}
    for node in soup.find_all(lambda t:local(t,'unit')):
        key=node.get('id')
        if not isinstance(key,str) or not key:continue
        if key in units:units[key]=False;continue
        divide=xchild(node,'divide')
        numerator=xchild(divide,'unitnumerator');denominator=xchild(divide,'unitdenominator')
        n=xchild(numerator,'measure');d=xchild(denominator,'measure')
        units[key]=bool(qname(node,node.name)==('http://www.xbrl.org/2003/instance','unit')
            and divide and len(node.find_all(recursive=False))==1
            and len(divide.find_all(recursive=False))==2 and n and d
            and len(numerator.find_all(recursive=False))==len(denominator.find_all(recursive=False))==1
            and not n.find(recursive=False) and not d.find(recursive=False)
            and qname(n,n.get_text(strip=True))==('http://www.xbrl.org/2003/iso4217','USD')
            and qname(d,d.get_text(strip=True))==('http://www.xbrl.org/2003/instance','shares'))
    observations=[]
    for i,node in enumerate(soup.find_all(lambda t:t.name.lower()=='ix:nonfraction')):
        concept=qname(node,node.get('name'));context=contexts.get(node.get('contextref'))
        if (not inline(node) or not gaap(concept) or concept[1]!='EarningsPerShareDiluted' or not context
                or not units.get(node.get('unitref')) or invalid_nil(node)
                or node.get('format') is not None
                or node.get('scale','0')!='0' or node.get('sign') not in {None,'-'}):continue
        member=context['member'];standard=re.fullmatch(r'CommonClass([A-Z0-9]+)Member',member[1]) if gaap(member) else None
        if member!=binding['member'] and not (standard and binding['label']==standard[1]):continue
        try:
            start,end=context['start'],context['end'];scope=_period(start,end)
            if not scope or _day(end)>_day(filing['report_date']):continue
            literal=node.get_text().strip()
            if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',literal):continue
            value=_finite(literal)
            if value is None or abs(value)>Decimal('1e6'):continue
            if node.get('sign')=='-':
                if value<0:continue
                value=-value
        except (ValueError,TypeError,KeyError):continue
        locator=f'DOCUMENT[{index}]/{target}/ix:nonFraction[{i}]/{node.get("name")}'
        observations.append(dict(source_field='dilutedEPSOrdinary',value=float(value),currency='USD',
            unit='currency_per_ordinary_share',unit_scale=1,period_basis=scope,fiscal_period_start=start,
            fiscal_period_end=end,accounting_basis='diluted_eps_ordinary',document_type=filing['form'],
            document_filename=target,document_sequence=1,source_locator=locator,
            listed_class_proof=dict(basis='DIRECT_OFFICIAL_CLASS_EPS_NOT_CONVERSION',ticker=listed,
                symbol=issuer['symbol'],class_label=binding['label'],cover_member=list(binding['member']),
                earnings_member=list(member),cover_context=binding['context'],earnings_context=node.get('contextref'),
                unit=node.get('unitref'),security_title=binding['title'],document_sha256=hashlib.sha256(body).hexdigest())))
    return observations


def _declared_exhibit(cover, filename, kind):
    """Accept an exact cover declaration in the link or its own table row."""
    number = re.escape(kind[3:])
    for link in cover.find_all('a', href=filename):
        label = ' '.join(link.get_text(' ', strip=True).split())
        if re.fullmatch(r'(?:Exhibit\s+)?'+number+r'(?:(?:\s+|\s*[-–—:]\s*)[^<>]{1,120})?', label, re.I):
            return True
        row = link.find_parent('tr')
        if row is None:
            continue
        cells = row.find_all(['td', 'th'], recursive=False)
        link_cells = [cell for cell in cells if link in cell.descendants]
        # A different row, an ambiguous multi-link row, or a label in the link
        # cell cannot identify this document as the declared exhibit.
        if len(link_cells) != 1 or len(row.find_all('a', href=True)) != 1:
            continue
        for cell in cells:
            if cell is link_cells[0]:
                continue
            sibling_label = ' '.join(cell.get_text(' ', strip=True).split())
            if re.fullmatch(r'(?:Exhibit\s+)?'+number, sibling_label, re.I):
                return True
    return False


def _domestic_fiscal_eps_context(rows, issuer):
    """Prove a fiscal Q4 start using distinct SEC Q3 and annual EPS periods."""
    if issuer['issuer_type']!='domestic':
        return {}
    annuals=[];quarters=[]
    for record in rows:
        if record.get('official_disclosure',{}).get('cik')!=issuer['cik']:
            continue
        for obs in record.get('financial_observations',[]):
            if (obs.get('source_field')!='dilutedEPSOrdinary' or obs.get('currency')!='USD'
                    or obs.get('accounting_basis')!='diluted_eps_ordinary'
                    or obs.get('unit')!='currency_per_ordinary_share'
                    or obs.get('unit_scale')!=1):
                continue
            item=(obs,record['record_id'])
            if obs.get('period_basis')=='annual':
                annuals.append(item)
            elif obs.get('period_basis')=='quarterly':
                quarters.append(item)
    context={};conflicting_ends=set()
    for annual,annual_ref in annuals:
        try:
            end=_day(annual['fiscal_period_end']);year_start=_day(annual['fiscal_period_start'])
            previous=[(obs,ref) for obs,ref in quarters
                      if year_start<=_day(obs['fiscal_period_start'])<=_day(obs['fiscal_period_end'])<end]
            if not previous:
                continue
            previous_end=max(_day(obs['fiscal_period_end']) for obs,_ in previous)
            matches=[(obs,ref) for obs,ref in previous if _day(obs['fiscal_period_end'])==previous_end]
            start=date.fromordinal(previous_end.toordinal()+1)
            if (len({(obs['fiscal_period_start'],obs['fiscal_period_end'],Decimal(str(obs['value']))) for obs,_ in matches})!=1
                    or not 70<=(end-start).days+1<=105 or not year_start<start):
                continue
            proof_records=[record for record in rows
                           if record['record_id'] in {annual_ref,matches[0][1]}]
            if len(proof_records)!=2:
                continue
            proof_times=[datetime.fromisoformat(record['published_at'].replace('Z','+00:00'))
                         for record in proof_records]
            if any(value.tzinfo is None for value in proof_times):
                continue
            proof_at=max(proof_times)
            candidate=dict(start=start.isoformat(),annual_eps=annual['value'],
                           annual_ref=annual_ref,prior_ref=matches[0][1],
                           proof_available_at=proof_at.astimezone(timezone.utc).isoformat())
            end_key=end.isoformat()
            if end_key in conflicting_ends:
                continue
            if end_key in context and context[end_key]!=candidate:
                context.pop(end_key,None)
                conflicting_ends.add(end_key)
            else:
                context[end_key]=candidate
        except (KeyError,TypeError,ValueError,InvalidOperation):
            continue
    return context


def parse_filing(source, issuer, filing, *, identity_digest, fiscal_eps_context=None):
    """Restricted full-submission envelope and inline XBRL, including exhibits.

    Never fetch an attachment named by untrusted HTML. The SEC full submission
    contains its original documents. All inline facts require an exact CIK context,
    explicit duration/unit/concept. Unsupported non-XBRL text stays unknown.
    """
    if not source or not allowed_url(filing['full_url']):
        return None
    body, fetched_at = source
    if not isinstance(body, bytes):
        return None
    try:
        text = body.decode('utf-8', errors='strict')
    except UnicodeError:
        return None
    if not _header_clock_matches(text, issuer, filing):
        return None
    base=f"https://www.sec.gov/Archives/edgar/data/{int(issuer['cik'])}/{filing['accession'].replace('-','')}/"
    if filing['full_url']!=base+filing['accession']+'.txt' or not filing['primary_url'].startswith(base):
        return None
    documents=[]
    for index,document in enumerate(re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>',text,re.S|re.I)):
        kind=re.search(r'<TYPE>([^\r\n<]+)',document,re.I)
        filename=re.search(r'<FILENAME>([^\r\n<]+)',document,re.I)
        sequence=re.search(r'<SEQUENCE>(\d+)',document,re.I)
        if not kind or not filename or not sequence:
            return None
        kind,filename=kind[1].strip(),filename[1].strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',filename):
            return None
        documents.append((index,kind,filename,int(sequence[1]),document))
    if len({d[2] for d in documents})!=len(documents) or len({d[3] for d in documents})!=len(documents):
        return None
    primary=[d for d in documents if d[1]==filing['form'] and base+d[2]==filing['primary_url'] and d[3]==1]
    if len(primary)!=1:
        return None
    cover=BeautifulSoup(primary[0][4],'html.parser')
    observations=[]
    statement_issues=[]
    # Contexts and external statement headings are document-scoped. An exhibit
    # must be declared by the original primary document with its actual identity.
    for doc_index,kind,filename,sequence,document in documents:
        if not filename.lower().endswith(('.htm','.html')):
            continue
        is_primary=(kind==filing['form'] and base+filename==filing['primary_url'])
        is_exhibit=(re.fullmatch(r'EX-99\.\d+',kind) and _declared_exhibit(cover,filename,kind))
        if not (is_primary or is_exhibit):
            continue
        soup=BeautifulSoup(document,'html.parser')
        from research_v2.official_disclosures import parse_statement_tables, parse_vehicle_operating, parse_us_gaap_diluted_eps
        try:
            table_observations=parse_statement_tables(soup,filing['published_at'],require_statements=True,currencies=('CNY','USD','HKD'),diagnostics=statement_issues)
            if is_exhibit and filing['form']=='8-K' and issuer['issuer_type']=='domestic' and fiscal_eps_context:
                table_observations += parse_us_gaap_diluted_eps(soup,filing['published_at'],fiscal_eps_context)
            if is_exhibit and issuer['issuer_id'] in {'LI_AUTO', 'XPENG'}:
                operating = parse_vehicle_operating(soup,filing['published_at'])
                for summary in operating:
                    if summary['source_field'] != 'vehicleSales':
                        table_observations.append(summary)
                        continue
                    exact = [row for row in table_observations if row['source_field'] == 'vehicleSales'
                             and row['fiscal_period_start'] == summary['fiscal_period_start']
                             and row['fiscal_period_end'] == summary['fiscal_period_end']
                             and row['currency'] == summary['currency']
                             and row['accounting_basis'] == summary['accounting_basis']]
                    if len(exact) != 1:
                        table_observations.append(summary)
                        continue
                    row = exact[0]
                    q = Decimal(str(summary['reporting_quantum']))
                    reported = Decimal(str(row['value'])) * Decimal(str(row['unit_scale']))
                    if (reported / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q != Decimal(str(summary['value'])):
                        table_observations.append(summary)  # Real disagreement remains visible to Evidence.
                        continue
                    row['precision_corroboration'] = dict(summary_value=summary['value'],
                        summary_quantum=summary['reporting_quantum'],
                        summary_locator=summary['source_locator'],
                        relation='same_exhibit_rounded_summary')
                    row['source_locator'] += '; ' + summary['source_locator']
        except (ValueError,TypeError,AttributeError,InvalidOperation,OverflowError):
            return None
        for obs in table_observations:
            end=date.fromisoformat(obs['fiscal_period_end'])
            if not obs.get('fiscal_period_start'):
                obs['fiscal_period_start']=end.replace(day=1,month=end.month-2).isoformat()
            obs.update(document_type=kind,document_filename=filename,document_sequence=sequence)
            obs['source_locator']=f'DOCUMENT[{doc_index}]/{filename}/'+obs['source_locator']
            observations.append(obs)
        contexts, units = {}, {}
        for node in soup.find_all(lambda t: t.name.lower().split(':')[-1] == 'context'):
            identifier = node.find(lambda t: t.name.lower().split(':')[-1] == 'identifier')
            start = node.find(lambda t: t.name.lower().split(':')[-1] == 'startdate')
            end = node.find(lambda t: t.name.lower().split(':')[-1] == 'enddate')
            if (not identifier or not re.fullmatch(r'0*'+str(int(issuer['cik'])), identifier.get_text(strip=True))
                    or identifier.get('scheme') not in {'http://www.sec.gov/CIK', 'https://www.sec.gov/CIK'}
                    or node.find(lambda t: t.name.lower().split(':')[-1] in {'segment', 'scenario'}) or not start or not end):
                continue
            contexts[node.get('id')] = None if node.get('id') in contexts else (start.get_text(strip=True), end.get_text(strip=True))
        for node in soup.find_all(lambda t: t.name.lower().split(':')[-1] == 'unit'):
            measures = node.find_all(lambda t: t.name.lower().split(':')[-1] == 'measure')
            if len(measures) == 1 and measures[0].get_text(strip=True) in {'iso4217:USD', 'iso4217:CNY', 'iso4217:HKD'}:
                units[node.get('id')] = None if node.get('id') in units else measures[0].get_text(strip=True).split(':')[1]
        for i, node in enumerate(soup.find_all(lambda t: t.name.lower() == 'ix:nonfraction')):
            try:
                mapped = _standard_concept(node)
                period = contexts.get(node.get('contextref')); currency = units.get(node.get('unitref'))
                if not mapped or not period or not currency or node.get('xsi:nil') == 'true' or node.get('format') not in {None, 'ixt:num-dot-decimal', 'ixt:numdotdecimal'}:
                    continue
                start, end = period; scope = _period(start, end)
                if not scope or _day(end) > _day(filing['filed']):
                    continue
                if filing['form'] in {'10-Q', '10-K', '20-F'} and end != filing['report_date']:
                    continue
                amount = re.sub(r'\s+', '', node.get_text()).replace(',', '')
                value = _finite(amount)
                scale = int(node.get('scale', '0'))
                if value is None or abs(value)>Decimal('1e20') or scale not in {0, 3, 6, 9} or node.get('sign') not in {None, '-'}:
                    continue
                value *= Decimal(10) ** scale
                if node.get('sign') == '-':
                    if value<0:
                        continue
                    value = -value
                field, basis = mapped
                if field == 'capitalExpenditures' and value < 0:
                    continue  # unsigned payment concept only; no blanket abs()
                observations.append(dict(source_field=field, value=float(value), currency=currency,
                    unit='currency_units', unit_scale=1, period_basis=scope,
                    fiscal_period_start=start, fiscal_period_end=end, accounting_basis=basis,
                    document_type=kind,document_filename=filename,document_sequence=sequence,
                    source_locator=f'DOCUMENT[{doc_index}]/{filename}/ix:nonFraction[{i}]/{node.get("name")}'))
            except (ValueError, TypeError, InvalidOperation):
                continue
    record=_record(issuer, filing, observations, body, filing['full_url'], fetched_at, identity_digest=identity_digest)
    if record:
        record['financial_observation_issues']=statement_issues
    return record


def _header_clock_matches(text, issuer, filing):
    """Corroborate real header acceptance separately from qualification bound."""
    try:
        header = re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>', text, re.S | re.I)
        if not header:
            return False
        cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', header[1])
        form = re.search(r'CONFORMED SUBMISSION TYPE:\s*([^\n<]+)', header[1])
        accession = re.search(r'ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})', header[1])
        accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', header[1])
        filed_header = re.search(r'FILED AS OF DATE:\s*(\d{8})', header[1])
        if (not cik or int(cik[1])!=int(issuer['cik']) or not form or form[1].strip()!=filing['form']
                or not accession or accession[1]!=filing['accession'] or not accepted):
            return False
        header_clock = datetime.strptime(accepted[1],'%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('America/New_York'))
        # Round-trip rejects nonexistent NY local times, not merely an offset.
        if header_clock.astimezone(timezone.utc).astimezone(ZoneInfo('America/New_York')) != header_clock:
            return False
        filed = _day(filing['filed'])
        candidate = _filing_clock(filed,header_clock.astimezone(timezone.utc).isoformat())
        if not candidate:
            return False
        basis = filing.get('publication_clock_basis','EXACT_SUBMISSIONS_ACCEPTANCE')
        if basis not in {'EXACT_SUBMISSIONS_ACCEPTANCE','CONSERVATIVE_FILED_DAY_END_MISSING_ACCEPTANCE','CONSERVATIVE_FILED_DAY_END_LATE_ACCEPTANCE'}:
            return False
        if basis=='CONSERVATIVE_FILED_DAY_END_LATE_ACCEPTANCE' and (candidate['publication_clock_basis']!=basis or not filing.get('accepted_at')):
            return False
        if basis=='CONSERVATIVE_FILED_DAY_END_MISSING_ACCEPTANCE' and filing.get('accepted_at') is not None:
            return False
        if basis != 'EXACT_SUBMISSIONS_ACCEPTANCE':
            if not filed_header or filed_header[1]!=filed.strftime('%Y%m%d'):
                return False
            expected = datetime.combine(filed,datetime.max.time(),ZoneInfo('America/New_York'))
        else:
            # Legacy exact same-day fixtures omit FILED AS OF DATE. When the
            # official header supplies it, it must still agree independently.
            if header_clock.astimezone(ZoneInfo('America/New_York')).date()!=filed:
                return False
            if filed_header and filed_header[1]!=filed.strftime('%Y%m%d'):
                return False
            expected = header_clock
        if accepted_meta := filing.get('accepted_at'):
            metadata_clock = _filing_clock(filed,accepted_meta)
            if not metadata_clock or metadata_clock['accepted_at'] != candidate['accepted_at']:
                return False
        elif basis == 'EXACT_SUBMISSIONS_ACCEPTANCE' and header_clock != datetime.fromisoformat(filing['published_at'].replace('Z','+00:00')):
            return False
        return expected == datetime.fromisoformat(filing['published_at'].replace('Z','+00:00'))
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):
        return False


def verify_filing_envelope(source, issuer, filing):
    """Verify a full-submission envelope without extracting financial facts."""
    if not source or not isinstance(issuer, dict) or not isinstance(filing, dict):
        return None
    if not allowed_url(filing.get('full_url')) or 'T' not in str(filing.get('published_at', '')):
        return None
    try:
        body, fetched_at = source
        if not isinstance(body, bytes):
            return None
        text = body.decode('utf-8', errors='strict')
        if not _header_clock_matches(text,issuer,filing):
            return None
        published = datetime.fromisoformat(filing['published_at'].replace('Z', '+00:00'))
        fetched = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
        base = (f"https://www.sec.gov/Archives/edgar/data/{int(issuer['cik'])}/"
                f"{filing['accession'].replace('-', '')}/")
        if (fetched.tzinfo is None or published.tzinfo is None or published > fetched
                or filing['full_url'] != base + filing['accession'] + '.txt'
                or not filing['primary_url'].startswith(base)):
            return None
        return dict(source_ref=filing['full_url'], source_sha256=hashlib.sha256(body).hexdigest(),
                    fetched_at=fetched.isoformat(), published_at=published.isoformat())
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, UnicodeError):
        return None


def collect(symbol):
    """Current-cycle discovery: cached directory + Submissions + facts + originals.

    No network in demo/mock. Successful original reads retain original fetched_at.
    A failure to resolve/verify never turns a request/card into source authority.
    """
    _diagnostics.set(())
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    from backend.services.market_data.cache import get_cached, set_cached, FUNDAMENTALS_TTL
    if PUBLIC_DEMO_MODE or os.getenv('AV_DEV_MOCK') == '1' or not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}:US', symbol):
        return [], {}.get
    cache_key = 'sec-disclosures:v2-listed-eps:'+symbol
    cached = get_cached(cache_key)
    if cached is not None:
        return deepcopy(cached), {r['record_id']:deepcopy(r) for r in cached}.get
    directory_source = get_cached('sec-directory:v1')
    if directory_source is None:
        directory_source = _fetch(DIRECTORY)
        if directory_source:
            set_cached('sec-directory:v1', directory_source, FUNDAMENTALS_TTL)
    directory = _json(directory_source)
    if not directory:
        if directory_source: _diagnose(DIRECTORY,'invalid_json')
        return [], {}.get
    matches = sec_ticker_matches(symbol, directory)
    if len(matches) != 1:
        _diagnose(DIRECTORY,'identity_unresolved')
        return [], {}.get
    try:
        cik = str(int(matches[0]['cik_str'])).zfill(10)
        if not re.fullmatch(r'\d{10}', cik) or int(cik) <= 0:
            return [], {}.get
        submissions_source = _fetch('https://data.sec.gov/submissions/CIK'+cik+'.json')
        submissions = _json(submissions_source)
        issuer = resolve_sec_issuer(symbol, directory, submissions)
        if not issuer:
            if submissions_source: _diagnose('https://data.sec.gov/submissions/CIK'+cik+'.json','invalid_json' if submissions is None else 'identity_unresolved')
            return [], {}.get
        filings = discover_filings(issuer, submissions)
        identity_digest = hashlib.sha256(directory_source[0]+submissions_source[0]).hexdigest()
        facts = _fetch('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json')
        if not facts and _access_failed():
            return [], {}.get
        rows = parse_companyfacts(facts, issuer, filings, identity_digest=identity_digest)
        if facts and not rows:
            invalid=_json(facts) is None
            _diagnose('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json','invalid_json' if invalid else 'data_missing')
            if invalid:
                return [], {}.get
        # Submissions metadata cannot distinguish earnings 6-Ks from events.
        # Search the current reporting cycle newest-first, not the first two
        # events. Annual reports are a fallback, never a prerequisite for L1.
        quarters=[f for f in filings if f['form']=='10-Q']
        annuals=[f for f in filings if f['form'] in {'10-K','20-F'}]
        events=[f for f in filings if f['form'] in {'6-K','8-K'}
                and (_day(filings[0]['filed'])-_day(f['filed'])).days<=550]
        selected=sorted(quarters+events,key=lambda f:(f['filed'],f['accession']),reverse=True)+annuals[:1]
        filed_by_accession={f['accession']:f['filed'] for f in filings}
        for filing in selected:
            # Facts retain their independent periods. Older complete quarters
            # cannot conceal a newer, partially observed quarterly statement.
            quarterly=[o for r in rows for o in r['financial_observations']
                       if o['period_basis']=='quarterly']
            if quarterly and filing['form'] in {'10-K','20-F'}:
                continue  # current/history quarters do not require a large annual original
            if quarterly:
                latest=max(o['fiscal_period_end'] for o in quarterly)
                complete={o['source_field'] for o in quarterly if o['fiscal_period_end']==latest}
                eps_field='dilutedEPSADS' if issuer['issuer_type']=='foreign_private' else 'dilutedEPSOrdinary'
                eps_periods={o['fiscal_period_end'] for o in quarterly if o['source_field']==eps_field}
                latest_filed=max(filed_by_accession[r['official_disclosure']['accession']]
                    for r in rows if any(o['period_basis']=='quarterly' and o['fiscal_period_end']==latest
                                         for o in r['financial_observations']))
                periods={o['fiscal_period_end'] for o in quarterly}
                if ({'totalRevenue','netIncome','operatingCashflow','capitalExpenditures'}<=complete
                        and len(periods)>=8 and set(sorted(periods,reverse=True)[:4])<=eps_periods and filing['filed']<=latest_filed):
                    break
            # Four independent earnings originals plus their comparatives are
            # sufficient for this minimal research projection. Event-only files
            # do not consume this existing financial-record bound.
            if len({r['official_disclosure']['accession'] for r in rows if r.get('source_url','').endswith('.txt') and any(o['period_basis']=='quarterly' for o in r['financial_observations'])})>=MAX_FILINGS:
                break
            try:
                original = _fetch(filing['full_url'])
                if not original:
                    continue
                record = parse_filing(original, issuer, filing, identity_digest=identity_digest,
                                      fiscal_eps_context=_domestic_fiscal_eps_context(rows,issuer))
            except (ValueError, TypeError, KeyError, UnicodeError, OSError, InvalidOperation, OverflowError):
                _diagnose(filing['full_url'],'parse_error')
                continue
            if record and record['financial_observations']:
                # Keep overlapping source facts too; evidence owner handles actual
                # conflicts rather than silently selecting whichever matches AV.
                rows.append(record)
        if rows:
            set_cached(cache_key, deepcopy(rows), FUNDAMENTALS_TTL)
        return deepcopy(rows), {r['record_id']:deepcopy(r) for r in rows}.get
    except (ValueError, TypeError, KeyError, UnicodeError):
        return [], {}.get
