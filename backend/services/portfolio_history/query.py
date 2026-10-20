"""Bounded, no-I/O historical queries over one original SQLite read transaction."""
from datetime import datetime, timezone
import hashlib
import json
from sqlalchemy import inspect, text
from .models import HistoryEvent, HistoryImport
from .sources import MAX_EVENTS, number, fail, timestamp, window_membership
from .imports import owner_scope, qualified_coverage
from .calculation import attribution


def history_account_ref(portfolio_id, broker, account):
    return 'history_account_'+hashlib.sha256(repr((portfolio_id,broker,account)).encode()).hexdigest()[:24]


def position_key(row):
    from .sources import instrument
    evidence=json.loads(row.classification_evidence_json or '{}')
    if evidence.get('con_id'):
        return 'conid:'+str(evidence['con_id'])
    try:
        return instrument(dict(symbol=row.symbol or row.ticker,currency=row.currency,
            assetCategory=row.broker_security_type or 'STK',listingExchange=evidence.get('primary_exchange') or evidence.get('exchange'),
            isin=evidence.get('isin')))[0]
    except ValueError:return None


def valuation(db, portfolio_id, broker, account, key, *, at):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.repository import publication_status
    from backend.services.execution_plan.market_hours import valuation_quality
    alias='snowball' if broker=='ibkr' else broker
    rows=db.query(Position).filter_by(portfolio_id=portfolio_id,broker=alias,broker_account_id=account).all()
    matching=[p for p in rows if position_key(p)==key]
    if len(matching)!=1:return None
    row=matching[0]
    run=db.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id,broker=alias,
        account_id=account,sync_source=row.sync_source,status='success').order_by(PositionSnapshotRun.id.desc()).first()
    meta=json.loads(run.context_json or '{}') if run else {}
    source_time=meta.get('price_as_of');positions_time=meta.get('positions_as_of')
    if not source_time or not positions_time:qualified=False
    else:
        market=(row.symbol or '').rsplit(':',1)[-1]
        q=valuation_quality(source_time,market,at=at,basis=meta.get('price_time_basis'))
        try:
            stamp=datetime.fromisoformat(timestamp(positions_time))
            recent=0<=(at-stamp).total_seconds()<=86400
        except ValueError:recent=False
        qualified=q['status'] in {'intraday','recent_close'} and recent and publication_status(db,run)=='matched'
    price_effective=source_time
    if qualified and len(source_time)==10:
        try:
            import exchange_calendars as calendars
            price_effective=calendars.get_calendar(q['calendar']).schedule.loc[source_time,'close'].isoformat()
        except (ValueError,KeyError):
            qualified=False;price_effective=None
    fx=None
    if row.original_currency==row.currency and row.fx_rate_date and row.fx_rate_to_cny and row.fx_rate_to_cny>0:
        fx=dict(rate=number(row.fx_rate_to_cny,positive=True),rate_date=row.fx_rate_date,source='stored_U0_FX',pair=row.currency+'/CNY')
    return dict(quantity=number(row.quantity),currency=row.original_currency,market_value=number(row.original_value),
        broker_cost_basis=number(row.cost_price*row.quantity),broker_reference_pnl=number(row.profit_loss_original_value),
        as_of=source_time or None,positions_as_of=positions_time,qualified=qualified,fx=fx,
        account_synced_at=run.finished_at.isoformat() if run and run.finished_at else None,
        price_as_of=source_time,valuation_effective_at=price_effective,price_time_basis=meta.get('price_time_basis'),quality='trusted_stored_source' if qualified else 'unknown_or_stale')


