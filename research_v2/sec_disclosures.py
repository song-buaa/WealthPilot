"""Bounded SEC source adapter owned by research_v2, not a financial engine.

Official directory + Submissions establish identity/accessions. CompanyFacts
supplies only entity-wide standard concepts. Restricted filing originals supply
inline-XBRL (or the existing LI table layout); unsupported facts stay unknown.
No AI extraction, asset writes, TTM calculation, redirect or arbitrary URL input.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
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
from backend.services.instruments.issuers import resolve_sec_issuer

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
_request_lock = threading.Lock()
_last_request = 0.0
_diagnostics = ContextVar('sec_source_diagnostics', default=())
_LOG = logging.getLogger(__name__)
SEC_IDENTITY_KEYS = {'WEALTHPILOT_SEC_ORGANIZATION', 'WEALTHPILOT_SEC_CONTACT_EMAIL'}


def request_identity():
    """Operator-owned identity only; no invented default or email ownership claim."""
    org=os.getenv('WEALTHPILOT_SEC_ORGANIZATION','').strip()
    email=os.getenv('WEALTHPILOT_SEC_CONTACT_EMAIL','').strip()
    if (not 2<=len(org)<=120 or any(ord(c)<32 or ord(c)>126 for c in org)
            or not re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+',email)
            or len(email)>254 or email.lower().endswith(('.invalid','.example','.test','@example.com','@example.org','@example.net'))
            or org.lower() in {'example','sample','test','your organization','your company'}):
        return None
    return org+' '+email


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
            published = filed.isoformat()
            locator = f'Submissions/filings/recent/filingDate[{i}] (filing day precision)'
            if accepted:
                clock = datetime.fromisoformat(accepted.replace('Z', '+00:00'))
                if clock.tzinfo is None or clock.astimezone(ZoneInfo('America/New_York')).date() != filed:
                    continue
                published = clock.isoformat()
                locator = f'Submissions/filings/recent/acceptanceDateTime[{i}]'
            rows.append(dict(accession=accession, form=form, filed=filed.isoformat(), report_date=report,
                published_at=published, publication_locator=locator, primary_url=base + document,
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
    return dict(symbol=issuer['symbol'], source='official_disclosure', source_url=url,
        record_id='official:sec:'+issuer['cik']+':'+filing['accession']+':'+digest,
        published_at=filing['published_at'], data_as_of=filing['published_at'], fetched_at=fetched_at,
        period=max(o['fiscal_period_end'] for o in observations),
        official_disclosure=dict(issuer_id=issuer['issuer_id'], source_url=url, cik=issuer['cik'],
                                 accession=filing['accession'], form=filing['form']),
        financial_observations=_deduplicate(observations),
        content='Independently sourced SEC statement facts; fiscal and filing clocks remain separate.',
        source_verification=dict(document_sha256=digest, publication_locator=filing['publication_locator'],
            issuer_identity=deepcopy(issuer), identity_document_sha256=identity_digest))


def parse_companyfacts(source, issuer, filings, *, identity_digest):
    """Select actual duration/currency/accession facts, no fiscal-frame guesses."""
    data = _json(source)
    if not data or str(data.get('cik', '')).zfill(10) != issuer['cik']:
        return []
    all_facts=data.get('facts')
    if not isinstance(all_facts,dict):
        return []
    by_accession={f['accession']:f for f in filings}
    by_filing={}
    for (taxonomy, concept), (field, basis) in CONCEPTS.items():
        concepts=all_facts.get(taxonomy)
        definition=concepts.get(concept) if isinstance(concepts,dict) else None
        units=definition.get('units') if isinstance(definition,dict) else None
        if not isinstance(units,dict):
            continue
        for original_unit, rows in units.items():
            currency=original_unit.removesuffix('/shares')
            if (field=='dilutedEPSOrdinary') != original_unit.endswith('/shares'):
                continue
            if currency not in {'USD', 'CNY', 'HKD'} or not isinstance(rows, list):
                continue
            for i, row in enumerate(rows):
                if not isinstance(row,dict):
                    continue
                try:
                    filing=by_accession.get(row.get('accn'))
                    if not filing or row.get('form')!=filing['form'] or row.get('filed')!=filing['filed']:
                        continue
                    scope=_period(row.get('start'),row.get('end'))
                    if not scope or _day(row['end'])>_day(filing['filed']):
                        continue
                    if filing['form'] in {'10-Q','10-K','20-F'} and row['end']>filing['report_date']:
                        continue
                    value=_finite(row.get('val'))
                    if value is None or abs(value)>Decimal('1e20') or (field=='capitalExpenditures' and value<0):
                        continue
                    by_filing.setdefault(filing['accession'],[]).append(dict(source_field=field,value=float(value),
                        currency=currency,unit='currency_per_ordinary_share' if field=='dilutedEPSOrdinary' else 'currency_units',unit_scale=1,period_basis=scope,
                        fiscal_period_start=row['start'],fiscal_period_end=row['end'],accounting_basis=basis,
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


def parse_filing(source, issuer, filing, *, identity_digest):
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
    header = re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>', text, re.S | re.I)
    if not header:
        return None
    cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', header[1])
    form = re.search(r'CONFORMED SUBMISSION TYPE:\s*([^\n<]+)', header[1])
    accession = re.search(r'ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})', header[1])
    accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', header[1])
    if (not cik or int(cik[1]) != int(issuer['cik']) or not form or form[1].strip() != filing['form']
            or not accession or accession[1] != filing['accession'] or not accepted
            or accepted[1][:8] != filing['filed'].replace('-', '')):
        return None
    if 'T' in filing['published_at']:
        header_clock=datetime.strptime(accepted[1], '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('America/New_York'))
        if header_clock != datetime.fromisoformat(filing['published_at'].replace('Z','+00:00')):
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
        from research_v2.official_disclosures import parse_statement_tables, parse_vehicle_operating
        try:
            table_observations=parse_statement_tables(soup,filing['published_at'],require_statements=True,currencies=('CNY','USD','HKD'),diagnostics=statement_issues)
            if is_exhibit and issuer['issuer_id'] in {'LI_AUTO', 'XPENG'}:
                table_observations += parse_vehicle_operating(soup,filing['published_at'])
        except (ValueError,TypeError,AttributeError,InvalidOperation,OverflowError):
            return None
        for obs in table_observations:
            end=date.fromisoformat(obs['fiscal_period_end'])
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
        header = re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>', text, re.S | re.I)
        if not header:
            return None
        cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', header[1])
        form = re.search(r'CONFORMED SUBMISSION TYPE:\s*([^\n<]+)', header[1])
        accession = re.search(r'ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})', header[1])
        accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', header[1])
        header_clock = datetime.strptime(accepted[1], '%Y%m%d%H%M%S').replace(
            tzinfo=ZoneInfo('America/New_York'))
        published = datetime.fromisoformat(filing['published_at'].replace('Z', '+00:00'))
        fetched = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
        base = (f"https://www.sec.gov/Archives/edgar/data/{int(issuer['cik'])}/"
                f"{filing['accession'].replace('-', '')}/")
        if (not cik or int(cik[1]) != int(issuer['cik']) or not form
                or form[1].strip() != filing['form'] or not accession
                or accession[1] != filing['accession'] or header_clock != published
                or fetched.tzinfo is None or published > fetched
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
    cache_key = 'sec-disclosures:v1:'+symbol
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
    matches = [r for r in directory.values() if isinstance(r, dict) and r.get('ticker') == symbol[:-3]]
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
                record = parse_filing(original, issuer, filing, identity_digest=identity_digest)
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
