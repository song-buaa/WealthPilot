"""Strict, offline IBKR Flex execution/cash-flow readers and original API reuse.

Reports/SDK are authorities. Orders, closed lots and holding snapshots are not
executions. No credentials, external fetch, trading calls or DB writes here.
"""
import csv
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import io
import json
import re
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

MAX_EVENTS = 10000
MAX_REPORT = 10 * 1024 * 1024
ERRORS = {'HISTORY_INVALID_REPORT', 'HISTORY_IDENTITY_CONFLICT', 'HISTORY_OWNER_UNPROVEN',
          'HISTORY_ACCOUNT_AMBIGUOUS', 'HISTORY_EVENT_CONFLICT', 'HISTORY_CORRECTION_REQUIRES_REVIEW',
          'HISTORY_COVERAGE_INVALID', 'HISTORY_SCHEMA_REQUIRED', 'HISTORY_QUERY_LIMIT', 'HISTORY_ACCOUNT_NOT_FOUND'}


def fail(code='HISTORY_INVALID_REPORT'):
    raise ValueError(code)


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('1e20') or (positive and result <= 0):fail()
        return '0' if result==0 else format(result.normalize(), 'f')
    except (InvalidOperation, TypeError):fail()


def timestamp(value, zone=None, *, date_only=False):
    text = str(value).strip()
    try:
        if re.fullmatch(r'\d{8}|\d{4}-\d{2}-\d{2}', text):
            if date_only:return date.fromisoformat(text).isoformat()
            fail()  # No invented midnight precision for executions/coverage.
        stamp = datetime.fromisoformat(text.replace(';', 'T'))
        if stamp.tzinfo is None:
            if not zone:fail()
            tz = ZoneInfo(zone)
            first, second = stamp.replace(tzinfo=tz, fold=0), stamp.replace(tzinfo=tz, fold=1)
            if first.utcoffset() != second.utcoffset():fail()  # Ambiguous/nonexistent local time.
            if first.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != stamp:fail()
            stamp = first
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, KeyError):fail()


