"""4.3 explicit research preview/confirm bridge; no provider or business DB access."""
from copy import deepcopy
from datetime import timedelta
import secrets
import threading
import time
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .contracts import Record, ThesisDraft, ReviewDraft, canonical, now, evidence_qualification
from .service import review_outcome
from .store import digest, version_diff


class ReviewPreviewDraft(ReviewDraft):
    thesis_revision: int | None = Field(default=None, gt=0, strict=True)
    review_as_of: AwareDatetime | None = None


class PreviewRequest(Record):
    operation: Literal['THESIS', 'REVIEW']
    record: ThesisDraft | ReviewPreviewDraft
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    analysis: dict | None = None
    analysis_source: Literal['NOT_RUN', 'USER_SUPPLIED_ANALYSIS_COPY'] = 'NOT_RUN'
    asset_view: dict | None = None
    scenario: dict | None = None

    @model_validator(mode='before')
    @classmethod
    def generated_thesis_id(cls, value):
        if isinstance(value, dict) and value.get('operation') == 'THESIS' and isinstance(value.get('record'), dict):
            value = deepcopy(value)
            if not value['record'].get('thesis_id'):
                value['record']['thesis_id'] = 'e1-' + digest(value['record'])[:24]
        return value

    @model_validator(mode='after')
    def sources(self):
        if (self.analysis is None) != (self.analysis_source == 'NOT_RUN'):
            raise ValueError('E1_ANALYSIS_SOURCE_MISMATCH')
        if self.operation == 'THESIS' and any(x is not None for x in (self.analysis, self.asset_view, self.scenario)):
            raise ValueError('E1_THESIS_ANALYSIS_NOT_APPLICABLE')
        if len(canonical(self.model_dump(mode='json')).encode()) > 2 * 1024 * 1024:
            raise ValueError('E1_PREVIEW_TOO_LARGE')
        return self


