"""Offline independent arithmetic and lineage check for a relative-value child."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime
from decimal import Decimal as D, localcontext


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load(path):
    b = json.loads(path.read_text())
    assert digest(b['payload']) == b['artifact_id'] == path.parent.name
    p = b['payload']
    for prefix, hashes in [('sources', p['source_files']), ('', p['view_hashes'])]:
        for name, expected in hashes.items():
            f = path.parent / prefix / name
            assert not f.is_symlink() and path.parent.resolve() in f.resolve().parents
            assert hashlib.sha256(f.read_bytes()).hexdigest() == expected
    return p


def verify_berkshire(parent_card):
    profile = parent_card['alternate_operating_profile']
    rows = profile['operating_millions']
    for column, expected in enumerate(profile['reported_totals_millions']):
        assert sum(values[column] for values in rows.values()) == expected
    shares = profile['class_a_outstanding']*1500 + profile['class_b_outstanding']
    bridge = parent_card['operating_earnings_bridge']
    assert bridge['b_equivalent_shares'] == shares
    segments = {name: D(fy + cy - py) for name, (fy,cy,py) in rows.items()}
    assert sum(segments.values()) == D(bridge['ttm_usd_millions'])
    with localcontext() as ctx:
        ctx.prec = 34
        price = D(parent_card['cross_checks']['observed_price']['values']['observed_price'])
        for name, hypothesis in profile['scenarios'].items():
            terminal_net = sum(v*(1+D(hypothesis['growth'][segment]))**5
                               for segment,v in segments.items())*1000000
            end_shares = shares*(1+D(hypothesis['annual_share_change']))**5
            wealth = terminal_net*D(hypothesis['multiple'])/end_shares
            case = parent_card['scenarios'][name]
            assert abs(D(case['terminal_wealth'])-wealth) < D('0.00000002')
            assert abs(D(case['annualized_return'])-((wealth/price)**(D(1)/5)-1)) < D('0.00000002')
    return len(profile['scenarios'])


def verify(path):
    if not __debug__:
        raise RuntimeError('OPTIMIZED_PYTHON_UNSAFE_FOR_VERIFICATION')
    child = load(path)
    parent = load(path.parent.parent / child['parent_artifact_id'] / 'bundle.json')
    assert digest(parent['result']) == child['parent_result_sha256']
    r = child['result']
    assert r['method_version'] in ('s3-relative-value.fixed-endpoint.v1','s3-relative-value.fixed-endpoint.v1.1')
    start = datetime.fromisoformat(parent['result']['decision_at'])
    at = datetime.fromisoformat(r['decision_at'])
    end = datetime.fromisoformat(r['forecast_endpoint'])
    assert (end.year, end.month, end.hour, end.minute, end.second) == (
        start.year+5, start.month, start.hour, start.minute, start.second)
    assert end.day == (28 if (start.month, start.day) == (2, 29) else start.day)
    assert start <= at < end and r['original_decision_at'] == start.isoformat()
    with localcontext() as ctx:
        ctx.prec = 34
        years = D(5)*D(str((end-at).total_seconds()))/D(str((end-start).total_seconds()))
    assert D(r['remaining_years']) == years
    original = {c['symbol']: c for c in parent['result']['cards']}
    checked = 0
    berkshire_checked = sum(verify_berkshire(c) for c in original.values()
                            if c.get('valuation_method') == 'BRK_CLASS_B_OPERATING_EARNINGS_V1')
    with localcontext() as ctx:
        ctx.prec = 34
        for card in r['cards']:
            old = original.pop(card['symbol'])
            assert card.get('quality') == old.get('quality')
            if not card['scenarios']:
                assert card['rank'] is None and 'conditional_value_range' not in card
                continue
            price = D(card['current_price_evidence']['price'])
            prev = D(old['cross_checks']['observed_price']['values']['observed_price'])
            cases = [(a, old['scenarios'][k], card['assumptions']['required_return'])
                     for k,a in card['scenarios'].items()]
            for key in ('sensitivity', 'joint_sensitivity'):
                assert len(card[key]) == len(old[key])
                cases += [(a['result'], b['result'], a.get('required_return', card['assumptions']['required_return']))
                          for a, b in zip(card[key], old[key]) if a['result']]
            for actual, prior, rate in cases:
                total = D(prior['terminal_wealth'])
                assert actual['terminal_wealth'] == prior['terminal_wealth'] and D(actual['dividends_cash']) == 0
                checks = dict(total_return=total/price-1, annualized_return=(total/price)**(1/years)-1,
                              conditional_value=total/(1+D(rate))**years)
                checks['margin_to_conditional_value'] = 1-price/checks['conditional_value']
                if prev == price and years == 5:
                    assert actual == prior
                for field, value in checks.items():
                    assert abs(D(actual[field])-value) < D('0.00000002'), (card['symbol'], field)
                checked += 1
            pairs = {x['against']: x for x in card['relative_return']}
            for other in r['cards']:
                if other is card or not other['scenarios']:
                    continue
                expected = D(card['scenarios']['base']['annualized_return'])-D(other['scenarios']['base']['annualized_return'])
                assert D(pairs[other['symbol']]['annualized_base_difference']) == expected
    assert not original
    return dict(artifact_id=path.parent.name, parent_artifact_id=child['parent_artifact_id'],
                cases=checked, berkshire_parent_cases=berkshire_checked,
                status='PASS' if checked else 'NO_QUALIFIED_CASES')


if __name__ == '__main__':
    print(json.dumps(verify(Path(sys.argv[1]).absolute())))