def instrument(row):
    from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
    conid = str(row.get('conid') or row.get('con_id') or '').strip()
    isin = str(row.get('isin') or '').strip().upper()
    symbol = str(row.get('symbol') or '').strip().upper()
    currency = str(row.get('currency') or '').strip().upper()
    sec_type = str(row.get('assetCategory') or row.get('sec_type') or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{3}', currency) or not re.fullmatch(r'[A-Z0-9:._-]{1,50}', symbol):fail()
    for trusted in TRUSTED_INSTRUMENTS.values():
        if (conid and conid == str(trusted.con_id)) or (isin and isin == trusted.isin):
            if (conid and conid != str(trusted.con_id)) or (isin and isin != trusted.isin) or currency != trusted.currency:
                fail('HISTORY_IDENTITY_CONFLICT')
            if symbol.split(':',1)[0] not in {trusted.symbol,trusted.local_symbol,trusted.user_alias}:
                fail('HISTORY_IDENTITY_CONFLICT')
            if ':' in symbol and symbol.rsplit(':',1)[1] not in {'LSE','LSEETF','UK'}:
                fail('HISTORY_IDENTITY_CONFLICT')
            if sec_type not in {'STK','ETF'}:fail('HISTORY_IDENTITY_CONFLICT')
            return f'conid:{trusted.con_id}', trusted.local_symbol+':LSE', currency, sec_type
    if conid:
        if not conid.isdigit() or int(conid) <= 0:fail()
        return 'conid:'+str(int(conid)), symbol, currency, sec_type
    # Without a stable ID the market-qualified listing must be explicit.
    market = str(row.get('listingExchange') or row.get('listing_exchange') or '').upper()
    if not market or market in {'SMART','NONE'} or not re.fullmatch(r'[A-Z0-9._-]{1,20}',market):fail()
    return f'listing:{symbol}:{market}:{currency}:{sec_type}', symbol+':'+market, currency, sec_type


def event(row, kind, external_id, executed_at):
    key, symbol, currency, sec_type = instrument(row)
    if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', str(external_id or '')):fail()
    result = dict(kind=kind,external_id=str(external_id),instrument_key=key,symbol=symbol,
        currency=currency,sec_type=sec_type,executed_at=executed_at,
        time_precision='date' if len(executed_at)==10 else 'instant',fx=None)
    return result


def parse_flex(data, account, *, zone):
    """XML or plain single-section CSV at EXECUTION detail, exact account filter.

    Exported source timezone is mandatory for naive execution timestamps. The
    caller must separately review any coverage/filter/opening evidence.
    """
    if not data or len(data)>MAX_REPORT:fail()
    try:text = data.decode('utf-8-sig')
    except UnicodeDecodeError:fail()
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():fail()
    rows, cash, periods, issues, found = [], [], [], set(), False
    try:
        if text.lstrip().startswith('<'):
            root = ET.fromstring(text)
            for statement in root.iter('FlexStatement'):
                owner = statement.get('accountId')
                if owner != account:continue
                found=True
                start, end = statement.get('fromDate'), statement.get('toDate')
                if not start or not end:fail('HISTORY_COVERAGE_INVALID')
                if start and end:
                    periods.append((date.fromisoformat(start),date.fromisoformat(end)))
                for trade in statement.iter('Trade'):
                    row = dict(trade.attrib)
                    if row.get('accountId',owner) != account:fail('HISTORY_OWNER_UNPROVEN')
                    rows.append(row)
                for transaction in statement.iter('CashTransaction'):
                    row = dict(transaction.attrib)
                    if row.get('accountId',owner) != account:fail('HISTORY_OWNER_UNPROVEN')
                    cash.append(row)
                if list(statement.iter('CorporateAction')):issues.add('CORPORATE_ACTIONS_UNSUPPORTED')
        else:
            reader = csv.DictReader(io.StringIO(text))
            aliases = {'ClientAccountID':'accountId','Account ID':'accountId','CurrencyPrimary':'currency',
                'Currency':'currency','AssetClass':'assetCategory','Symbol':'symbol','Conid':'conid',
                'ISIN':'isin','ListingExchange':'listingExchange','IBExecID':'ibExecID',
                'IB Execution ID':'ibExecID','DateTime':'dateTime','Buy/Sell':'buySell','Quantity':'quantity',
                'TradePrice':'tradePrice','IBCommission':'ibCommission','IBCommissionCurrency':'ibCommissionCurrency',
                'LevelOfDetail':'levelOfDetail','IBOrderID':'ibOrderID','OrderReference':'orderReference',
                'FifoPnlRealized':'fifoPnlRealized','CostBasis':'costBasis','Taxes':'taxes','Multiplier':'multiplier'}
            if len(reader.fieldnames or [])!=len(set(reader.fieldnames or [])):fail()
            for original in reader:
                if None in original:fail()
                row = {aliases.get(k,k):v for k,v in original.items()}
                if row.get('accountId')==account:rows.append(row);found=True
            issues.add('CSV_CASHFLOWS_NOT_INCLUDED')
    except (ET.ParseError, csv.Error, ValueError, TypeError):fail()
    if not found:fail('HISTORY_OWNER_UNPROVEN')
    if len(rows)+len(cash)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
    events = []
    for row in rows:
        if row.get('levelOfDetail','').upper()!='EXECUTION':fail()
        if row.get('origTradeID') or row.get('transactionType','ExchTrade') not in {'ExchTrade','Trade'}:
            fail('HISTORY_CORRECTION_REQUIRES_REVIEW')
        e = event(row,'EXECUTION',row.get('ibExecID'),timestamp(row.get('dateTime'),zone))
        side = str(row.get('buySell','')).upper()
        if side not in {'BUY','SELL'}:fail()
        qty = Decimal(number(row.get('quantity')))
        if qty==0 or (side=='BUY' and qty<0):fail()
        e.update(side=side,quantity=number(abs(qty),positive=True),price=number(row.get('tradePrice'),positive=True),
            multiplier=number(row.get('multiplier') or 1,positive=True),
            commission=number(-Decimal(number(row['ibCommission']))) if row.get('ibCommission') not in (None,'') else None,
            commission_currency=row.get('ibCommissionCurrency') or None,
            taxes=number(-Decimal(number(row['taxes']))) if row.get('taxes') not in (None,'') else None,
            broker_realized_pnl=number(row['fifoPnlRealized']) if row.get('fifoPnlRealized') not in (None,'') else None,
            broker_cost_basis=number(row['costBasis']) if row.get('costBasis') not in (None,'') else None,
            broker_order_id=row.get('ibOrderID') or None,perm_id=None,order_ref=row.get('orderReference') or None)
        events.append(e)
    for row in cash:
        types={'Dividends':'DIVIDEND','Withholding Tax':'INCOME_TAX','Broker Interest Received':'INTEREST',
               'Other Fees':'FEE'}
        kind = types.get(row.get('type'))
        if not kind:
            issues.add('UNSUPPORTED_CASHFLOW_TYPE');continue
        # Account-wide fees without security identity cannot be assigned to a holding.
        if not row.get('symbol'):
            issues.add('UNALLOCATED_CASHFLOW');continue
        e=event(row,kind,row.get('transactionID'),timestamp(row.get('dateTime') or row.get('date'),zone,date_only=True))
        if kind=='FEE':issues.add('FEE_ALLOCATION_UNVERIFIED')
        e['amount']=number(row.get('amount'));events.append(e)
    bounds=[(timestamp(a.isoformat()+'T00:00:00',zone),
        timestamp((b+timedelta(days=1)).isoformat()+'T00:00:00',zone)) for a,b in periods]
    return events,dict(report_periods=[(a.isoformat(),b.isoformat()) for a,b in periods],
        report_bounds_utc=bounds,issues=sorted(issues))


def recent_ibkr(adapter, account):
    """Reuse account-filtered B0 adapter. Recent API response is NEVER full coverage."""
    if getattr(adapter,'_account_id',None)!=account:fail('HISTORY_OWNER_UNPROVEN')
    rows = adapter.list_execution_details()
    if len(rows)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
    result=[]
    for row in rows:
        if row.get('observed_account_id')!=account:fail('HISTORY_OWNER_UNPROVEN')
        if row.get('pending_price_revision'):fail('HISTORY_CORRECTION_REQUIRES_REVIEW')
        e=event(row,'EXECUTION',row.get('exec_id'),timestamp(row.get('timestamp')))
        side={'BOT':'BUY','SLD':'SELL','BUY':'BUY','SELL':'SELL'}.get(row.get('side'))
        if not side:fail()
        e.update(side=side,quantity=number(row.get('shares'),positive=True),price=number(row.get('price'),positive=True),
            multiplier=number(row.get('multiplier') or 1,positive=True),commission=number(row['commission']) if row.get('commission') is not None else None,
            commission_currency=row.get('commission_currency'),taxes=None,broker_realized_pnl=None,
            broker_cost_basis=None,broker_order_id=str(row.get('order_id') or '') or None,
            perm_id=str(row.get('perm_id') or '') or None,order_ref=row.get('order_ref') or None)
        result.append(e)
    return result


def historical_fx(events, service):
    """Optional import-time enrichment using existing FXService, never query-time I/O."""
    result=[]
    for event_ in events:
        e=dict(event_);day=e['executed_at'][:10]
        try:
            quote=service.get_quote(e['currency'],'CNY',day)
            if quote.quality=='historical' and quote.rate_date==day and Decimal(number(quote.rate,positive=True)):
                e['fx']=dict(rate=number(quote.rate,positive=True),rate_date=day,source=quote.source,pair=e['currency']+'/CNY')
        except ValueError:pass
        result.append(e)
    return result
