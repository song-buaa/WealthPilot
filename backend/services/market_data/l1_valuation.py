"""Bind existing valuation inputs in the L1 research path."""
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from app.shared_sqlite import private_file

from .evidence import record, stamp
from .financial_research import annual_owner_cash_history
from .quality_valuation import bind_country_risk_assumption_source, bind_treasury_terminal_anchor


SOURCE_ROOT_ENV = 'WEALTHPILOT_L1_VALUATION_SOURCE_ROOT'
SOURCE_FILES = {'treasury': ('a2', 'treasury.original'), 'country_risk': ('a3', 'nyu-country-risk.original')}


def _read_private(path, limit):
    with private_file(path).open('rb') as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('L1_VALUATION_SOURCE_TOO_LARGE')
    return raw


def read_inputs(symbol, observed):
    """Read a private 4.1 source receipt and its existing original bytes."""
    if not observed or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}:US', symbol):
        return {}
    root = os.getenv(SOURCE_ROOT_ENV)
    if not root or not Path(root).is_absolute():
        return {key: dict(status='UNKNOWN', reason='SOURCE_ROOT_UNCONFIGURED') for key in SOURCE_FILES}
    try:
        if date.fromisoformat(observed).isoformat() != observed:
            return {}
        directory = Path(root) / symbol / observed
        manifest = json.loads(_read_private(directory / 'manifest.json', 128 * 1024))
        if manifest['symbol'] != symbol or manifest['observation_date'] != observed:
            raise ValueError('L1_VALUATION_SOURCE_IDENTITY_MISMATCH')
    except FileNotFoundError:
        return {key: dict(status='MISSING', reason='NO_SOURCE_RECEIPT_FOR_OBSERVATION') for key in SOURCE_FILES}
    except (KeyError, OSError, TypeError, ValueError):
        return {key: dict(status='UNKNOWN', reason='SOURCE_RECEIPT_INVALID') for key in SOURCE_FILES}
    sources = {}
    for key, (field, filename) in SOURCE_FILES.items():
        if field not in manifest:
            continue
        item = manifest[field]
        if not isinstance(item, dict):
            sources[key] = dict(status='UNKNOWN', reason='SOURCE_RECEIPT_INVALID')
            continue
        sources[key] = item
        if item.get('status') != 'VERIFIED':
            continue
        try:
            item['original'] = _read_private(directory / filename, 4 * 1024 * 1024)
        except FileNotFoundError:
            sources[key] = dict(status='MISSING', reason='SOURCE_ORIGINAL_MISSING')
        except (OSError, ValueError):
            sources[key] = dict(status='UNKNOWN', reason='SOURCE_ORIGINAL_INVALID')
    return sources


def observation_date(assessment):
    prices = [row for row in assessment['financial_research']['records']
              if row.get('field') == 'observed_price' and row.get('quality') == 'valid']
    return prices[0]['period'] if len(prices) == 1 else None


def bind_inputs(assessment, sources):
    """Consume server-owned source receipts before any model or valuation proposal."""
    as_of = stamp(assessment['as_of'])
    observation = observation_date(assessment)
    status = {}
    bound = {**assessment, 'records': list(assessment['records'])}
    for name in ('treasury', 'country_risk'):
        item = sources.get(name) or {}
        state = item.get('status', 'MISSING')
        if state not in {'VERIFIED', 'MISSING', 'STALE', 'CONFLICT', 'UNKNOWN', 'NOT_APPLICABLE'}:
            state = 'UNKNOWN'
        reason = item.get('reason')
        if not isinstance(reason, str) or not re.fullmatch(r'[A-Z0-9_]{1,100}', reason):
            reason = 'SOURCE_QUALIFIED' if state == 'VERIFIED' else 'SOURCE_NOT_AVAILABLE'
        if state == 'VERIFIED':
            original = item.get('original')
            known_at = stamp(item.get('known_at'))
            fetched_at = stamp(item.get('fetched_at'))
            if not isinstance(original, bytes) or sha256(original).hexdigest() != item.get('source_sha256'):
                state, reason = 'CONFLICT', 'SOURCE_HASH_MISMATCH'
            elif not known_at or not fetched_at or not as_of:
                state, reason = 'UNKNOWN', 'SOURCE_CLOCK_UNVERIFIED'
            elif not known_at <= fetched_at <= as_of:
                state, reason = 'CONFLICT', 'SOURCE_CLOCK_CONFLICT'
            else:
                try:
                    if name == 'treasury':
                        source = item.get('record') or {}
                        if (not observation or source.get('period') != observation or known_at < stamp(observation)
                                or source.get('fetched_at') != item['fetched_at']
                                or source.get('source_url') != item.get('source_url')
                                or source.get('symbol') != assessment['symbol']):
                            raise ValueError('QUALIFIED_SAME_SESSION_USD_TREASURY_ANCHOR_REQUIRED')
                        fact = record('research', {
                            **{k: source[k] for k in ('record_id', 'source', 'source_url', 'symbol',
                               'period', 'data_as_of', 'published_at', 'fetched_at')},
                            'content': '美国财政部日度国债收益率曲线，五年期美元国债观察。',
                        }, at=as_of, symbol=assessment['symbol'])
                        if fact['quality'] != 'valid':
                            raise ValueError('TREASURY_EVIDENCE_NOT_QUALIFIED')
                        bound['records'].append(fact)
                        bind_treasury_terminal_anchor(bound, original=original, record_id=fact['record_id'])
                    else:
                        bind_country_risk_assumption_source(bound, original=original,
                            source_url=item['source_url'], fetched_at=item['fetched_at'])
                        if known_at < stamp(bound['country_risk_assumption_source']['vintage']):
                            raise ValueError('COUNTRY_RISK_CLOCK_CONFLICT')
                except ValueError as error:
                    state, reason = 'CONFLICT', str(error)
                except (KeyError, TypeError, ArithmeticError):
                    state, reason = 'UNKNOWN', 'VALUATION_SOURCE_BINDING_FAILED'
        status[name] = {'status': state, 'reason': reason}
        if state == 'VERIFIED':
            status[name].update({k: item[k] for k in ('known_at', 'fetched_at', 'source_sha256', 'source_url')})
            if name == 'treasury':
                status[name]['observation_date'] = observation
                status[name]['published_at'] = fact['published_at']
            else:
                status[name]['vintage'] = bound['country_risk_assumption_source']['vintage']
    has_history = bool(annual_owner_cash_history(assessment))
    status['annual_cash_bridge'] = {
        'status': 'VERIFIED' if has_history else 'MISSING',
        'reason': 'SAME_FILING_THREE_YEARS_QUALIFIED' if has_history else 'SAME_FILING_THREE_YEARS_NOT_QUALIFIED',
    }
    assessment['l1_valuation_sources'] = status
    if status['treasury']['status'] == 'VERIFIED':
        assessment['records'] = bound['records']
    if all(item['status'] == 'VERIFIED' for item in status.values()):
        assessment['treasury_terminal_anchor'] = bound['treasury_terminal_anchor']
        assessment['country_risk_assumption_source'] = bound['country_risk_assumption_source']
