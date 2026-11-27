import json
from datetime import datetime, timezone

import pytest

from backend.services import s3_current_financial as current
from research_v2 import official_disclosures, sec_disclosures

AT = datetime(2026, 11, 27, 13, tzinfo=timezone.utc)


def test_frozen_current_calls_official_owner_without_vendor_or_sec_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Frozen transport may not make a live call')

    seen = {}
    monkeypatch.setattr(official_disclosures, '_collect_li', forbidden)
    monkeypatch.setattr(sec_disclosures, '_fetch', forbidden)

    def owner(symbol, *, current):
        seen.update(current)
        assert current['frozen_fetch'](sec_disclosures.DIRECTORY) == (b'{}', AT.isoformat())
        assert current['frozen_fetch']('missing-original') is None
        current['result'] = dict(latest_period='2026-09-30', vendor_records=[])
        return [{'record_id': 'official'}], None

    monkeypatch.setattr(sec_disclosures, 'collect', owner)
    vendor = dict(payload={'symbol': 'TSLA'}, diagnostics={'fetched_at': AT.isoformat()})
    entry = dict(directory={'url': sec_disclosures.DIRECTORY, 'path': 'directory'},
                 submissions={}, companyfacts={}, current_financial=dict(schema_version=current.SCHEMA,
                 vendor={'EARNINGS': dict(url='https://www.alphavantage.co/query', path='av',
                                          symbol='TSLA:US', function='EARNINGS')}))

    def source(row, url):
        assert row['url'] == url
        return (json.dumps(vendor).encode() if row['path'] == 'av' else b'{}', AT.isoformat())

    rows, lookup, receipt = current.frozen('TSLA:US', entry, source, AT)
    assert lookup('official') == rows[0]
    assert seen['at'] == AT and seen['capture'] == {'EARNINGS': vendor}
    assert receipt['vendor_records'] == [] and receipt['av_endpoints']['EARNINGS'] == vendor['diagnostics']
    vendor['diagnostics']['fetched_at'] = '2026-11-28T13:00:00Z'
    with pytest.raises(ValueError, match='S3_CURRENT_VENDOR_CLOCK_REJECTED'):
        current.frozen('TSLA:US', entry, source, AT)


def test_current_capture_failure_is_per_issuer_and_cannot_replay_as_legacy(tmp_path, monkeypatch):
    from backend.services.market_data import av_fundamentals_service as av

    directory, submissions, facts = [dict(path=name, url=name) for name in ('dir', 'sub', 'facts')]
    manifest = dict(companies={
        symbol: dict(directory=directory, submissions=submissions, companyfacts=facts)
        for symbol in ('NVDA:US', 'TSLA:US')}, requests=[])

    def collect(symbol):
        if symbol == 'NVDA:US':
            raise OSError('unavailable')
        return {}

    monkeypatch.setattr(av, 'collect_current_statements', collect)
    monkeypatch.setattr(official_disclosures, 'collect_current', lambda *args, **kwargs: ([], None, {}))
    current.capture(tmp_path, manifest, manifest['companies'])
    assert manifest['companies']['TSLA:US']['current_financial']['vendor'] == {}
    with pytest.raises(ValueError, match='S3_CURRENT_OWNER_CAPTURE_FAILED'):
        current.frozen('NVDA:US', manifest['companies']['NVDA:US'], None, AT)


def test_professional_partial_nvda_bridge_cannot_be_supplied_with_group_profit():
    bridge = dict(status='PARTIAL_GAAP_OPERATING_TTM', missing_fields=['net_income'],
                  eps={'values': {'diluted_eps_ttm': '9.31'}}, flows={'operating_income': {'value': '239090000000'}})
    assert current.blocker('NVDA:US', bridge) == 'NVDA_PARENT_NET_INCOME_SCOPE_UNPROVEN'
    assert current.blocker('TSLA:US', bridge) == 'S3_CURRENT_OPERATING_TTM_INCOMPLETE'
