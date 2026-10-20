"""Atomic import of broker facts. No order/Position mutations or broker I/O."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text, inspect
from .models import HistoryImport, HistoryEvent
from .sources import dumps, timestamp, number, MAX_EVENTS, fail


class Coverage(BaseModel):
    """Private, offline operator-reviewed report evidence, not an MCP override.

    Completeness is scoped to interval/instruments and never inferred from rows
    being absent. A zero opening must be independently evidenced in statements.
    """
    model_config = ConfigDict(extra='forbid')
    coverage_start: str
    coverage_end: str  # exclusive
    source_timezone: str
    scope: Literal['account','instruments'] = 'instruments'
    instrument_keys: list[str] = Field(default_factory=list,max_length=200)
    complete_executions: bool = False
    complete_cashflows: bool = False
    corporate_actions_reviewed: bool = False
    opening_zero_keys: list[str] = Field(default_factory=list,max_length=200)
    evidence_ref: str = Field(min_length=1,max_length=500)

    @model_validator(mode='after')
    def check(self):
        self.coverage_start=timestamp(self.coverage_start)
        self.coverage_end=timestamp(self.coverage_end)
        if not self.coverage_start<self.coverage_end:fail('HISTORY_COVERAGE_INVALID')
        if (datetime.fromisoformat(self.coverage_end)-datetime.fromisoformat(self.coverage_start)).days>3660:
            fail('HISTORY_COVERAGE_INVALID')
        if not self.evidence_ref.strip() or (self.scope=='instruments' and not self.instrument_keys):
            fail('HISTORY_COVERAGE_INVALID')
        if self.scope=='instruments' and not set(self.opening_zero_keys)<=set(self.instrument_keys):
            fail('HISTORY_COVERAGE_INVALID')
        return self


def owner_scope(db, portfolio_id, broker, account):
    if broker not in {'ibkr','tiger','guojin'} or not account or account.lower() in {'unknown','pending','__unconfigured__'}:
        fail('HISTORY_OWNER_UNPROVEN')
    aliases = ('ibkr','snowball') if broker=='ibkr' else (broker,broker)
    params=dict(a=account,b=aliases[0],c=aliases[1])
    owners=set()
    for table,broker_col,account_col in [('positions','broker','broker_account_id'),
            ('position_snapshot_runs','broker','account_id'),('order_records','broker_name','broker_account_id')]:
        rows=db.execute(text(f'SELECT DISTINCT portfolio_id FROM {table} WHERE {broker_col} IN (:b,:c) '
            f'AND {account_col}=:a AND portfolio_id IS NOT NULL'),params)
        owners.update(r[0] for r in rows)
    for table in ('portfolio_history_imports','portfolio_history_events'):
        if table in inspect(db.connection()).get_table_names():
            owners.update(r[0] for r in db.execute(text(f'SELECT DISTINCT portfolio_id FROM {table} '
                'WHERE broker=:b AND account_id=:a'),dict(b=broker,a=account)))
    if not owners:fail('HISTORY_OWNER_UNPROVEN')
    if owners!={portfolio_id}:fail('HISTORY_ACCOUNT_AMBIGUOUS')


def validate_events(events, coverage):
    if len(events)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
    seen={}
    for original in events:
        e=dict(original)
        if e.get('kind') not in {'EXECUTION','DIVIDEND','INCOME_TAX','INTEREST','FEE'}:fail()
        if e.get('kind')=='EXECUTION':
            if e.get('side') not in {'BUY','SELL'}:fail()
            for field in ('quantity','price','multiplier'):e[field]=number(e.get(field),positive=True)
            if Decimal(e['quantity'])*Decimal(e['price'])*Decimal(e['multiplier'])>Decimal('1e20'):fail()
            for field in ('commission','taxes','broker_realized_pnl','broker_cost_basis'):
                if e.get(field) is not None:e[field]=number(e[field])
            if e.get('commission_currency') and not re.fullmatch(r'[A-Z]{3}',e['commission_currency']):fail()
        else:e['amount']=number(e.get('amount'))
        if not re.fullmatch(r'[A-Z]{3}',e.get('currency','')):fail()
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',e.get('external_id','')):fail()
        if not e.get('instrument_key') or not e.get('symbol'):fail()
        if not (coverage.coverage_start[:10] <= e['executed_at'][:10] <= coverage.coverage_end[:10]):
            fail('HISTORY_COVERAGE_INVALID')
        instant=e['executed_at'] if len(e['executed_at'])>10 else e['executed_at']+'T00:00:00+00:00'
        if not coverage.coverage_start<=instant<coverage.coverage_end:fail('HISTORY_COVERAGE_INVALID')
        if coverage.scope=='instruments' and e['instrument_key'] not in coverage.instrument_keys:fail('HISTORY_COVERAGE_INVALID')
        fx=e.get('fx')
        if fx and (fx.get('rate_date')!=e['executed_at'][:10] or not fx.get('source')
                or fx.get('pair')!=e['currency']+'/CNY' or Decimal(number(fx.get('rate'),positive=True))<=0):fail()
        identity=(e['kind'],e['external_id'])
        if identity in seen and seen[identity]!=e:fail('HISTORY_EVENT_CONFLICT')
        seen[identity]=e
    return list(seen.values())


def import_batch(db, portfolio_id, broker, account, source, source_sha256, events, coverage, source_metadata=None):
    """Parse/FX I/O must be finished BEFORE calling; one short durable transaction.

    Existing known fields cannot be corrected silently. Missing actual fee/FX
    facts may be enriched by a subsequent report; both import evidences remain.
    """
    coverage=Coverage.model_validate(coverage)
    events=validate_events(events,coverage)
    if not re.fullmatch(r'[a-z0-9_]{1,50}',source) or not re.fullmatch(r'[0-9a-f]{64}',source_sha256):fail()
    metadata=dict(source_metadata or {})
    if metadata.get('issues'):
        coverage.complete_cashflows=False
        if 'CORPORATE_ACTIONS_UNSUPPORTED' in metadata['issues']:coverage.complete_executions=False
    # Native XML report period bounds must cover the attested interval.
    bounds=metadata.get('report_bounds_utc',[])
    if bounds and not any(a<=coverage.coverage_start and b>=coverage.coverage_end for a,b in bounds):
        fail('HISTORY_COVERAGE_INVALID')
    if source=='ibkr_recent':
        coverage.complete_executions=False;coverage.complete_cashflows=False;coverage.opening_zero_keys=[]
    evidence=dict(coverage=coverage.model_dump(),source_sha256=source_sha256,source_metadata=metadata,
        event_identities=sorted([[e['kind'],e['external_id']] for e in events]),
        completeness_authority='operator_reviewed_statement_not_inferred_from_absence')
    digest=hashlib.sha256(dumps(dict(evidence=evidence,events=events)).encode()).hexdigest()
    if db.in_transaction():raise ValueError('HISTORY_IMPORT_REQUIRES_NEW_TRANSACTION')
    db.execute(text('BEGIN IMMEDIATE'))
    try:
        owner_scope(db,portfolio_id,broker,account)
        existing=db.query(HistoryImport).filter_by(broker=broker,account_id=account,source=source,digest=digest).one_or_none()
        if existing:
            if existing.portfolio_id!=portfolio_id:fail('HISTORY_OWNER_UNPROVEN')
            db.rollback();return dict(status='unchanged',import_id=existing.id,events=0)
        record=HistoryImport(id=uuid.uuid4().hex,portfolio_id=portfolio_id,broker=broker,account_id=account,
            source=source,digest=digest,imported_at=datetime.now(timezone.utc).isoformat(),
            coverage_start=coverage.coverage_start,coverage_end=coverage.coverage_end,evidence_json=dumps(evidence))
        db.add(record);db.flush()
        stored=db.query(HistoryEvent).filter_by(broker=broker,account_id=account).limit(MAX_EVENTS+1).all()
        if len(stored)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
        by_id={(r.kind,r.external_id):r for r in stored}
        families={r.external_id.rsplit('.',1)[0] for r in stored if r.kind=='EXECUTION' and '.' in r.external_id}
        for e in events:
            old=by_id.get((e['kind'],e['external_id']))
            if old:
                if old.portfolio_id!=portfolio_id:fail('HISTORY_OWNER_UNPROVEN')
                saved=json.loads(old.payload_json);new={**saved}
                enrich={'commission','commission_currency','taxes','broker_realized_pnl','broker_cost_basis','fx','perm_id','broker_order_id','order_ref'}
                for key,value in e.items():
                    if saved.get(key)==value or value is None:continue
                    if key not in enrich or saved.get(key) is not None:fail('HISTORY_EVENT_CONFLICT')
                    new[key]=value
                    new.setdefault('field_evidence',{})[key]=record.id
                if new!=saved:
                    new['evidence_import_ids']=sorted(set(saved.get('evidence_import_ids',[])+[record.id]))
                    old.payload_json=dumps(new)
                continue
            # IBKR correction suffixes are not a second economically distinct fill.
            if broker=='ibkr' and e['kind']=='EXECUTION' and '.' in e['external_id']:
                family=e['external_id'].rsplit('.',1)[0]
                if family in families:fail('HISTORY_CORRECTION_REQUIRES_REVIEW')
                families.add(family)
            payload={**e,'evidence_import_ids':[record.id]}
            row=HistoryEvent(portfolio_id=portfolio_id,broker=broker,account_id=account,kind=e['kind'],
                external_id=e['external_id'],instrument_key=e['instrument_key'],symbol=e['symbol'],
                currency=e['currency'],executed_at=e['executed_at'],import_id=record.id,payload_json=dumps(payload))
            db.add(row);by_id[(e['kind'],e['external_id'])]=row
            if len(by_id)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
        db.commit()
        return dict(status='imported',import_id=record.id,events=len(events))
    except Exception:
        db.rollback();raise
