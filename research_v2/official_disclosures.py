"""Independent, bounded LI original-disclosure verification in the research owner.

Cards supply discovery URLs only. Authority comes from freshly fetched original
bytes, their publication header and unambiguous statement cells, never AI fields.
Unsupported layouts/periods remain unknown. No persistence or asset operations.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import re
from urllib.parse import urlsplit, urljoin
import urllib.request

from bs4 import BeautifulSoup

IR_INDEX = 'https://ir.lixiang.com/news/'
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
    'net cash (used in)/provided by operating activities': ('operatingCashflow', 'operating_cash_flow'),
    'capital expenditures': ('capitalExpenditures', 'positive_capital_cash_outflow'),
}


def allowed_url(url, *, index=False):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and not p.username and not p.password and p.port in (None, 443)
            and not p.query and not p.fragment
            and ((p.hostname == 'ir.lixiang.com' and
                  (re.fullmatch(r'/(?:zh-hans/|zh-hant/)?news-releases/news-release-details/[a-z0-9]+(?:-[a-z0-9]+)*/?', p.path) is not None
                   or (index and re.fullmatch(r'/(?:zh-hans/|zh-hant/)?news/?', p.path) is not None)))
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
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; WealthPilot/4.2)', 'Accept': 'text/html,text/plain'})
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
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%Y-%m-%d', '%Y年 %m月 %d日'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _publication(value):
    """Retain explicit source timezones; day-only sources remain day precision."""
    value = ' '.join(str(value).split())
    timed = re.fullmatch(r'([A-Za-z]+ \d{1,2}, \d{4})(?: at)? (\d{1,2}:\d{2})(?: ([AP]M))? (EDT|EST|UTC|GMT)', value)
    if timed:
        clock = datetime.strptime(timed[1]+' '+timed[2]+(' '+timed[3] if timed[3] else ''),
            '%B %d, %Y %I:%M %p' if timed[3] else '%B %d, %Y %H:%M')
        return clock.replace(tzinfo=timezone(timedelta(hours={'EDT':-4,'EST':-5,'UTC':0,'GMT':0}[timed[4]])))
    day = _date(value)
    if day:
        return day
    clock = datetime.fromisoformat(value.replace('Z','+00:00'))
    if clock.tzinfo is None:
        raise ValueError('PUBLICATION_TIMEZONE_UNKNOWN')
    return clock


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


def _amount(text):
    """Strict statement amount, including paired accounting parentheses."""
    text = str(text).strip()
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1]
        if text.startswith('-'):
            return None
    if not re.fullmatch(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', text):
        return None
    value = Decimal(text.replace(',', ''))
    return -value if negative else value


def _statement_kind(rows):
    header = ' '.join(' '.join(r) for r in rows[:2]).lower()
    if 'condensed consolidated statements of comprehensive income' in header:
        return 'income'
    if 'condensed consolidated statements of cash flows' in header:
        return 'cash_flow'
    return None


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
        if urlsplit(url).hostname != 'www.sec.gov' and not any(re.search(r'Li Auto Inc\..*Financial Results', t.get_text(' ', strip=True), re.I) for t in titles):
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
            clocks = []
            for node in soup.select('meta[property="article:published_time"], meta[name="date"], time[datetime], .field--name-field-nir-news-date'):
                value = node.get('content') or node.get('datetime') or node.get_text(' ', strip=True)
                clocks.append(_publication(value))
            days = {clock.date().isoformat() if isinstance(clock, datetime) else clock for clock in clocks}
            times = [clock for clock in clocks if isinstance(clock, datetime)]
            if not clocks or len(days) != 1 or (times and len(set(times)) != 1):
                return None
            if any(clock > fetched for clock in times):
                return None
            published = times[0].isoformat() if times else clocks[0]
            precision = 'timezone-aware timestamp' if times else 'day precision'
            publication_locator = 'original HTML publication header ('+precision+')'
        if published[:10] > fetched.date().isoformat():
            return None
        observations = []
        tables = [(i, table, _grid(table)) for i, table in enumerate(soup.find_all('table'))]
        has_statements = any(_statement_kind(rows) for _, _, rows in tables)
        for ti, table, rows in tables:
            statement = _statement_kind(rows)
            if has_statements and not statement:
                continue  # Rounded summaries/reconciliations cannot override statements.
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
                if statement and ((statement == 'income') != (key in {'totalRevenue', 'netIncome'})):
                    continue
                for col, amount in enumerate(row[1:], 1):
                    header = ' '.join(r[col] for r in rows[:ri] if col < len(r))
                    # Period semantics belong to this column, not a neighboring YTD column.
                    if not re.search(r'three months ended', header, re.I) or re.search(r'(six|nine|twelve) months|year ended', header, re.I):
                        continue
                    # A column must independently identify ONE quarter end + RMB.
                    dates = re.findall(r'(?:March|June|September|December)\s+\d{1,2},?\s+20\d{2}', header)
                    currency = set(re.findall(r'(?<![A-Za-z])(?:RMB|CNY|USD|US\$|HK\$|HKD|EUR|GBP|JPY|\$)(?![A-Za-z])', header))
                    explicit_currency = any(r[col].strip().strip('()') in {'RMB', 'CNY'} for r in rows[:ri] if col < len(r))
                    if not explicit_currency or len(set(dates)) != 1 or currency not in ({'RMB'}, {'CNY'}):
                        continue
                    end = _date(dates[0].replace(',', '') if ',' not in dates[0] else dates[0])
                    if not end:
                        try:
                            end = datetime.strptime(dates[0].replace(',', ''), '%B %d %Y').date().isoformat()
                        except ValueError:
                            continue
                    if end[5:] not in {'03-31','06-30','09-30','12-31'} or end > published:
                        continue
                    value = _amount(amount)
                    if value is None or not value.is_finite():
                        continue
                    reported_value, normalization = value, None
                    if key == 'capitalExpenditures' and (statement == 'cash_flow' or value < 0):
                        # Original LI cash-flow table explicitly reconciles OCF +
                        # negative CapEx = FCF. Never apply abs() to unknown signs.
                        before = next((r for r in reversed(rows[:ri]) if any(r)), [])
                        after = next((r for r in rows[ri+1:] if any(r)), [])
                        same_block = (before and after and LABELS.get(before[0].strip().lower().rstrip(':'), (None,))[0] == 'operatingCashflow'
                            and after[0].strip().lower() == 'free cash flow (non-gaap)')
                        ocf = _amount(before[col]) if same_block and col < len(before) else None
                        fcf = _amount(after[col]) if same_block and col < len(after) else None
                        if statement != 'cash_flow' or ocf is None or fcf is None or ocf - abs(value) != fcf:
                            continue
                        value = abs(value)
                        normalization = dict(basis='official_cash_flow_reconciliation: OCF - positive_spend = FCF',
                            reported_operating_cash_flow=float(ocf), reported_free_cash_flow=float(fcf),
                            reported_unit_scale=scales[0])
                    if label == 'net loss' and value > 0:
                        continue
                    if label == 'net cash used in operating activities' and value > 0:
                        continue
                    observations.append(dict(source_field=key, value=float(value), reported_value=float(reported_value),
                        reported_unit_scale=scales[0], sign_normalization=normalization, currency='CNY', unit='currency_units',
                        unit_scale=scales[0], period_basis='quarterly', fiscal_period_end=end, accounting_basis=basis,
                        source_locator=f'table[{ti}]/row[{ri}]/column[{col}]/{row[0]}'))
        # Identical repetitions in the SAME original are aliases, not another
        # independent observation. Conflicting cells stay unknown permanently.
        unique = {}
        for obs in observations:
            k = obs['source_field'], obs['fiscal_period_end']
            if k not in unique:
                unique[k] = obs
            elif unique[k] is not None:
                previous = unique[k]
                keys = ('currency', 'unit', 'accounting_basis', 'period_basis')
                if (all(previous[n] == obs[n] for n in keys)
                        and previous['value'] * previous['unit_scale'] == obs['value'] * obs['unit_scale']):
                    previous['source_locator'] += '; '+obs['source_locator']
                else:
                    unique[k] = None
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


def _collect_li(symbol, candidates=()):
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
            if (allowed_url(url) and re.fullmatch(r'Li Auto Inc\. (?:Announces|Reports) .*Financial Results',
                    a.get_text(' ', strip=True), re.I) and url not in urls):
                urls.append(url)
            if len(urls) == 4:
                break
    for candidate in candidates:
        if len(urls) == 4:
            break
        url = candidate.get('source_url') if isinstance(candidate, dict) else None
        if allowed_url(url) and urlsplit(url).hostname == 'ir.lixiang.com' and url not in urls:
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


def collect(symbol, candidates=()):
    """Unified research-owner discovery; issuer-specific layouts stay optional.

    SEC accessions are discovered independently of research cards. LI retains its
    reviewed official IR layout as an additional public source, not a SEC fork.
    """
    from research_v2.sec_disclosures import collect as collect_sec
    sec_rows, _ = collect_sec(symbol)
    ir_rows, _ = _collect_li(symbol, candidates)
    rows = [*sec_rows, *ir_rows]
    return rows, {r['record_id']:deepcopy(r) for r in rows}.get
