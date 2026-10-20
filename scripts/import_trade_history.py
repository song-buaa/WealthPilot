#!/usr/bin/env python3
"""Private, explicit historical import. Default validates only; no HTTP write API.

No order calls. Production apply/read-only broker access require separate approval.
"""
import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.append(str(ROOT/'backend'))


def main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.shared_sqlite import verify, private_file, writer_connection
    from pocs.poc02_wealth_mcp.reader import readonly_snapshot
    from backend.services.portfolio_history.imports import Coverage, owner_scope, import_batch
    from backend.services.portfolio_history.sources import parse_flex, recent_ibkr, historical_fx, dumps, MAX_REPORT, fail
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--contract',required=True)
    parser.add_argument('--portfolio-id',type=int,required=True);parser.add_argument('--account-id',required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--flex-file');source.add_argument('--recent-ibkr',action='store_true')
    parser.add_argument('--coverage',required=True,help='Private reviewed evidence JSON, never infer complete from empty rows')
    parser.add_argument('--historical-fx',action='store_true',help='Explicit import-time historical FXService reads, no latest fallback')
    parser.add_argument('--apply',action='store_true',help='Persist only B2 history facts; default no DB writes')
    args=parser.parse_args()
    contract=verify(args.database,args.contract,integrity=True)
    if contract['portfolio_id']!=args.portfolio_id:fail('HISTORY_OWNER_UNPROVEN')
    coverage=Coverage.model_validate_json(private_file(args.coverage,0o600).read_text())
    with readonly_snapshot(args.database,args.contract) as connection, Session(bind=connection,autoflush=False) as db:
        owner_scope(db,args.portfolio_id,'ibkr',args.account_id)
    metadata={}
    if args.flex_file:
        source_path=private_file(args.flex_file,0o600)
        if source_path.stat().st_size>MAX_REPORT:fail()
        data=source_path.read_bytes();source_sha=hashlib.sha256(data).hexdigest()
        events,metadata=parse_flex(data,args.account_id,zone=coverage.source_timezone)
        source_name='ibkr_flex'
    else:
        if os.environ.get('IBKR_READ_ONLY_MODE','true').lower()!='true' or os.environ.get('ENABLE_IBKR_LIVE_TRADING','false').lower()!='false':
            fail('HISTORY_OWNER_UNPROVEN')
        from backend.services.action.brokers.factory import get_broker_adapter
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            events=recent_ibkr(get_broker_adapter('ibkr'),args.account_id)
        source_sha=hashlib.sha256(dumps(events).encode()).hexdigest();source_name='ibkr_recent'
    if args.historical_fx:
        from app.fx_service import fx_service
        events=historical_fx(events,fx_service)
    from backend.services.portfolio_history.imports import validate_events
    events=validate_events(events,coverage)
    result=dict(status='validated_only',events=len(events),source=source_name,
        no_order_or_position_mutation=True,source_issues=metadata.get('issues',[]))
    if args.apply:
        engine=create_engine('sqlite://',creator=lambda:writer_connection(args.database,args.contract))
        try:
            from app.b2_schema import verify as verify_history_schema
            with engine.connect() as connection:
                verify_history_schema(connection.connection.driver_connection)
            with Session(engine) as db:
                result.update(import_batch(db,args.portfolio_id,'ibkr',args.account_id,source_name,source_sha,
                    events,coverage.model_dump(),metadata))
        finally:engine.dispose()
    print(json.dumps(result))


if __name__=='__main__':
    logging.getLogger().handlers=[logging.NullHandler()]
    try:main()
    except Exception as exc:
        from backend.services.portfolio_history.sources import ERRORS
        code=str(exc) if str(exc) in ERRORS else 'HISTORY_IMPORT_REFUSED'
        print(json.dumps(dict(status='refused',reason_code=code)));sys.exit(1)
