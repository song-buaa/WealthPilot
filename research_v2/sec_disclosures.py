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
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from backend.services.instruments.issuers import resolve_sec_issuer

DIRECTORY = 'https://www.sec.gov/files/company_tickers.json'
FORMS = {'10-K', '10-Q', '20-F', '6-K', '8-K'}
MAX_BYTES = 16 * 1024 * 1024
MAX_FILINGS = 4
# Explicit semantic mappings, not label similarity. Unsupported/custom concepts
# remain unknown. NetIncomeLoss is parent-attributable and must not silently
# corroborate AV's unspecified consolidation basis.
CONCEPTS = {
    ('us-gaap', 'RevenueFromContractWithCustomerExcludingAssessedTax'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'Revenues'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'SalesRevenueNet'): ('totalRevenue', 'total_revenue'),
    ('us-gaap', 'ProfitLoss'): ('netIncome', 'consolidated_net_income'),
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
        return None
    try:
        with _request_lock:
            time.sleep(max(0, .25 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
            req = urllib.request.Request(url, headers={
                'User-Agent': 'WealthPilot/4.2 public research https://github.com/song-buaa/WealthPilot',
                'Accept': 'application/json,text/html,text/plain'})
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=8) as response:
                if response.geturl() != url:
                    return None
                body = response.read(MAX_BYTES + 1)
        if not body or len(body) > MAX_BYTES:
            return None
        return body, datetime.now(timezone.utc).isoformat()
    except Exception:
        return None  # Never retain provider response bodies/error strings.


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
    if not observations:
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
        for currency, rows in units.items():
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
                    if filing['form'] in {'10-Q','10-K','20-F'} and row['end']!=filing['report_date']:
                        continue
                    value=_finite(row.get('val'))
                    if value is None or abs(value)>Decimal('1e20') or (field=='capitalExpenditures' and value<0):
                        continue
                    by_filing.setdefault(filing['accession'],[]).append(dict(source_field=field,value=float(value),
                        currency=currency,unit='currency_units',unit_scale=1,period_basis=scope,
                        fiscal_period_start=row['start'],fiscal_period_end=row['end'],accounting_basis=basis,
                        source_locator=f'/facts/{taxonomy}/{concept}/units/{currency}/{i} (accn={filing["accession"]})'))
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


def parse_filing(source, issuer, filing, *, identity_digest):
    """Restricted full-submission envelope and inline XBRL, including exhibits.

    Never fetch an attachment named by untrusted HTML. The SEC full submission
    contains its original documents. All inline facts require an exact CIK context,
    explicit duration/unit/concept. Unsupported non-XBRL text stays unknown.
    """
    if not source or not allowed_url(filing['full_url']):
        return None
    body, fetched_at = source
    if not isinstance(body, bytes) or len(body)>MAX_BYTES:
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
    observations = []
    # Context IDs are document-scoped; no cross-exhibit borrowing of clocks/units.
    for doc_index, document in enumerate(re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>', text, re.S | re.I)):
        soup = BeautifulSoup(document, 'html.parser')
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
                    source_locator=f'DOCUMENT[{doc_index}]/ix:nonFraction[{i}]/{node.get("name")}'))
            except (ValueError, TypeError, InvalidOperation):
                continue
    if not observations and issuer['issuer_id'] == 'LI_AUTO':
        # Reuse the reviewed real LI statement layout; do not add an issuer-specific
        # SEC parser. SEC discovery/envelope was independently verified above.
        from research_v2.official_disclosures import parse_original
        row = parse_original(body, filing['full_url'], fetched_at)
        if row:
            observations = row['financial_observations']
            for obs in observations:
                obs['fiscal_period_start'] = date.fromisoformat(obs['fiscal_period_end']).replace(day=1, month=((int(obs['fiscal_period_end'][5:7])-1)//3)*3+1).isoformat()
    return _record(issuer, filing, observations, body, filing['full_url'], fetched_at, identity_digest=identity_digest)


def collect(symbol):
    """Bounded discovery: cached directory + Submissions + facts + <=4 originals.

    No network in demo/mock. Successful original reads retain original fetched_at.
    A failure to resolve/verify never turns a request/card into source authority.
    """
    import os
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
        return [], {}.get
    matches = [r for r in directory.values() if isinstance(r, dict) and r.get('ticker') == symbol[:-3]]
    if len(matches) != 1:
        return [], {}.get
    try:
        cik = str(int(matches[0]['cik_str'])).zfill(10)
        if not re.fullmatch(r'\d{10}', cik) or int(cik) <= 0:
            return [], {}.get
        submissions_source = _fetch('https://data.sec.gov/submissions/CIK'+cik+'.json')
        submissions = _json(submissions_source)
        issuer = resolve_sec_issuer(symbol, directory, submissions)
        if not issuer:
            return [], {}.get
        filings = discover_filings(issuer, submissions)
        identity_digest = hashlib.sha256(directory_source[0]+submissions_source[0]).hexdigest()
        facts = _fetch('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json')
        rows = parse_companyfacts(facts, issuer, filings, identity_digest=identity_digest)
        # Only missing semantic field/scope combinations use original attachments.
        # Limit to latest four relevant filings, never walk arbitrary source URLs.
        # Include latest annual/quarterly reports even when monthly foreign
        # event reports fill the top of Submissions. Remaining slots are events.
        standards=[f for f in filings if f['form'] in {'10-K','10-Q','20-F'}]
        events=[f for f in filings if f['form'] in {'6-K','8-K'}]
        selected=standards[:2]+events[:2]
        for filing in selected:
            existing = [o for r in rows if r['official_disclosure']['accession'] == filing['accession'] for o in r['financial_observations']]
            fields = {o['source_field'] for o in existing}
            if len(fields) == 4:
                continue
            original = _fetch(filing['full_url'])
            record = parse_filing(original, issuer, filing, identity_digest=identity_digest)
            if record and record['financial_observations']:
                # Keep overlapping source facts too; evidence owner handles actual
                # conflicts rather than silently selecting whichever matches AV.
                rows.append(record)
        if rows:
            set_cached(cache_key, deepcopy(rows), FUNDAMENTALS_TTL)
        return deepcopy(rows), {r['record_id']:deepcopy(r) for r in rows}.get
    except (ValueError, TypeError, KeyError, UnicodeError):
        return [], {}.get
