"""Independent, bounded LI original-disclosure verification in the research owner.

Cards supply discovery URLs only. Authority comes from freshly fetched original
bytes, their publication header and unambiguous statement cells, never AI fields.
Unsupported layouts/periods remain unknown. No persistence or asset operations.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from urllib.parse import urlsplit, urljoin
import urllib.request

from bs4 import BeautifulSoup

IR_INDEX = 'https://ir.lixiang.com/news-releases'
MAX_BYTES = 2 * 1024 * 1024
LABELS = {
    'total revenues': ('totalRevenue', 'total_revenue'),
    'total revenue': ('totalRevenue', 'total_revenue'),
    'net income': ('netIncome', 'consolidated_net_income'),
    'net loss': ('netIncome', 'consolidated_net_income'),
    'net income/(loss)': ('netIncome', 'consolidated_net_income'),
    'net (loss)/income': ('netIncome', 'consolidated_net_income'),
    'net cash provided by operating activities': ('operatingCashflow', 'operating_cash_flow'),
    'net cash used in operating activities': ('operatingCashflow', 'operating_cash_flow'),
    'capital expenditures': ('capitalExpenditures', 'positive_capital_cash_outflow'),
}


def allowed_url(url, *, index=False):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
            and not p.query and not p.fragment
            and ((p.hostname == 'ir.lixiang.com' and
                  (p.path.startswith('/news-releases/news-release-details/') or (index and p.path == '/news-releases')))
                 or (p.hostname == 'www.sec.gov' and
                     re.fullmatch(r'/Archives/edgar/data/[0-9]+/[0-9/.-]+\.txt', p.path) is not None)))
    except (ValueError, TypeError):
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never let a candidate/redirect escape the original source.


def _fetch(url):
    if not allowed_url(url, index=True):
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WealthPilot/4.2 private research', 'Accept': 'text/html,text/plain'})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=8) as response:
            if response.geturl() != url:
                return None
            body = response.read(MAX_BYTES + 1)
        if not body or len(body) > MAX_BYTES:
            return None
        return body, datetime.now(timezone.utc).isoformat()
    except Exception:
        return None  # No sensitive HTTP response/error strings.


def _date(text):
    text = ' '.join(str(text).split())
    dated_time = re.fullmatch(r'([A-Za-z]+ \d{1,2}, \d{4})(?: at)? \d{1,2}:\d{2}(?: [AP]M)? (?:EDT|EST|UTC|GMT)', text)
    if dated_time:
        text = dated_time[1]
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _grid(table):
    """Expand original HTML spans, preserving actual column alignment."""
    result, occupied = [], {}
    for i, tr in enumerate(table.find_all('tr')):
        row, col = {}, 0
        for (r, c), value in list(occupied.items()):
            if r == i:
                row[c] = value
        for cell in tr.find_all(['td', 'th'], recursive=False):
            while col in row:
                col += 1
            rs, cs = int(cell.get('rowspan', 1)), int(cell.get('colspan', 1))
            if not 1 <= rs <= 30 or not 1 <= cs <= 30:
                raise ValueError('INVALID_TABLE')
            value = ' '.join(cell.stripped_strings)
            for c in range(col, col + cs):
                row[c] = value
                for r in range(i + 1, i + rs):
                    occupied[r, c] = value
            col += cs
        if row:
            result.append([row.get(c, '') for c in range(max(row) + 1)])
    return result


def parse_original(body, url, fetched_at):
    """Strict original-source parser. No card metadata or AV numbers as input."""
    if not isinstance(body, bytes) or len(body) > MAX_BYTES or not allowed_url(url):
        return None
    try:
        text = body.decode('utf-8')
        soup = BeautifulSoup(text, 'html.parser')
        fetched = datetime.fromisoformat(fetched_at.replace('Z', '+00:00'))
        if fetched.tzinfo is None:
            return None
        titles = soup.find_all(['h1', 'h2', 'title'])
        if not any(re.search(r'Li Auto Inc\..*Financial Results', t.get_text(' ', strip=True), re.I) for t in titles):
            return None
        if urlsplit(url).hostname == 'www.sec.gov':
            # SEC full submission has independently stamped acceptance and issuer.
            header = re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>', text, re.S | re.I)
            company = re.search(r'COMPANY DATA:(.*?)(?:FILING VALUES:|BUSINESS ADDRESS:)', header[1], re.S | re.I) if header else None
            name = re.search(r'COMPANY CONFORMED NAME:\s*([^\n]+)', company[1]) if company else None
            cik = re.search(r'CENTRAL INDEX KEY:\s*(\d+)', company[1]) if company else None
            path_cik = urlsplit(url).path.split('/')[4]
            if (not name or name[1].strip().lower() != 'li auto inc.' or not cik
                    or int(cik[1]) != int(path_cik)):
                return None
            accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', header[1])
            if not accepted:
                return None
            # Acceptance is a filing date, not an invented intraday publication clock.
            published = datetime.strptime(accepted[1][:8], '%Y%m%d').date().isoformat()
            publication_locator = 'SEC-HEADER/ACCEPTANCE-DATETIME (filing day precision)'
        else:
            dates = []
            for node in soup.select('meta[property="article:published_time"], meta[name="date"], time[datetime], .field--name-field-nir-news-date'):
                value = node.get('content') or node.get('datetime') or node.get_text(' ', strip=True)
                date = _date(value)
                if not date:
                    try:
                        date = datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat()
                    except (ValueError, TypeError, AttributeError):
                        return None
                if not date:
                    return None
                dates.append(date)
            if not dates or len(set(dates)) != 1:
                return None
            published, publication_locator = dates[0], 'original HTML publication header (day precision)'
        if published > fetched.date().isoformat():
            return None
        observations = []
        for ti, table in enumerate(soup.find_all('table')):
            rows = _grid(table)
            context = ' '.join(' '.join(r) for r in rows).lower()
            # Never reinterpret year-to-date/annual numbers as quarters.
            if not re.search(r'three months ended', context):
                continue
            caption = table.find_previous(['p', 'div'])
            unit_text = (table.get_text(' ', strip=True) + ' ' + (caption.get_text(' ', strip=True) if caption and table not in caption.descendants else '')).lower()
            scales = [scale for label, scale in [('thousands', 1000), ('millions', 1000000)] if re.search(r'in '+label, unit_text)]
            if len(scales) != 1:
                continue
            for ri, row in enumerate(rows):
                if not row:
                    continue
                label = row[0].strip().lower().rstrip(':')
                if label not in LABELS:
                    continue
                key, basis = LABELS[label]
                for col, amount in enumerate(row[1:], 1):
                    header = ' '.join(r[col] for r in rows[:ri] if col < len(r))
                    # Period semantics belong to this column, not a neighboring YTD column.
                    if not re.search(r'three months ended', header, re.I) or re.search(r'(six|nine|twelve) months|year ended', header, re.I):
                        continue
                    # A column must independently identify ONE quarter end + RMB.
                    dates = re.findall(r'(?:March|June|September|December)\s+\d{1,2},?\s+20\d{2}', header)
                    currency = set(re.findall(r'\b(?:RMB|CNY|USD|US\$|HKD)\b', header))
                    if len(set(dates)) != 1 or currency not in ({'RMB'}, {'CNY'}):
                        continue
                    end = _date(dates[0].replace(',', '') if ',' not in dates[0] else dates[0])
                    if not end:
                        try:
                            end = datetime.strptime(dates[0].replace(',', ''), '%B %d %Y').date().isoformat()
                        except ValueError:
                            continue
                    if end[5:] not in {'03-31','06-30','09-30','12-31'} or end > published or not re.fullmatch(r'\(?-?[\d,]+(?:\.\d+)?\)?', amount):
                        continue
                    value = Decimal(amount.replace(',', '').replace('(', '-').replace(')', ''))
                    if not value.is_finite():
                        continue
                    # Negative CapEx/"used" semantics are not silently reinterpreted.
                    if (key == 'capitalExpenditures' and value < 0) or (label == 'net loss' and value > 0):
                        continue
                    if label == 'net cash used in operating activities' and value > 0:
                        continue
                    observations.append(dict(source_field=key, value=float(value), currency='CNY', unit='currency_units',
                        unit_scale=scales[0], period_basis='quarterly', fiscal_period_end=end, accounting_basis=basis,
                        source_locator=f'table[{ti}]/row[{ri}]/column[{col}]/{row[0]}'))
        # Ambiguous duplicate cells cannot certify a value (even if one matches AV).
        unique = {}
        for obs in observations:
            k = obs['source_field'], obs['fiscal_period_end']
            if k in unique:
                unique[k] = None
            else:
                unique[k] = obs
        observations = [o for o in unique.values() if o is not None]
        if not observations:
            return None
        digest = hashlib.sha256(body).hexdigest()
        return dict(symbol='LI:US', source='official_disclosure', source_url=url, record_id='official:sha256:'+digest,
            published_at=published, data_as_of=published, fetched_at=fetched_at,
            period=max(o['fiscal_period_end'] for o in observations),
            official_disclosure=dict(issuer_id='LI_AUTO', source_url=url), financial_observations=observations,
            content='Independently parsed original LI quarterly statement cells; publication is day precision.',
            source_verification=dict(document_sha256=digest, publication_locator=publication_locator))
    except (ValueError, TypeError, AttributeError, InvalidOperation, OverflowError):
        return None


def collect(symbol, candidates=()):
    """Read original research sources only; demo/mock never opens a socket.

    At most one discovery page + four originals. Cache retains original fetched_at.
    The existing UAT network guard still rejects non-whitelisted requests.
    """
    import os
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    from backend.services.market_data.cache import get_cached, set_cached, FUNDAMENTALS_TTL
    if symbol != 'LI:US' or PUBLIC_DEMO_MODE or os.getenv('AV_DEV_MOCK') == '1':
        return [], {}.get
    cached = get_cached('official-disclosures:v1:LI')
    if cached is not None:
        rows = deepcopy(cached)
        return rows, {r['record_id']:deepcopy(r) for r in rows}.get
    # Independent issuer index takes precedence over old/untrusted card URLs.
    urls = []
    page = _fetch(IR_INDEX)
    if page:
        soup = BeautifulSoup(page[0], 'html.parser')
        for a in soup.find_all('a', href=True):
            url = urljoin(IR_INDEX, a['href'])
            if allowed_url(url) and 'financial-results' in url and url not in urls:
                urls.append(url)
            if len(urls) == 4:
                break
    for candidate in candidates:
        if len(urls) == 4:
            break
        url = candidate.get('source_url') if isinstance(candidate, dict) else None
        if allowed_url(url) and url not in urls:
            urls.append(url)
    rows = []
    for url in urls:
        original = _fetch(url)
        record = parse_original(original[0], url, original[1]) if original else None
        if record:
            rows.append(record)
    if rows:
        set_cached('official-disclosures:v1:LI', deepcopy(rows), FUNDAMENTALS_TTL)
    return rows, {r['record_id']:deepcopy(r) for r in rows}.get
