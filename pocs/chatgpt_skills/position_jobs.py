"""Original single-position task Owner with private durable receipts and artifacts.

No provider or database writes. Query/recovery never executes analysis.
"""
import hashlib
import math
import json
import os
import re
import secrets
import threading
import time
from typing import Literal
from copy import deepcopy
from pathlib import Path
from pydantic import BaseModel, ConfigDict, ValidationError
from app.llm_observation import run_id
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult
from pocs.chatgpt_skills.position_store import PositionStore, StoreError, encode


class AnalysisReport(AnalysisResult):
    """Versioned delivery projection; complete v1 remains in the private artifact."""
    schema_version: Literal['wealthpilot.analysis-report.v1'] = 'wealthpilot.analysis-report.v1'


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['wealthpilot.position-artifact.v1'] = 'wealthpilot.position-artifact.v1'
    artifact_id: str
    sha256: str
    bytes: int
    result_schema: Literal['wealthpilot.analysis.v1'] = 'wealthpilot.analysis.v1'
    access: Literal['authorized_private_file_only'] = 'authorized_private_file_only'


class PositionAnalysisJob(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['wealthpilot.position-analysis-job.v2'] = 'wealthpilot.position-analysis-job.v2'
    job_id: str | None = None
    analysis_run_id: str | None = None
    request_scope: str
    state: Literal['running','completed','unavailable']
    business_status: Literal['ok','partial','unavailable','UNKNOWN'] = 'UNKNOWN'
    issue: str | None = None
    poll_after_seconds: int | None = None
    delivery: Literal['report'] = 'report'
    artifact: ArtifactReference | None = None
    result: AnalysisReport | None = None


def _reader_audit(final, position, financial, citations):
    """Whitelist trace metadata; the raw evidence and paths stay in the private artifact."""
    fields = ('source', 'published_at', 'known_at', 'data_as_of', 'fetched_at', 'period',
              'fiscal_period_start', 'fiscal_period_end', 'currency', 'historical_only')
    refs = set(citations.values())
    sources = {}
    groups = [financial.get('records')] + [position.get(key) for key in (
        'records', 'verified_field_records', 'historical_field_records', 'quality_field_records',
        'verified_period_records')]
    groups.extend(claim['paragraph'].get('sources') for claim in final['claims'])
    for rows in groups:
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            ref = row.get('evidence_ref') or row.get('record_id')
            if isinstance(ref, str) and ref in refs:
                metadata = sources.setdefault(ref, {})
                metadata.update({key: row[key] for key in fields if row.get(key) is not None})
                links = row.get('publication_links')
                if isinstance(links, list) and links:
                    keys = ('record_id', 'document_type', 'fiscal_period_start', 'fiscal_period_end',
                            'published_at', 'fetched_at', 'known_at')
                    metadata['publication_links'] = [
                        {key: link[key] for key in keys if link.get(key) is not None}
                        for link in links if isinstance(link, dict)
                    ]
    cells = {ref: {k: deepcopy(cell[k]) for k in ('name', 'method', 'assumption_ref', 'decision_at',
                'listed_currency', 'listed_unit', 'required_return', 'parameters', 'result',
                'label', 'assumption_origin', 'proposal_origin', 'owner_cash_parameters',
                'entry_price_sensitivity') if k in cell}
             for ref, cell in final['scenario_cells'].items()}
    return {'citation_key': citations, 'sources': sources, 'scenario_cells': cells,
            **{k: deepcopy(final.get(k)) for k in ('coverage', 'generation_diagnostic',
                                                 'diagnostic', 'scenario_decision_review', 'valuation_status',
                                                 'research_as_of', 'holding_context', 'valuation_source_status')
               if k in final}}



def report_projection(full, *, view="reader"):
    """Keep all user prose/explanations/valuation; archive bulk machine Evidence."""
    value = full.model_dump(mode='json')
    original = value['evidence']
    incomplete = []
    def mapping(value):
        if value is None:return {}
        if isinstance(value,dict):return value
        incomplete.append(True)
        return {}
    def records(value):
        if value is None:return []
        if not isinstance(value,list):
            incomplete.append(True)
            return []
        if any(not isinstance(row,dict) for row in value):incomplete.append(True)
        return [row for row in value if isinstance(row,dict)]
    position = mapping(original.get('position_evidence'))
    financial = mapping(position.get('financial_research'))
    value['evidence'] = dict(delivery='summary_complete_evidence_in_private_artifact',
        financial_research={k:deepcopy(v) for k,v in financial.items() if k != 'records'},
        financial_metrics=[{**{k:deepcopy(v) for k,v in row.items() if k != 'details'},
            'details':{k:deepcopy(v) for k,v in mapping(row.get('details')).items()
                       if k in {'formula','comparison_period','limitations','method'}}}
                           for row in records(financial.get('records'))],
        technical_levels=deepcopy(position.get('technical_levels')),
        source_index=[{k:deepcopy(v) for k,v in row.items() if k in
            {'kind','symbol','source','source_url','record_id','published_at','data_as_of','fetched_at',
             'scope','period','quality','issues','is_seed','is_mock','is_fallback'}}
            for row in records(position.get('records'))],
        position_context={k:deepcopy(position.get(k)) for k in
            ('symbol','as_of','issues','conflicts','source_conflicts','confidence_ceiling','infoNeeded','research_intent')},
        conditional_valuation=deepcopy(position.get('conditional_valuation')),
        qualification=deepcopy(position.get('qualification')),
        policy_reference=deepcopy(original.get('effective_rules_provenance')))
    # Entire professional explanations and their qualified sources stay intact.
    # Policy authority and full trace remain in the immutable v1 artifact.
    analysis = value.get('professional_result',{}).get('analysis_result')
    if isinstance(analysis,dict):
        analysis.pop('policy_context',None)
        if 'l1_research_v2' in original:
            value['evidence']['conditional_valuation'] = deepcopy(analysis.get('conditionalValuation'))
            final = original['l1_research_v2']
            if (full.scenario == 'analyze_position' and full.status in {'ok', 'partial'}
                    and full.professional_analysis_complete and analysis.get('mode') != 'facts_only'
                    and analysis.get('professional_explanation')):
                from backend.services.market_data.l1_reader import reader_projection
                reading = reader_projection(final, position.get("symbol"), policy_bound=bool(analysis.get("policy_reference")))
                if reading:
                    if view == 'reader':
                        value['conclusion'] = reading[0]
                    else:
                        value['evidence']['l1_reader_audit'] = _reader_audit(final, position, financial, reading[1])
    if incomplete:
        value['limitations'].append('REPORT_EVIDENCE_SUMMARY_PARTIAL: unsupported machine evidence shapes remain in the complete private artifact.')
    value['schema_version'] = 'wealthpilot.analysis-report.v1'
    return AnalysisReport.model_validate(value)


def _reject_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('NONFINITE_RESULT')
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite(item)


class PositionJobs:
    def __init__(self, execute, slot, *, storage_root=None, quota_bytes=None,
                 max_receipts=64, max_results=8, retention_seconds=7200,
                 source_revision='UNKNOWN', event=None):
        self.scope = 'pa_' + secrets.token_hex(16)
        self.execute, self.slot = execute, slot
        self.root, self.quota = storage_root, quota_bytes
        self.max_receipts, self.max_results = max_receipts, max_results
        self.retention_seconds = retention_seconds
        self.source_revision = source_revision if source_revision!='UNKNOWN' else 'position_jobs:sha256:'+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.event = event
        if not math.isfinite(retention_seconds) or retention_seconds<=0:
            raise ValueError('RETENTION_MUST_BE_POSITIVE')
        self.lock = threading.Lock()

    def _store(self):
        return PositionStore(self.root,self.quota)

    def _emit(self, row, category, envelope=None):
        value=dict(schema_version='wealthpilot.position-storage-event.v1',
            analysis_run_id=row['analysis_run_id'], job_id=row['job_id'],
            storage_status=category, result_bytes=envelope['result_bytes'] if envelope else 'UNKNOWN',
            artifact_sha256=envelope['result_sha256'] if envelope else 'UNKNOWN',
            business_status=row.get('business_status','UNKNOWN'))
        if self.event:
            try:self.event(value)
            except Exception:pass  # diagnostics never replace a committed result

    def _response(self,row=None,issue=None,envelope=None, *, view="reader"):
        artifact=None;result=None
        if envelope:
            artifact=ArtifactReference(artifact_id=row['job_id'],sha256=envelope['result_sha256'],bytes=envelope['result_bytes'])
            if not issue:
                result=report_projection(AnalysisResult.model_validate(envelope['result']), view=view)
        return PositionAnalysisJob(request_scope=self.scope,job_id=row['job_id'] if row else None,
            analysis_run_id=row['analysis_run_id'] if row else None,state='unavailable' if issue else row['state'] if row else 'unavailable',
            business_status=row.get('business_status','UNKNOWN') if row else 'UNKNOWN',issue=issue or (row.get('issue') if row else None),
            poll_after_seconds=15 if row and row['state']=='running' and not issue else None,
            artifact=artifact,result=result)

    @staticmethod
    def _category(error):
        if isinstance(error,StoreError):return error.code
        if isinstance(error,ValidationError):return 'ANALYSIS_RESULT_INVALID'
        if isinstance(error,(ValueError,TypeError)):return 'RESULT_SERIALIZATION_FAILED'
        if isinstance(error,OSError):return 'RESULT_STORAGE_UNAVAILABLE'
        return 'ANALYSIS_JOB_FAILED'

    def _recover(self,store,data,row, *, view="reader"):
        envelope=store.load_result(row)
        if envelope:
            # Validation precedes recovery: an arbitrary JSON file is not success.
            full=AnalysisResult.model_validate(envelope['result'])
            if row['state'] != 'completed':
                row.update(state='completed',issue=None,finished=envelope['finished_at'],business_status=full.status,artifact_sha256=envelope['result_sha256'],artifact_bytes=envelope['result_bytes'])
                store.save_receipts(data)
                self._emit(row,'recovered',envelope)
            if time.time()-row['finished'] >= self.retention_seconds:
                self._emit(row,'RESULT_EXPIRED',envelope)
                return self._response(row,issue='RESULT_EXPIRED',envelope=envelope)
            return self._response(row,envelope=envelope,view=view)
        if row['state']=='completed':
            raise StoreError('RESULT_ARTIFACT_MISSING')
        if row['state']=='running':
            admission=store.admission()
            if admission is not None:
                os.close(admission)
                row.update(state='unavailable',issue='ANALYSIS_INTERRUPTED',finished=time.time())
                store.save_receipts(data)
                self._emit(row,'ANALYSIS_INTERRUPTED')
        return self._response(row)

    def start(self,request_id,request:AnalysisRequest):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}',request_id):
            return self._response(issue='REQUEST_ID_INVALID')
        fingerprint=hashlib.sha256(json.dumps(request.model_dump(mode='json'),sort_keys=True,
            ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        key=hashlib.sha256(request_id.encode()).hexdigest()
        store=None;admission=None;reserved=False
        try:
            store=self._store()
            with self.lock,store.locked():
                data=store.receipts()
                if key in data['requests']:
                    row=data['jobs'][data['requests'][key]]
                    if row['fingerprint']!=fingerprint:return self._response(issue='REQUEST_ID_CONFLICT')
                    return self._recover(store,data,row)
                if len(data['requests'])>=self.max_receipts:
                    return self._response(issue='JOB_CAPACITY_REACHED')
                admission=store.admission()
                if admission is None:return self._response(issue='ANALYSIS_BUSY')
                # Holding the only execution lease proves no previous running
                # receipt has a live owner. Tombstone it; never resume its work.
                for previous in data['jobs'].values():
                    if previous['state']=='running':
                        if store.load_result(previous):
                            self._recover(store,data,previous)
                        else:
                            previous.update(state='unavailable',issue='ANALYSIS_INTERRUPTED',finished=time.time())
                            store.save_receipts(data)
                            self._emit(previous,'ANALYSIS_INTERRUPTED')
                occupied=sum(r['state']=='completed' and time.time()-r['finished']<self.retention_seconds
                             for r in data['jobs'].values())
                if occupied>=self.max_results:return self._response(issue='JOB_CAPACITY_REACHED')
                if not self.slot.acquire(blocking=False):return self._response(issue='ANALYSIS_BUSY')
                reserved=True
                row=dict(job_id=secrets.token_urlsafe(32),analysis_run_id=run_id(),fingerprint=fingerprint,
                    state='running',issue=None,created=time.time(),finished=None,business_status='UNKNOWN',
                    source_revision=self.source_revision,fingerprint_version='analysis-request.v1',
                    owner_scope=self.scope,owner_pid=os.getpid(),request_key=key)
                data['jobs'][row['job_id']]=row;data['requests'][key]=row['job_id']
                store.save_receipts(data)  # durable claim BEFORE thread/Worker/model
                try:
                    thread=threading.Thread(target=self._run,args=(row,request.model_copy(deep=True),store,admission),
                        name='wp-position-analysis',daemon=True)
                    thread.start()
                except Exception:
                    row.update(state='unavailable',issue='JOB_START_FAILED',finished=time.time())
                    store.save_receipts(data)
                    return self._response(row)
                store=None;admission=None;reserved=False  # ownership passed to thread
                return self._response(row)
        except Exception as error:
            return self._response(issue=self._category(error))
        finally:
            if admission is not None:os.close(admission)
            if reserved:self.slot.release()
            if store is not None:store.close()

    def _run(self,row,request,store,admission):
        envelope=None;serialized=None
        try:
            try:
                returned=self.execute(request,row['analysis_run_id'])
            except Exception as error:
                # Transport codes are closed constants; never persist raw errors.
                from pocs.chatgpt_skills.diagnostics import safe_diagnostic
                detail=safe_diagnostic(getattr(error,'analysis_diagnostic',None),row['analysis_run_id'])
                categories={'ipc_timeout':'ANALYSIS_IPC_TIMEOUT','worker_exited':'ANALYSIS_WORKER_EXITED',
                    'worker_start_failed':'ANALYSIS_WORKER_START_FAILED','worker_start_refused':'ANALYSIS_WORKER_START_FAILED',
                    'invalid_json':'ANALYSIS_JSON_INVALID','validation_failed':'ANALYSIS_RESULT_INVALID'}
                if detail and detail['safe_error_category'] in categories:
                    raise StoreError(categories[detail['safe_error_category']]) from None
                code=str(error)
                if code=='ANALYSIS_RESULT_INVALID':raise StoreError(code) from None
                allowed={'BUSINESS_OPERATION_UNAVAILABLE','ANALYSIS_RESULT_INVALID','ANALYSIS_RUN_ID_MISMATCH'}
                raise StoreError('ANALYSIS_WORKER_FAILED' if code in allowed or isinstance(error,(TimeoutError,EOFError)) else 'ANALYSIS_JOB_FAILED') from None
            full=AnalysisResult.model_validate(returned)
            _reject_nonfinite(full.model_dump(mode="python"))
            # Exactly the old encoder contract, before any report projection.
            report_projection(full)  # Verify delivery before exposing a completed receipt.
            raw=encode(full.model_dump(mode='json'))
            serialized={'result_bytes':len(raw),'result_sha256':hashlib.sha256(raw).hexdigest()}
            row['business_status']=full.status
            self._emit(row,'serialization_completed',serialized)
            with store.locked():
                envelope=store.save_result(row,full,time.time())
                data=store.receipts();saved=data['jobs'][row['job_id']]
                saved.update(state='completed',issue=None,finished=envelope['finished_at'],business_status=full.status,artifact_sha256=envelope['result_sha256'],artifact_bytes=envelope['result_bytes'])
                store.save_receipts(data)
                row=saved
            self._emit(row,'committed',envelope)
        except Exception as error:
            issue=self._category(error)
            try:
                with store.locked():
                    data=store.receipts();saved=data['jobs'][row['job_id']]
                    saved.update(state='unavailable',issue=issue,finished=time.time(),business_status=row.get('business_status','UNKNOWN'))
                    store.save_receipts(data)
            except Exception:pass  # existing durable claim remains, never replay
            self._emit(row,issue,envelope or serialized)
        finally:
            os.close(admission);store.close();self.slot.release()

    def get(self,job_id, *, view="reader"):
        if not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,64}',job_id):
            return self._response(issue='JOB_UNKNOWN_OR_PROCESS_RESTARTED')
        store=None;row=None
        try:
            store=self._store()
            with self.lock,store.locked():
                data=store.receipts();row=data['jobs'].get(job_id)
                if row is None:return self._response(issue='JOB_UNKNOWN_OR_PROCESS_RESTARTED')
                return self._recover(store,data,row,view=view)
        except Exception as error:
            if row:self._emit(row,self._category(error))
            return self._response(row,issue=self._category(error))
        finally:
            if store is not None:store.close()


# Existing S3 company-pool process-local contract is intentionally unchanged.
# Durable L1 rollout does not migrate S3 jobs or alter its frozen archive.
from dataclasses import dataclass

class ProcessLocalPositionAnalysisJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wealthpilot.position-analysis-job.v1"] = "wealthpilot.position-analysis-job.v1"
    job_id: str | None = None
    analysis_run_id: str | None = None
    request_scope: str
    state: Literal["running", "completed", "unavailable"]
    business_status: Literal["ok", "partial", "unavailable", "UNKNOWN"] = "UNKNOWN"
    issue: Literal["REQUEST_SCOPE_UNAVAILABLE", "REQUEST_ID_INVALID", "REQUEST_ID_CONFLICT", "ANALYSIS_BUSY",
        "JOB_CAPACITY_REACHED", "JOB_UNKNOWN_OR_PROCESS_RESTARTED", "RESULT_EXPIRED",
        "JOB_START_FAILED", "ANALYSIS_JOB_FAILED", "RESULT_STORAGE_UNAVAILABLE"] | None = None
    poll_after_seconds: int | None = None
    result: AnalysisResult | None = None


@dataclass
class _Receipt:
    fingerprint: str
    job_id: str
    analysis_run_id: str
    state: str = "running"
    issue: str | None = None
    result_json: str | None = None
    finished: float | None = None


class ProcessLocalPositionJobs:
    """One paid analysis at a time, shared admission with the original sync reads.

    Receipts never evict within this process: capacity refusal is safer than replay.
    Full results expire after two hours; retrieval never extends their lifetime.
    """
    def __init__(self, execute, slot, *, max_receipts=64, max_results=8,
                 retention_seconds=7200, max_result_bytes=2 * 1024 * 1024):
        self.scope = "pa_" + secrets.token_hex(16)
        self.execute, self.slot = execute, slot
        self.max_receipts, self.max_results = max_receipts, max_results
        self.retention_seconds, self.max_result_bytes = retention_seconds, max_result_bytes
        self.lock = threading.Lock()
        self.requests, self.jobs = {}, {}

    def _expire(self):
        now = time.monotonic()
        for row in self.jobs.values():
            if row.result_json is not None and now - row.finished >= self.retention_seconds:
                row.result_json = None
                row.state, row.issue = "unavailable", "RESULT_EXPIRED"

    def _response(self, row=None, issue=None):
        result = AnalysisResult.model_validate_json(row.result_json) if row and row.result_json is not None else None
        return ProcessLocalPositionAnalysisJob(request_scope=self.scope, job_id=row.job_id if row else None,
            analysis_run_id=row.analysis_run_id if row else None,
            state=row.state if row else "unavailable", issue=issue or (row.issue if row else None),
            business_status=result.status if result else "UNKNOWN",
            poll_after_seconds=15 if row and row.state == "running" else None, result=result)

    def start(self, request_id: str, request: AnalysisRequest):
        # Match the public input contract, without interpreting legacy scope prefixes.
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", request_id):
            return self._response(issue="REQUEST_ID_INVALID")
        payload = request.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self.lock:
            self._expire()
            previous = self.requests.get(request_id)
            if previous:
                if previous.fingerprint != fingerprint:
                    return self._response(issue="REQUEST_ID_CONFLICT")
                return self._response(previous)
            occupied = sum(row.state == "running" or row.result_json is not None for row in self.jobs.values())
            if len(self.requests) >= self.max_receipts or occupied >= self.max_results:
                return self._response(issue="JOB_CAPACITY_REACHED")
            if not self.slot.acquire(blocking=False):
                return self._response(issue="ANALYSIS_BUSY")
            row = _Receipt(fingerprint, secrets.token_urlsafe(32), run_id())
            self.requests[request_id] = self.jobs[row.job_id] = row
            try:
                # A process-owned thread, never a task in the HTTP request's cancel scope.
                thread = threading.Thread(target=self._run, args=(row, request.model_copy(deep=True)),
                    name="wp-position-analysis", daemon=True)
                thread.start()
            except Exception:
                row.state, row.issue, row.finished = "unavailable", "JOB_START_FAILED", time.monotonic()
                self.slot.release()
            return self._response(row)

    def _run(self, row, request):
        result_json, issue = None, None
        try:
            result = AnalysisResult.model_validate(self.execute(request, row.analysis_run_id))
            result_json = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            if len(result_json.encode()) > self.max_result_bytes:
                result_json, issue = None, "RESULT_STORAGE_UNAVAILABLE"
        except Exception:
            # No raw exception, prompt, assets, or model content in job metadata/logs.
            issue = "ANALYSIS_JOB_FAILED"
        finally:
            with self.lock:
                row.result_json, row.finished = result_json, time.monotonic()
                row.state, row.issue = ("completed", None) if result_json is not None else ("unavailable", issue)
            self.slot.release()

    def get(self, job_id: str):
        with self.lock:
            self._expire()
            row = self.jobs.get(job_id)
            return self._response(row, None if row else "JOB_UNKNOWN_OR_PROCESS_RESTARTED")