class ResearchBridge:
    """Bounded boot-local drafts. No disk writes until an explicitly confirmed commit.

    Tokens do not prove human intent: the trusted client must show the preview and
    await a fresh human confirmation. Restart/expiry invalidates unsaved previews.
    """
    def __init__(self, store, *, ttl_seconds=900, capacity=32, clock=time.monotonic):
        self.store = store
        self.ttl = ttl_seconds
        self.capacity = capacity
        self.clock = clock
        self.pending = {}
        self.lock = threading.Lock()

    def _head(self, db, thesis_id):
        row = db.execute('SELECT head_revision FROM theses WHERE portfolio_id=? AND thesis_id=?',
                         (self.store.portfolio_id, thesis_id)).fetchone()
        return row[0] if row else 0

    def preview(self, raw):
        request = PreviewRequest.model_validate(raw)
        record = request.record.model_dump(mode='json')
        if request.operation == 'THESIS' and not record.get('thesis_id'):
            record['thesis_id'] = 'e1-' + digest(record)[:24]
        thesis_id = record.get('thesis_id')
        if not isinstance(thesis_id, str):
            raise ValueError('E1_THESIS_ID_REQUIRED')
        with self.store.connection() as db:
            head = self._head(db, thesis_id)
            expected = head if request.expected_revision is None else request.expected_revision
            previous = self.store._version(db, thesis_id, head) if head else None
            if request.operation == 'THESIS':
                draft = ThesisDraft.model_validate(record)
                record = draft.model_dump(mode='json')
                if head:
                    first = self.store._version(db, thesis_id, 1)
                    if any(first['content'][k] != record[k] for k in ('user_original_judgment', 'original_judgment_kind')):
                        raise ValueError('E1_ORIGINAL_JUDGMENT_IMMUTABLE_USE_REFLECTION')
                evaluation = dict(evidence_qualification=[evidence_qualification(e, draft.decision_as_of) for e in draft.evidence]
                                  if draft.decision_as_of else [], current_qualification='NOT_RECHECKED',
                                  exposure={'status': 'CANNOT_QUANTIFY', 'confirmed_limit_status': 'UNKNOWN'})
                changes = version_diff(previous['content'] if previous else {}, record)
                storage_fingerprint = digest(dict(content=record, expected_revision=expected))
                kind = 'thesis_versions'
            else:
                if not previous:
                    raise ValueError('E1_RECORD_NOT_FOUND')
                if record.get('thesis_revision') is None:
                    record['thesis_revision'] = head
                if record.get('review_as_of') is None:
                    record['review_as_of'] = now().isoformat()
                draft = ReviewDraft.model_validate(record)
                if draft.thesis_revision != expected:
                    raise ValueError('E1_REVIEW_HEAD_REQUIRED')
                record = draft.model_dump(mode='json')
                thesis = self.store._version(db, thesis_id, draft.thesis_revision)
                if request.analysis is None and not draft.not_run_reason:
                    raise ValueError('E1_ANALYSIS_NOT_RUN_REASON_REQUIRED')
                evaluation = review_outcome(self.store.portfolio_id, thesis, draft, request.analysis,
                    request.asset_view, request.scenario,
                    dict(confirmed=[], issues=['E1_BRIDGE_NO_AUTHORIZED_TARGET_CONTEXT']), request.analysis_source)
                # Distinguish this review's edits from the thesis version's own history.
                row = db.execute('SELECT * FROM decision_reviews WHERE portfolio_id=? AND thesis_id=? '
                    'AND event_index=1 ORDER BY rowid DESC LIMIT 1', (self.store.portfolio_id, thesis_id)).fetchone()
                previous_review = self.store._read(row) if row else None
                old_review = previous_review['review'] if previous_review else {}
                changes = version_diff(old_review, record)
                storage_fingerprint = None
                kind = 'decision_reviews'
            payload = dict(operation=request.operation, portfolio_scope=self.store.portfolio_id,
                expected_revision=expected, record=record, analysis=request.analysis,
                analysis_source=request.analysis_source, asset_view=request.asset_view, scenario=request.scenario)
            base_fingerprint = digest(payload)
            payload['previous_review_sha256'] = (previous_review['sha256'] if previous_review else None) if request.operation == 'REVIEW' else None
            fingerprint = digest(payload)
            request_id = request.request_id or 'e1-save-' + base_fingerprint
            if storage_fingerprint is None:
                storage_fingerprint = base_fingerprint
            prior = self.store._request(db, request_id, storage_fingerprint, kind)
            if head != expected and prior is None:
                raise ValueError('E1_REVISION_CONFLICT_REPREVIEW')
        preview = dict(schema_version='wealthpilot.4.3.e1.preview.v1', request_id=request_id,
            portfolio_scope=self.store.portfolio_id, thesis_id=thesis_id, current_revision=head,
            expected_revision=expected, operation=request.operation, payload=payload,
            preview_fingerprint=fingerprint, changes=changes, evaluation=evaluation,
            summary=self._summary(request.operation, record, evaluation, changes,
                                  record if request.operation == 'THESIS' else thesis['content']),
            current_thesis=previous,
            confirmation_required='保存这次判断', saved=False, execution_authorized=False,
            expires_at=(now()+timedelta(seconds=self.ttl)).isoformat(),
            limitation='Local/future authorized research save only; not online integration or trading authorization')
        with self.lock:
            self.pending = {k:v for k,v in self.pending.items() if v['deadline'] > self.clock()}
            if len(self.pending) >= self.capacity:
                raise ValueError('E1_PREVIEW_CAPACITY_REACHED')
            token = secrets.token_urlsafe(32)
            self.pending[token] = dict(deadline=self.clock()+self.ttl, preview=deepcopy(preview),
                                       storage_fingerprint=storage_fingerprint, kind=kind)
        return dict(preview, preview_token=token)

    @staticmethod
    def _summary(operation, record, evaluation, changes, thesis):
        is_review = operation == 'REVIEW'
        evidence = thesis.get('evidence', [])
        return dict(title='待永久保存的研究记录（尚未保存）',
            action=record.get('action_intent', 'THESIS_UPDATE'),
            judgment=evaluation.get('effective_verdict', record.get('status')),
            rationale=record.get('rationale', record.get('change_reason')),
            changed_fields=sorted(changes),
            evidence_by_relation={r:[e['evidence_id'] for e in evidence if e['relation']==r]
                                  for r in ('supports','opposes','contextual')},
            source_kinds=sorted({e['kind'] for e in evidence}),
            claims=thesis['claims'], evidence=evidence,
            known_at_qualification=evaluation.get('evidence_qualification', []),
            original_analysis_status=evaluation.get('original_analysis_status', 'NOT_ATTACHED'),
            analysis_provenance=evaluation.get('analysis_provenance', 'NOT_ATTACHED'),
            limitations=(evaluation.get('analysis') or {}).get('limitations', []),
            data_quality=(evaluation.get('analysis') or {}).get('data_quality', {}),
            restrictions=evaluation.get('verdict_restrictions', []),
            exposure=evaluation['exposure'],
            notice='不是买入许可；金额/股数/风险上限未知时不生成。' if is_review else '仅保存研究版本，不执行分析。')

    def commit(self, token, fingerprint, *, confirmed=False, confirmation=None):
        # Serializes same-token confirmations; SQLite serializes distinct bridge instances.
        with self.lock:
            item = self.pending.get(token)
            if item is None or item['deadline'] <= self.clock():
                self.pending.pop(token, None)
                raise ValueError('E1_PREVIEW_EXPIRED_OR_RESTARTED')
            if confirmation == '取消':
                self.pending.pop(token)
                return dict(saved=False, cancelled=True, execution_authorized=False)
            if confirmed is not True or confirmation != '保存这次判断':
                raise ValueError('E1_EXPLICIT_CONFIRMATION_REQUIRED')
            preview = item['preview']
            if not isinstance(fingerprint, str) or not secrets.compare_digest(fingerprint, preview['preview_fingerprint']):
                raise ValueError('E1_PREVIEW_CHANGED_REPREVIEW')
            p = preview['payload']
            with self.store.connection(write=True) as db:
                prior = self.store._request(db, preview['request_id'], item['storage_fingerprint'], item['kind'])
                if prior is None:
                    if self._head(db, preview['thesis_id']) != p['expected_revision']:
                        raise ValueError('E1_REVISION_CONFLICT_REPREVIEW')
                    if p['operation'] == 'THESIS':
                        result = self.store.save(p['record'], p['expected_revision'], preview['request_id'], _db=db)
                    else:
                        latest = db.execute('SELECT sha256 FROM decision_reviews WHERE portfolio_id=? AND thesis_id=? '
                            'AND event_index=1 ORDER BY rowid DESC LIMIT 1',
                            (self.store.portfolio_id, preview['thesis_id'])).fetchone()
                        if (latest[0] if latest else None) != p['previous_review_sha256']:
                            raise ValueError('E1_REVIEW_CONTEXT_CHANGED_REPREVIEW')
                        draft = ReviewDraft.model_validate(p['record'])
                        started, _ = self.store.begin_review(draft, item['storage_fingerprint'], preview['request_id'], _db=db)
                        result = self.store.finish_review(started, preview['evaluation'], preview['request_id'],
                                                          item['storage_fingerprint'], _db=db)
                else:
                    if prior.get('state', 'COMPLETED') != 'COMPLETED':
                        raise ValueError('E1_PRIOR_SAVE_INCOMPLETE_NO_REPLAY')
                    result = prior
            # Fresh read-only connection after COMMIT; never claim saved on readback failure.
            if p['operation'] == 'THESIS':
                readback = self.store.get(result['thesis_id'], result['revision'])
            else:
                readback = self.store.reviews(result['thesis_id'], result['review_id'])[0]
            if readback != result:
                raise ValueError('E1_SAVE_READBACK_MISMATCH')
            return dict(saved=True, state='SAVED_AND_READ_BACK', request_id=preview['request_id'],
                thesis_id=result['thesis_id'], revision=result.get('revision', result.get('thesis_revision')),
                review_id=result.get('review_id'), portfolio_scope=self.store.portfolio_id,
                preview_fingerprint=fingerprint, record_sha256=result['sha256'],
                analysis_provenance=result.get('analysis_provenance', 'NOT_ATTACHED'),
                summary=preview['summary'], execution_authorized=False,
                current_qualification=result['current_qualification'])
