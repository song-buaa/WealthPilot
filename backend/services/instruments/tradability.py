"""Read-only account/product evidence in the original instruments domain.

No order/WhatIf invocation, cash calculation, persistence or credential handling.
A resolvable product, a past fill, trade=True or a PASS preview alone is not an
account permission grant. Current adapters do not prove positive permission.
"""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class TradabilityQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    broker: Literal['ibkr', 'tiger']
    account_ref: str | None = Field(default=None, pattern=r'^account_[0-9a-f]{20}$')
    symbol: str | None = Field(default=None, pattern=r'^[A-Z0-9][A-Z0-9.-]{0,29}:(US|HK|LSE|UK|LSEETF)$')
    con_id: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    isin: str | None = Field(default=None, pattern=r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$')

    @model_validator(mode='after')
    def identity(self):
        if not (self.symbol or self.con_id or self.isin):
            raise ValueError('INSTRUMENT_IDENTITY_REQUIRED')
        return self


def query(db, portfolio_id, arguments, *, adapter=None, synthetic=False, deadline=None):
    from app.models import Position
    from backend.services.action.ownership import stored_account_ref as account_ref
    from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
    request = TradabilityQuery.model_validate(arguments)
    broker = 'snowball' if request.broker == 'ibkr' else request.broker
    rows = db.query(Position).filter_by(portfolio_id=portfolio_id, broker=broker).all()
    owners = {account_ref(portfolio_id, r): str(r.broker_account_id) for r in rows
              if r.broker_account_id and r.sync_source == 'api'}
    selected = [ref for ref in owners if not request.account_ref or request.account_ref == ref]
    if request.account_ref and not selected:
        raise ValueError('ACCOUNT_NOT_FOUND')
    result = dict(schema_version='wealthpilot.tradability.v1', synthetic=synthetic,
        queried_at=datetime.now(timezone.utc).isoformat(), broker=request.broker,
        account_ref=selected[0] if len(selected)==1 else None,
        accounts=[{'account_ref': ref} for ref in sorted(owners)],
        tradability='UNKNOWN', purpose='BUY_STOCK_OR_ETF', instrument=None,
        supported_order_types=None, trading_hours=None, minimum_quantity=None,
        fees={'status':'UNKNOWN','amount':None,'currency':None},
        evidence_source='STORED_IDENTITY_ONLY', evidence_at=None,
        issues=['ACCOUNT_PERMISSION_UNPROVEN'],
        limitations=['Not an order authorization or a fill guarantee. No orders or WhatIf sent.'])
    if len(selected) != 1:
        result['issues'].append('EXPLICIT_ACCOUNT_REQUIRED')
        return result
    from backend.services.portfolio_history.imports import owner_scope
    try:
        owner_scope(db, portfolio_id, request.broker, owners[selected[0]])
    except ValueError:
        raise ValueError('ACCOUNT_NOT_FOUND') from None
    # All supplied identities must describe the same listing, never union matches.
    candidates=[]
    for t in TRUSTED_INSTRUMENTS.values():
        if request.con_id and t.con_id != request.con_id:continue
        if request.isin and t.isin != request.isin:continue
        if request.symbol:
            symbol, market=request.symbol.split(':')
            if market not in {'UK','LSE','LSEETF'} or symbol not in {t.user_alias,t.symbol,t.local_symbol}:continue
        candidates.append(t.to_dict())
    if len(candidates)==1 and request.broker=='ibkr':
        t=candidates[0];result['instrument']={k:t[k] for k in ('con_id','isin','symbol','local_symbol','currency','exchange','sec_type','long_name')}
        result['evidence_at']=t['verified_at']
    if adapter is None:
        result['issues'].append('LIVE_ACCOUNT_QUERY_NOT_CONFIGURED')
        return result
    expected=owners[selected[0]]
    if adapter.broker_name != request.broker or str(getattr(adapter,'_account_id','')) != expected:
        raise ValueError('ACCOUNT_NOT_FOUND')
    # Resolve immutable scalar scope then release SQLite read transaction for I/O.
    db.rollback()
    try:
        facts=adapter.instrument_details(request.model_dump(exclude={'account_ref','broker'}),
            **({'deadline':deadline} if request.broker=='ibkr' and deadline is not None else {}))
    except Exception:
        result['issues'].append('BROKER_QUERY_UNAVAILABLE')
        return result  # never expose raw SDK/account/network errors
    instrument=facts.get('instrument')
    if not instrument or instrument.get('sec_type') not in {'STK','ETF'}:
        result['issues'].append('PRODUCT_IDENTITY_UNPROVEN_OR_UNSUPPORTED')
        return result
    mismatch = (request.con_id and instrument.get('con_id')!=request.con_id or
                request.isin and instrument.get('isin')!=request.isin)
    if request.symbol:
        symbol,market=request.symbol.split(':')
        exchange=instrument.get('exchange')
        valid_market=({'US':{'US'},'HK':{'HK'},'LSE':{'LSEETF'},'UK':{'LSEETF'},'LSEETF':{'LSEETF'}}[market])
        mismatch = mismatch or symbol not in {instrument.get('symbol'),instrument.get('local_symbol')} or facts.get('market',exchange) not in valid_market
    if mismatch:
        result['instrument']=None
        result['issues'].append('INSTRUMENT_IDENTITY_CONFLICT')
        return result
    result.update(instrument=instrument, evidence_source=facts['source'], evidence_at=result['queried_at'],
        supported_order_types=facts.get('order_types'),trading_hours=facts.get('trading_hours'),
        minimum_quantity=facts.get('minimum_quantity'))
    # Negative product restrictions prove NO for this BUY purpose. Positive
    # product flags are not account permissions; adapters must not invent YES.
    if facts.get('buy_restricted') is True:
        result['tradability']='NO';result['issues']=['BROKER_PRODUCT_BUY_RESTRICTED']
    return result
