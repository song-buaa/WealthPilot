"""S3 source adapter. SEC/AV transport and financial qualification remain with 4.1."""
import hashlib
import json
from pathlib import Path

from backend.services.market_data.evidence import stamp
from backend.services.market_data.financial_research import operating_ttm_bridge
from research_v2 import official_disclosures, sec_disclosures

SCHEMA = 's3.current-financial-owner.v1'
SYMBOLS = frozenset({'NVDA:US', 'TSLA:US'})
VENDOR_FUNCTIONS = ('INCOME_STATEMENT', 'EARNINGS', 'CASH_FLOW')


def frozen(symbol, entry, source, at):
    contract = entry['current_financial']
    if symbol not in SYMBOLS or contract.get('schema_version') != SCHEMA:
        raise ValueError('S3_CURRENT_FINANCIAL_CONTRACT_REJECTED')
    if contract.get('failure'):
        raise ValueError(contract['failure'])
    receipts = [entry[kind] for kind in ('directory', 'submissions', 'companyfacts')]
    receipts += entry.get('originals', [])
    by_url = {row['url']: row for row in receipts if row.get('path')}

    def fetch(url):
        row = by_url.get(url)
        return source(row, url) if row is not None else None

    vendor = {}
    for function, row in contract['vendor'].items():
        if function not in VENDOR_FUNCTIONS or row.get('function') != function or row.get('symbol') != symbol:
            raise ValueError('S3_CURRENT_VENDOR_IDENTITY_REJECTED')
        body, fetched = source(row, 'https://www.alphavantage.co/query')
        item = json.loads(body)
        if stamp(item['diagnostics']['fetched_at']) != stamp(fetched):
            raise ValueError('S3_CURRENT_VENDOR_CLOCK_REJECTED')
        vendor[function] = item
    rows, verifier, receipt = official_disclosures.collect_current(
        symbol, at=at, frozen_fetch=fetch, frozen_vendor=vendor)
    if not receipt:
        raise ValueError('S3_CURRENT_OWNER_RECEIPT_UNAVAILABLE')
    return rows, verifier, receipt


def capture(root, manifest, symbols):
    """Record only the current Owner inputs not already captured for this pool."""
    from backend.services.market_data.av_fundamentals_service import collect_current_statements

    root = Path(root)

    def store(body, name, **metadata):
        (root / name).write_bytes(body)
        row = dict(metadata, path=name, sha256=hashlib.sha256(body).hexdigest())
        manifest['requests'].append(row)
        return row

    for symbol in sorted(SYMBOLS.intersection(symbols)):
        entry = manifest['companies'][symbol]
        ticker = symbol[:-3]
        try:
            if not all(entry.get(kind, {}).get('path') for kind in ('directory', 'submissions', 'companyfacts')):
                raise ValueError('S3_CURRENT_STRUCTURED_INPUT_UNAVAILABLE')
            by_url = {row['url']: row for row in [
                entry['directory'], entry['submissions'], entry['companyfacts'], *entry.get('originals', [])]
                if row.get('path')}

            def fetch(url):
                if row := by_url.get(url):
                    path = root / row['path']
                    body = path.read_bytes()
                    if hashlib.sha256(body).hexdigest() != row['sha256']:
                        raise ValueError('S3_CURRENT_CAPTURE_SOURCE_HASH_REJECTED')
                    return body, row['fetched_at']
                response = sec_disclosures._fetch(url)
                if response:
                    body, fetched_at = response
                    row = store(body, ticker + '-v2-' + url.rsplit('/', 1)[-1],
                                url=url, fetched_at=fetched_at, owner='SEC', status='RAW_NOT_YET_QUALIFIED')
                    entry.setdefault('originals', []).append(row)
                    by_url[url] = row
                return response

            vendor = collect_current_statements(symbol)
            official_disclosures.collect_current(symbol, frozen_fetch=fetch, frozen_vendor=vendor)
            entry['current_financial'] = dict(schema_version=SCHEMA, vendor={
                function: store(json.dumps(item, sort_keys=True).encode(), ticker+'-v2-'+function+'.json',
                                function=function, symbol=symbol, owner='Alpha Vantage',
                                url='https://www.alphavantage.co/query', fetched_at=item['diagnostics']['fetched_at'])
                for function, item in vendor.items()})
        except (KeyError, ValueError, TypeError, OSError, ArithmeticError):
            entry['current_financial'] = dict(schema_version=SCHEMA, failure='S3_CURRENT_OWNER_CAPTURE_FAILED')


def operating_bridge(assessment):
    return operating_ttm_bridge(assessment, current=True) if assessment.get('s3_current_owner') else operating_ttm_bridge(assessment)


def blocker(symbol, bridge):
    if bridge['status'] == 'PARTIAL_GAAP_OPERATING_TTM':
        if symbol == 'NVDA:US' and 'net_income' in bridge['missing_fields']:
            return 'NVDA_PARENT_NET_INCOME_SCOPE_UNPROVEN'
        return 'S3_CURRENT_OPERATING_TTM_INCOMPLETE'
    return None
