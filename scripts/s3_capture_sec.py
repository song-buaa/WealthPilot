"""Explicit bounded SEC raw capture for offline S3; no AV, LLM or broker calls.

Invoke only with authorized real SEC identity via scripts/sec_dev.py run.
Existing Source Owner controls identity, URL allowlist, rate and error taxonomy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('symbols', nargs='+')
    args = parser.parse_args()
    if not 1 <= len(args.symbols) <= 2 or args.output.exists():
        parser.error('Use a new output directory and at most two explicitly authorized symbols.')
    from research_v2 import sec_disclosures as sec
    from backend.services.instruments.issuers import sec_ticker_matches, resolve_sec_issuer
    if not sec.request_identity():
        print(json.dumps(dict(status='identity_unconfigured', request_sent=False,
                             repair='scripts/sec_dev.py configure; scripts/sec_dev.py run -- ...')))
        return 2
    args.output.mkdir(parents=True)
    manifest = dict(schema_version='s3-official-raw.v1', companies={}, requests=[])
    def capture(url, name):
        source = sec._fetch(url)
        if source:
            raw, at = source
            path = args.output/name
            path.write_bytes(raw)
            receipt = dict(url=url, path=name, sha256=hashlib.sha256(raw).hexdigest(), fetched_at=at,
                           status='HTTP200_RAW_ONLY_NOT_FINANCIAL_QUALIFICATION')
        else:
            receipt = dict(url=url, status='FAILED', diagnostics=sec.source_diagnostics())
        manifest['requests'].append(receipt)
        (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2))
        return source, receipt
    directory, directory_receipt = capture(sec.DIRECTORY, 'directory.json')
    if not directory:
        return 2
    for symbol in sorted(set(args.symbols)):
        entry = manifest['companies'][symbol] = dict(directory=directory_receipt)
        try:
            matches = sec_ticker_matches(symbol, json.loads(directory[0]))
            if len(matches) != 1:
                raise ValueError('IDENTITY_UNRESOLVED')
            cik = str(int(matches[0]['cik_str'])).zfill(10)
            submissions, entry['submissions'] = capture('https://data.sec.gov/submissions/CIK'+cik+'.json', cik+'-submissions.json')
            if not submissions or not resolve_sec_issuer(symbol, json.loads(directory[0]), json.loads(submissions[0])):
                raise ValueError('IDENTITY_UNRESOLVED')
            _, entry['companyfacts'] = capture('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json', cik+'-companyfacts.json')
        except (ValueError, KeyError, TypeError):
            entry['failure'] = 'OFFICIAL_IDENTITY_OR_SOURCE_UNRESOLVED'
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(dict(path=str(args.output/'manifest.json'), request_count=len(manifest['requests']),
                         raw_successes=sum('sha256' in r for r in manifest['requests']))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