def query(db, portfolio_id, *, account_ref=None, symbol=None, start=None, end=None, limit=50, offset=0,
          synthetic=False, at=None):
    if start:start=timestamp(start)
    if end:end=timestamp(end)
    if symbol:
        from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
        trusted=TRUSTED_INSTRUMENTS.get(symbol)
        if trusted:symbol='conid:'+str(trusted.con_id)
    # MCP readonly_snapshot already BEGINs; Web/PEER must use the same read view.
    if not db.connection().connection.driver_connection.in_transaction:db.execute(text('BEGIN'))
    result=dict(schema_version='wealthpilot.trade-history.v1',synthetic=synthetic,
        queried_at=(at or datetime.now(timezone.utc)).isoformat(),snapshot_id=None,
        status='partial',issues=[],uncertain_events=[],accounts=[],items=[],attribution=[],coverage=[],total=0,next_offset=None,
        scope='Stored broker execution/cashflow facts. No broker refresh, account inference or transaction. Attribution uses full available pre-window history; item dates only filter presentation.')
    tables=inspect(db.connection()).get_table_names()
    if not {'portfolio_history_events','portfolio_history_imports'}<=set(tables):
        result['issues']=['HISTORY_SCHEMA_NOT_AVAILABLE'];return result
    imports=db.query(HistoryImport).filter_by(portfolio_id=portfolio_id).limit(MAX_EVENTS+1).all()
    if len(imports)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
    owners=sorted({(r.broker,r.account_id) for r in imports})
    allowed=[]
    for broker,account in owners:
        ref=history_account_ref(portfolio_id,broker,account)
        if account_ref and ref!=account_ref:continue
        try:owner_scope(db,portfolio_id,broker,account)
        except ValueError:
            result['issues'].append('HISTORY_OWNER_UNPROVEN');continue
        allowed.append((broker,account))
        result['accounts'].append(dict(account_ref=ref,broker=broker))
    if account_ref and not allowed:fail('HISTORY_ACCOUNT_NOT_FOUND')
    events=db.query(HistoryEvent).filter_by(portfolio_id=portfolio_id).order_by(
        HistoryEvent.executed_at,HistoryEvent.external_id).limit(MAX_EVENTS+1).all()
    if len(events)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
    groups={};shown=[];contradicted_execution=set();contradicted_cash=set()
    allowed=set(allowed)
    uncertain_groups=set()
    for r in events:
        if (r.broker,r.account_id) not in allowed:continue
        e=json.loads(r.payload_json)
        if symbol and symbol not in {r.symbol,r.instrument_key}:continue
        group=(r.broker,r.account_id,r.instrument_key)
        until=window_membership(e,end=end)
        visible=window_membership(e,start=start,end=end)
        if until=='uncertain' or visible=='uncertain':
            result['issues'].append('HISTORY_TIME_BOUNDARY_UNCERTAIN')
            result['uncertain_events'].append(dict(kind=r.kind,external_id=r.external_id,
                account_ref=history_account_ref(portfolio_id,r.broker,r.account_id),reason_code='HISTORY_TIME_BOUNDARY_UNCERTAIN'))
        if until=='uncertain':uncertain_groups.add(group)
        if until!='inside':continue
        groups.setdefault(group,[]).append(e)
        if visible!='inside':continue
        public={k:e.get(k) for k in ('kind','external_id','instrument_key','symbol','currency','sec_type',
            'executed_at','time_precision','source_time_raw','source_timezone','source_local_date','source_date_semantics','source_time_evidence','fx_date','fx_date_basis','side','quantity','price','multiplier','commission','commission_currency',
            'taxes','broker_realized_pnl','broker_cost_basis','broker_order_id','perm_id','amount','fx')}
        public.update(account_ref=history_account_ref(portfolio_id,r.broker,r.account_id),broker=r.broker,
            commission_status='KNOWN' if e.get('commission') is not None and e.get('commission_currency') else 'UNKNOWN',
            taxes_status='KNOWN' if e.get('taxes') is not None else 'UNKNOWN',
            evidence_sources=[dict(import_id=i.id,source=i.source,imported_at=i.imported_at,
                source_sha256=json.loads(i.evidence_json)['source_sha256']) for i in imports if i.id in e.get('evidence_import_ids',[])],
            field_evidence={k:v for k,v in e.get('field_evidence',{}).items() if k in public},
            source=next(i.source for i in imports if i.id==r.import_id),
            imported_at=next(i.imported_at for i in imports if i.id==r.import_id),
            local_order_id=_local_order(db,portfolio_id,r.broker,r.account_id,e))
        shown.append(public)
    for (broker,account,key),facts in groups.items():
        if len(groups)>200:fail('HISTORY_QUERY_LIMIT')
        matching=[r for r in imports if (r.broker,r.account_id)==(broker,account)]
        evidence=[json.loads(r.evidence_json) for r in matching]
        # An older statement cannot continue to prove completeness when newly
        # imported facts inside its claimed interval were absent from that file.
        for imported,proof in zip(matching,evidence):
            c=proof['coverage'];ids=proof.get('event_identities',[])
            in_scope=c['scope']=='account' or key in c['instrument_keys']
            missing=[e for e in facts if in_scope and window_membership(e,c['coverage_start'],c['coverage_end'])!='outside'
                and [e['kind'],e['external_id']] not in ids]
            if any(e['kind']=='EXECUTION' for e in missing):
                c['complete_executions']=False;contradicted_execution.add(imported.id)
            if any(e['kind']!='EXECUTION' for e in missing):
                c['complete_cashflows']=False;contradicted_cash.add(imported.id)
            if missing:result['issues'].append('REPORT_COVERAGE_CONTRADICTED_BY_IMPORTED_FACTS')
        current=valuation(db,portfolio_id,broker,account,key,at=at or datetime.now(timezone.utc))
        if end and current and (not current['positions_as_of'] or
                datetime.fromisoformat(timestamp(current['positions_as_of']))>=datetime.fromisoformat(end)):current=None
        calculated=attribution(facts,evidence,current,boundary_uncertain=(broker,account,key) in uncertain_groups)
        calculated.update(account_ref=history_account_ref(portfolio_id,broker,account),broker=broker,
            instrument_key=key,symbol=facts[0]['symbol'])
        result['attribution'].append(calculated)
        result['issues'].extend(calculated['issues'])
    for r in imports:
        if (r.broker,r.account_id) not in allowed:continue
        e=json.loads(r.evidence_json);declared=e['coverage'];c,reasons=qualified_coverage(e)
        if symbol and c['scope']=='instruments' and not any(
                g[2] in c['instrument_keys'] for g in groups if g[:2]==(r.broker,r.account_id)):continue
        result['coverage'].append(dict(import_id=r.id,account_ref=history_account_ref(portfolio_id,r.broker,r.account_id),
            broker=r.broker,source=r.source,imported_at=r.imported_at,coverage_start=r.coverage_start,
            coverage_end=r.coverage_end,scope=c['scope'],instrument_keys=c['instrument_keys'],
            declared_complete_executions=declared['complete_executions'],declared_complete_cashflows=declared['complete_cashflows'],
            complete_executions=c['complete_executions'] and r.id not in contradicted_execution,
            complete_cashflows=c['complete_cashflows'] and r.id not in contradicted_cash,
            corporate_actions_reviewed=c['corporate_actions_reviewed'],opening_zero_keys=c['opening_zero_keys'],
            completeness_authority=e['completeness_authority'],source_sha256=e['source_sha256'],
            issues=sorted(set(e['source_metadata'].get('issues',[])+reasons))))
    result['total']=len(shown);result['items']=shown[offset:offset+limit]
    if offset+limit<len(shown):result['next_offset']=offset+limit
    if not groups:result['issues'].append('HISTORICAL_COVERAGE_UNKNOWN_NO_FACTS')
    result['issues']=sorted(set(result['issues']))
    result['status']='partial' if result['issues'] else 'ok'
    return result


def _local_order(db, pid, broker, account, event):
    from backend.services.action.models import OrderRecord
    # Legacy owner=NULL never gets claimed or rewritten, even on matching orderRef.
    q=db.query(OrderRecord).filter_by(portfolio_id=pid,broker_name=broker,broker_account_id=account)
    matches=[o for o in q if o.side==event.get('side') and o.symbol==event['symbol'] and (
        (event.get('order_ref') and o.id==event['order_ref']) or
        (event.get('perm_id') not in (None,'','0') and o.broker_order_id==event['perm_id']))]
    return matches[0].id if len(matches)==1 else None
