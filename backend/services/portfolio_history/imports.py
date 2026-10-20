"""Atomic import of broker facts. No order/Position mutations or broker I/O."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import uuid
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text, inspect
from .models import HistoryImport, HistoryEvent
from .sources import dumps, timestamp, number, MAX_EVENTS, fail, cashflow_fx_date, window_membership


class ReportEvidence(BaseModel):
    """Operator-reviewed export definition, bound to actual report bytes and scope."""
    model_config = ConfigDict(extra='forbid')
    broker: Literal['ibkr','tiger','guojin']
    account_id: str = Field(min_length=1)
    document_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    definition_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    locator: str = Field(min_length=1, max_length=500)
    coverage_start: str
    coverage_end: str
    source_timezone: str
    filter_scope: Literal['account','instruments']
    instrument_keys: list[str] = Field(default_factory=list, max_length=200)
    sections: list[str] = Field(max_length=30)


class OpeningEvidence(BaseModel):
    """An actual opening quantity, with an independently attached source document."""
    model_config = ConfigDict(extra='forbid')
    broker: Literal['ibkr','tiger','guojin']
    account_id: str = Field(min_length=1)
    instrument_key: str
    as_of: str
    quantity: str
    document_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    locator: str = Field(min_length=1, max_length=500)


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
    report_evidence: ReportEvidence | None = None
    opening_evidence: list[OpeningEvidence] = Field(default_factory=list,max_length=200)

    @model_validator(mode='after')
    def check(self):
        ZoneInfo(self.source_timezone)
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


def qualified_coverage(proof):
    """Revalidate persisted evidence; pre-fix flags alone never confer qualification."""
    c = Coverage.model_validate(proof['coverage']).model_dump()
    requested = dict(c)
    c.update(complete_executions=False, complete_cashflows=False,
             corporate_actions_reviewed=False, opening_zero_keys=[])
    issues = []
    r = requested.get('report_evidence')
    meta = proof.get('source_metadata', {})
    attached = set(proof.get('verified_document_sha256', []))
    valid = bool(proof.get('evidence_version') == 2 and r and
        r['broker'] == proof.get('broker') and r['account_id'] == proof.get('account_id') == meta.get('account_id') and
        r['document_sha256'] == proof.get('source_sha256') == meta.get('source_sha256') and
        r['definition_sha256'] in attached and r['definition_sha256'] != r['document_sha256'] and
        r['locator'].strip() and r['source_timezone'] == c['source_timezone'] == meta.get('source_timezone') and
        timestamp(r['coverage_start']) == c['coverage_start'] and timestamp(r['coverage_end']) == c['coverage_end'] and
        r['filter_scope'] == c['scope'] and set(r['instrument_keys']) == set(c['instrument_keys']) and
        meta.get('format') == 'xml' and any(a <= c['coverage_start'] and b >= c['coverage_end']
            for a,b in meta.get('report_bounds_utc', [])))
    if valid:
        present = set(meta.get('sections_present', [])) & set(r['sections'])
        c['corporate_actions_reviewed'] = requested['corporate_actions_reviewed'] and 'CorporateActions' in present
        c['complete_executions'] = requested['complete_executions'] and 'Trades' in present and c['corporate_actions_reviewed']
        c['complete_cashflows'] = requested['complete_cashflows'] and 'CashTransactions' in present and not meta.get('issues')
        for opening in requested['opening_evidence']:
            if (opening['broker'] == r['broker'] and opening['account_id'] == r['account_id'] and
                opening['document_sha256'] in attached and opening['document_sha256'] != r['document_sha256'] and
                opening['locator'].strip() and timestamp(opening['as_of']) == c['coverage_start'] and
                Decimal(number(opening['quantity'])) == 0 and opening['instrument_key'] in requested['opening_zero_keys'] and
                (c['scope'] == 'account' or opening['instrument_key'] in c['instrument_keys'])):
                c['opening_zero_keys'].append(opening['instrument_key'])
    else:
        issues.append('REPORT_SCOPE_EVIDENCE_UNPROVEN')
    if requested['opening_zero_keys'] and not c['opening_zero_keys']:
        issues.append('INDEPENDENT_OPENING_EVIDENCE_UNPROVEN')
    if not c['complete_cashflows']:
        issues.append('CASHFLOW_SECTION_OR_SCOPE_UNPROVEN')
    if 'CORPORATE_ACTIONS_UNSUPPORTED' in meta.get('issues', []):
        c['complete_executions'] = False
    return c, issues


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
        if window_membership(e,coverage.coverage_start,coverage.coverage_end)=='outside':fail('HISTORY_COVERAGE_INVALID')
        if coverage.scope=='instruments' and e['instrument_key'] not in coverage.instrument_keys:fail('HISTORY_COVERAGE_INVALID')
        fx=e.get('fx')
        if fx and (fx.get('rate_date')!=cashflow_fx_date(e) or not fx.get('source')
                or fx.get('pair')!=e['currency']+'/CNY' or Decimal(number(fx.get('rate'),positive=True))<=0):fail()
        identity=(e['kind'],e['external_id'])
        if identity in seen and seen[identity]!=e:fail('HISTORY_EVENT_CONFLICT')
        seen[identity]=e
    return list(seen.values())


def build_evidence(broker, account, source, source_sha256, events, coverage, source_metadata=None, *, evidence_documents=()):
    """Validate all byte-bound provenance before taking a SQLite write reservation."""
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
    documents = {hashlib.sha256(raw).hexdigest() for raw in evidence_documents}
    evidence=dict(evidence_version=2,broker=broker,account_id=account,verified_document_sha256=sorted(documents),
        coverage=coverage.model_dump(),source_sha256=source_sha256,source_metadata=metadata,
        event_identities=sorted([[e['kind'],e['external_id']] for e in events]),
        completeness_authority='bound_report_and_independent_opening_v2')
    qualified_coverage(evidence)
    return coverage, events, evidence


def import_batch(db, portfolio_id, broker, account, source, source_sha256, events, coverage, source_metadata=None, *, evidence_documents=()):
    """Parse/FX I/O must be finished BEFORE calling; one short durable transaction.

    Existing known fields cannot be corrected silently. Missing actual fee/FX
    facts may be enriched by a subsequent report; both import evidences remain.
    """
    coverage,events,evidence=build_evidence(broker,account,source,source_sha256,events,coverage,source_metadata,
        evidence_documents=evidence_documents)
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
                saved=json.loads(old.payload_json);new=deepcopy(saved)
                enrich={'commission','commission_currency','taxes','broker_realized_pnl','broker_cost_basis','fx','perm_id','broker_order_id','order_ref','source_time_raw','source_timezone','source_local_date',
                    'source_date_semantics','fx_date','fx_date_basis'}
                provenance={'source_time_raw','source_timezone','source_local_date','source_date_semantics','fx_date_basis'}
                for key,value in e.items():
                    if key in provenance and saved.get(key) is not None:continue
                    if saved.get(key)==value or value is None:continue
                    if key not in enrich or saved.get(key) is not None:fail('HISTORY_EVENT_CONFLICT')
                    new[key]=value
                    new.setdefault('field_evidence',{})[key]=record.id
                new.setdefault('source_time_evidence',{})[record.id]={k:e.get(k) for k in provenance | {'fx_date'}}
                if new!=saved:
                    new['evidence_import_ids']=sorted(set(saved.get('evidence_import_ids',[])+[record.id]))
                    old.payload_json=dumps(new)
                continue
            # IBKR correction suffixes are not a second economically distinct fill.
            if broker=='ibkr' and e['kind']=='EXECUTION' and '.' in e['external_id']:
                family=e['external_id'].rsplit('.',1)[0]
                if family in families:fail('HISTORY_CORRECTION_REQUIRES_REVIEW')
                families.add(family)
            payload={**e,'evidence_import_ids':[record.id], 'source_time_evidence':{record.id:{
                k:e.get(k) for k in ('source_time_raw','source_timezone','source_local_date','source_date_semantics','fx_date','fx_date_basis')}}}
            row=HistoryEvent(portfolio_id=portfolio_id,broker=broker,account_id=account,kind=e['kind'],
                external_id=e['external_id'],instrument_key=e['instrument_key'],symbol=e['symbol'],
                currency=e['currency'],executed_at=e['executed_at'],import_id=record.id,payload_json=dumps(payload))
            db.add(row);by_id[(e['kind'],e['external_id'])]=row
            if len(by_id)>MAX_EVENTS:fail('HISTORY_QUERY_LIMIT')
        db.commit()
        return dict(status='imported',import_id=record.id,events=len(events))
    except Exception:
        db.rollback();raise
