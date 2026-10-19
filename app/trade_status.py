"""Read-only original OrderRecord/SnapshotRun projection. No broker I/O."""
from datetime import datetime, timezone
from types import SimpleNamespace
from sqlalchemy import text
from app.fill_sync import decode, orders, sync_state, lease_expired


def query(db, portfolio_id, *, order_id=None, batch_id=None, symbol=None, limit=10, synthetic=False):
    from backend.api.broker_sync import safe_sync_reason
    # Python's SQLite legacy transaction mode does not BEGIN for SELECT.
    # The API and MCP must compare order/proof/publication in one read view.
    if not db.connection().connection.driver_connection.in_transaction:
        db.execute(text('BEGIN'))
    selected=[r for r in orders(db,portfolio_id) if (not order_id or r['id']==order_id)
        and (not batch_id or r['batch_id']==batch_id) and (not symbol or r['symbol']==symbol)]
    items=[]
    for row in list(reversed(selected))[:limit]:
        p=decode(row.get('fill_sync_json'))
        run=None
        if p.get('last_run_id'):
            run=db.execute(text('SELECT * FROM position_snapshot_runs WHERE id=:i AND portfolio_id=:p AND account_id=:a AND broker=:b'),
                dict(i=p['last_run_id'],p=portfolio_id,a=row['broker_account_id'],
                     b='snowball' if row['broker_name']=='ibkr' else row['broker_name'])).mappings().first()
        state=sync_state(row,run)
        from services.broker_sync.models import PositionSnapshotRun
        from services.broker_sync.repository import publication_status
        latest_good=db.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id,
            broker='snowball' if row['broker_name']=='ibkr' else row['broker_name'],
            account_id=row['broker_account_id'],sync_source='api',status='success').order_by(PositionSnapshotRun.id.desc()).first()
        publication=publication_status(db,latest_good)
        if state=='SYNCED' and publication!='matched':state='SYNC_UNVERIFIED'
        evidence=p.get('executions',{})
        stamps=sorted(v['timestamp'] for v in evidence.values() if v.get('timestamp'))
        fees=p.get('actual_commissions',{})
        only_fee=next(iter(fees.items())) if len(fees)==1 else (None,None)
        proof=p.get('coverage',{})
        safe_reason=safe_sync_reason(SimpleNamespace(**run)) if run else None
        if lease_expired(run):safe_reason='SYNC_LEASE_EXPIRED'
        items.append(dict(order_id=row['id'],batch_id=row['batch_id'],leg_id=row['batch_leg_id'],
            symbol=row['symbol'],side=row['side'],broker=row['broker_name'],
            broker_order_id=row['broker_order_id'],trade_status=row['status'],
            ordered_quantity=row['quantity'],filled_quantity=row['filled_quantity'],
            average_filled_price=str(row['avg_filled_price']) if row['avg_filled_price'] is not None else None,
            currency=p.get('currency'),
            actual_filled_at=stamps[-1] if stamps else p.get('broker_filled_at'),
            observed_at=str(row['last_synced_at']) if row['last_synced_at'] else None,
            actual_commission=dict(status='KNOWN' if fees else 'UNKNOWN',amount=only_fee[1],currency=only_fee[0],amounts_by_currency=fees),
            asset_sync=dict(status=state,run_id=run['id'] if run else None,
                positions_verified=not p.get('evidence_conflict') and bool(proof.get('positions_verified')) and publication=='matched' and proof.get('target_quantity')==row['filled_quantity'],
                cash_verified=not p.get('evidence_conflict') and bool(proof.get('cash_verified')) and publication=='matched' and proof.get('target_quantity')==row['filled_quantity'],
                covered_quantity=p.get('covered_quantity',0),target_quantity=row['filled_quantity'],
                position_updated_at=latest_good.finished_at.isoformat() if latest_good and latest_good.finished_at else None,
                last_successful_run_id=latest_good.id if latest_good else None,publication_status=publication,
                reason_code=safe_reason,issues=sorted(set(proof.get('issues',[])
                    +(['EXECUTION_EVIDENCE_DISPUTED'] if p.get('evidence_conflict') else [])
                    +(['SYNC_PUBLICATION_'+publication.upper()] if publication!='matched' else []))),data_quality=decode_quality(run))))
    return dict(schema_version='wealthpilot.trade-sync.v1',synthetic=synthetic,
        queried_at=datetime.now(timezone.utc).isoformat(),status='partial' if any(
            i['asset_sync']['status'] not in {'NOT_REQUIRED','SYNCED'} or i['trade_status']=='unknown' for i in items) else 'ok',
        scope='Stored broker observations and original publication coverage. No live refresh or trading operation.',items=items)


def decode_quality(run):
    import json
    if not run:return {'quality':'unavailable','issues':['NO_FILL_SYNC_ATTEMPT']}
    meta=json.loads(run['context_json'] or '{}')
    return {k:meta.get(k) for k in ('quality','issues','positions_as_of','cash_as_of','price_as_of','fx')}
