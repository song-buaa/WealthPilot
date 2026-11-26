"""Private L1 samples and append-only quality reviews. No research execution."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pocs.chatgpt_skills.position_store import PositionStore, StoreError, encode

RUBRIC = 'l1-research-evaluation.v1'
DIMENSIONS = ('financial_evidence', 'business_drivers', 'competition_counterevidence',
              'valuation_returns', 'decision_opportunity_cost')
KEY = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')]
HASH = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
TEXT = Annotated[str, Field(min_length=1, max_length=6000)]
SCORE = Annotated[Decimal, Field(ge=0, le=2, max_digits=3, decimal_places=2)]
TARGET = Literal['native', 'reader', 'client_draft', 'client_final_human_reported']


def code_revision(root=None):
    """Read installed identity or checkout Git metadata; never launch a process."""
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    try:
        manifest = root.parent / 'installed.json'
        if root.name == 'code' and manifest.is_file() and manifest.stat().st_size < 65536:
            data = json.loads(manifest.read_text())
            if not isinstance(data,dict) or not isinstance(data.get('revision'),str):
                return 'UNKNOWN'
            if data.get('package_verified') is True and re.fullmatch('[a-f0-9]{40}', data['revision']):
                return data['revision']
        git = root / '.git'
        if git.is_file():
            value = git.read_text().strip()
            if not value.startswith('gitdir: '):
                return 'UNKNOWN'
            git = (root / value[8:]).resolve()
        head = (git / 'HEAD').read_text().strip()
        if re.fullmatch('[a-f0-9]{40}',head):
            return head
        if not head.startswith('ref: '):
            return 'UNKNOWN'
        ref = head[5:]
        if not re.fullmatch(r'refs/[A-Za-z0-9_./-]+',ref) or '..' in Path(ref).parts:
            return 'UNKNOWN'
        common = (git / (git/'commondir').read_text().strip()).resolve() if (git/'commondir').is_file() else git
        if (common/ref).is_file():
            revision = (common/ref).read_text().strip()
            return revision if re.fullmatch('[a-f0-9]{40}',revision) else 'UNKNOWN'
        for line in (common/'packed-refs').read_text().splitlines():
            parts = line.split()
            if len(parts)==2 and parts[1]==ref and re.fullmatch('[a-f0-9]{40}',parts[0]):
                return parts[0]
    except (OSError, ValueError):
        pass
    return 'UNKNOWN'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return sha256(encode(value)).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Dimension(Contract):
    score: SCORE
    basis: TEXT
    deductions: TEXT
    report_references: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(min_length=1, max_length=20)


class Evaluation(Contract):
    request_id: KEY
    target: TARGET
    report_sha256: HASH | None = None
    rubric_version: Literal['l1-research-evaluation.v1'] = RUBRIC
    ratings: dict[str, Dimension] | None = None
    not_rated_reason: TEXT | None = None
    readability: Literal['PASS', 'PARTIAL', 'FAIL', 'N/R']
    readability_basis: TEXT
    fact_checks: TEXT
    valuation_checks: TEXT
    defect_tags: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=30)
    suggestions: TEXT
    scorer_type: Literal['ai', 'human']
    scorer_model: Annotated[str, Field(min_length=1, max_length=120)]
    scored_at: Annotated[str, Field(min_length=1, max_length=80)]
    revision_of: KEY | None = None
    revision_reason: TEXT | None = None

    @model_validator(mode='after')
    def valid_review(self):
        if self.ratings is not None and set(self.ratings) != set(DIMENSIONS):
            raise ValueError('ALL_FIVE_DIMENSIONS_REQUIRED')
        if self.ratings is None and not self.not_rated_reason:
            raise ValueError('NOT_RATED_REASON_REQUIRED')
        if self.revision_of and not self.revision_reason:
            raise ValueError('REVISION_REASON_REQUIRED')
        if datetime.fromisoformat(self.scored_at.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('SCORE_TIMEZONE_REQUIRED')
        return self


class Feedback(Contract):
    request_id: KEY
    comment: TEXT
    report_text: Annotated[str, Field(min_length=1, max_length=200000)] | None = None
    report_kind: Literal['client_draft', 'client_final_human_reported'] = 'client_draft'
    author_type: Literal['ai', 'human']
    evaluation_id: KEY | None = None

    @model_validator(mode='after')
    def human_report(self):
        if self.report_text is not None and not self.report_text.strip():
            raise ValueError('REPORT_TEXT_MUST_HAVE_CONTENT')
        if self.report_kind == 'client_final_human_reported' and self.author_type != 'human':
            raise ValueError('FINAL_REPORT_REQUIRES_HUMAN_ATTESTATION')
        return self


class Files(PositionStore):
    NAME_PATTERN = r'(?:receipts\.lock|(?:sample|state|evaluation|feedback)-[a-f0-9]{64}\.json|\.pending-[a-f0-9]{32})'


class SampleStore:
    """Reuse PositionStore permission checks, flock, atomic/fsync writes and quota."""
    def __init__(self, artifact_root, *, quota_bytes=64*1024*1024):
        self.artifact_root = Path(artifact_root)
        self.root = self.artifact_root.with_name(self.artifact_root.name + '.evaluation')
        # Verify original operator-owned boundary before creating its sibling.
        source = PositionStore(self.artifact_root, quota_bytes)
        source.close()
        try:
            self.root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        self.files = Files(self.root, quota_bytes)

    def close(self):
        self.files.close()

    @staticmethod
    def sample_id(job_id):
        return digest(['l1-sample.v1', job_id])

    def capture(self, row, request=None, request_id=None, envelope=None):
        sid = self.sample_id(row['job_id'])
        with self.files.locked():
            name = f'sample-{sid}.json'
            existing = self.files.read(name)
            if existing is None:
                body = request.model_dump(mode='json') if request is not None else None
                # Full unrelated history is deliberately excluded. Its count and
                # the original request fingerprint preserve the capture boundary.
                history_count = len(body.pop('history', [])) if body else None
                sample = dict(schema_version='wealthpilot.research-sample.v1', sample_id=sid,
                    source='automatic_l1', created_at=now(), symbol=body.get('symbol') if body else None,
                    purpose=((body.get('research_context') or {}).get('objective') or 'UNKNOWN') if body else 'UNKNOWN',
                    research_type='L1_SINGLE_SYMBOL', question=body.get('question') if body else None,
                    request=body, request_id=request_id, excluded_history_count=history_count,
                    request_fingerprint=row['fingerprint'], analysis_run_id=row['analysis_run_id'],
                    job_id=row['job_id'], code_revision=row.get('source_revision', 'UNKNOWN') if re.fullmatch('[a-f0-9]{40}',row.get('source_revision','')) else 'UNKNOWN',
                    source_code_identity=row.get('source_revision', 'UNKNOWN'),
                    request_capture='CAPTURED_WITHOUT_HISTORY' if body else 'UNKNOWN')
                self.files.write(name, sample, immutable=True)
            elif existing['request_fingerprint'] != row['fingerprint']:
                raise StoreError('SAMPLE_IDENTITY_CONFLICT')
            state = dict(job_state=row['state'], business_status=row.get('business_status','UNKNOWN'),
                         issue=row.get('issue'), observed_at=now())
            if envelope:
                from pocs.chatgpt_skills.analysis import AnalysisResult
                from pocs.chatgpt_skills.position_jobs import report_projection
                full = AnalysisResult.model_validate(envelope['result'])
                reader = report_projection(full).conclusion
                native = full.conclusion
                position = full.evidence.get('position_evidence') or {}
                result = full.professional_result.get('analysis_result') or {}
                reports = {key: dict(available=bool(text.strip()),sha256=sha256(text.encode()).hexdigest(),
                            **({'text': text} if len(text.encode()) <= 128*1024 else {}),
                            storage='INLINE' if len(text.encode()) <= 128*1024 else 'ARTIFACT_REFERENCE',
                            version='wealthpilot.analysis.v1' if key=='native' else 'wealthpilot.analysis-report.v1')
                           for key,text in [('native',native),('reader',reader)]}
                state.update(symbol=position.get('symbol'), research_at=full.queried_at,
                    observation_dates=sorted({str(r['period']) for r in position.get('financial_research',{}).get('records',[])
                                              if r.get('field')=='observed_price' and r.get('period')}),
                    reports=reports, artifact=dict(artifact_id=row['job_id'], sha256=envelope['result_sha256'],
                        bytes=envelope['result_bytes'], schema_version=envelope['result_schema']),
                    professional_analysis_complete=full.professional_analysis_complete,
                    report_available=bool(native.strip()), synthetic=full.synthetic,
                    policy_reference=result.get('policy_reference'),
                    data_quality={k:full.data_quality[k] for k in ('status','issues','limitations') if k in full.data_quality},
                    stage_diagnostics=full.stage_diagnostics, issues=full.issues,
                    llm_observation=full.llm_observation)
            # Replace only derived lifecycle metadata. Request and review records
            # are immutable. Original Artifact is never copied or changed.
            previous = self.files.read(f'state-{sid}.json')
            if previous and previous.get('artifact'):
                if envelope and previous['artifact']['sha256'] != envelope['result_sha256']:
                    raise StoreError('SAMPLE_ARTIFACT_CONFLICT')
                # Same immutable payload may recover from an interrupted receipt
                # write. Refresh lifecycle only, preserving report versions.
                state = dict(previous, job_state=row['state'], issue=row.get('issue'),
                             business_status=row.get('business_status','UNKNOWN'))
                if state == previous:
                    return sid
            self.files.write(f'state-{sid}.json', state)
        return sid

    def _sample(self, sid):
        if not re.fullmatch(r'[a-f0-9]{64}', sid):
            raise ValueError('SAMPLE_ID_INVALID')
        sample = self.files.read(f'sample-{sid}.json')
        if not sample:
            raise ValueError('SAMPLE_UNKNOWN')
        return sample

    def _records(self, kind, sid):
        rows = []
        for name in sorted(os.listdir(self.files.fd)):
            if name.startswith(kind+'-') and name.endswith('.json'):
                row = self.files.read(name)
                if row['sample_id'] == sid:
                    rows.append(row)
        return rows

    def _append(self, kind, sid, payload):
        body = payload.model_dump(mode='json')
        identifier = digest([kind,sid,body['request_id']])
        name = f'{kind}-{identifier}.json'
        previous = self.files.read(name)
        if previous:
            if previous['input'] != body:
                raise ValueError('EVALUATION_REQUEST_CONFLICT')
            return previous, True
        return dict(schema_version='wealthpilot.research-'+kind+'.v1',
            **{kind+'_id':identifier}, sample_id=sid, created_at=now(), input=body), False

    def evaluate(self, sid, evaluation):
        with self.files.locked():
            self._sample(sid)
            state = self.files.read(f'state-{sid}.json') or {}
            review, exists = self._append('evaluation',sid,evaluation)
            if exists:
                return review
            if evaluation.target in {'native','reader'}:
                report = state.get('reports',{}).get(evaluation.target)
                available = bool(report and report.get('available',state.get('report_available',False)))
            else:
                reports = [r['input'] for r in self._records('feedback',sid)
                           if r['input']['report_kind']==evaluation.target and r['input'].get('report_text')]
                report = next((r for r in reports if sha256(r['report_text'].encode()).hexdigest()==evaluation.report_sha256),None)
                available = bool(report)
            if not available and evaluation.ratings is None and evaluation.report_sha256 is None:
                report = None
            if report and (report.get('sha256') or evaluation.report_sha256) != evaluation.report_sha256:
                raise ValueError('EVALUATION_REPORT_MISMATCH')
            if not report and evaluation.report_sha256 is not None:
                raise ValueError('EVALUATION_REPORT_MISMATCH')
            if evaluation.ratings and not available:
                raise ValueError('REPORT_UNAVAILABLE_USE_NOT_RATED')
            ratings = evaluation.ratings
            total = sum((r.score for r in ratings.values()),Decimal(0)) if ratings else None
            prior = [r for r in self._records('evaluation',sid)
                     if r['input']['target']==evaluation.target and r['input']['scorer_type']==evaluation.scorer_type]
            if prior and not evaluation.revision_of:
                raise ValueError('REEVALUATION_REQUIRES_REVISION_AND_REASON')
            old = None
            if evaluation.revision_of:
                old = self.files.read(f'evaluation-{evaluation.revision_of}.json')
                if not old or old['sample_id'] != sid or old['input']['target']!=evaluation.target:
                    raise ValueError('REVISION_SAMPLE_OR_TARGET_MISMATCH')
            review.update(total=str(total) if total is not None else None,
                          quality_status='N/R' if total is None else 'PASS' if total>=8 else 'FAIL',
                          scorer_provenance='CLIENT_REPORTED',
                          confirmation='HUMAN_REPORTED' if evaluation.scorer_type=='human' else 'UNCONFIRMED_AI',
                          score_delta=str(total-Decimal(old['total'])) if old and total is not None and old['total'] is not None else None)
            self.files.write(f"evaluation-{review['evaluation_id']}.json",review,immutable=True)
            return review

    def feedback(self, sid, feedback):
        with self.files.locked():
            self._sample(sid)
            review, exists = self._append('feedback',sid,feedback)
            if exists:
                return review
            if feedback.evaluation_id:
                old = self.files.read(f'evaluation-{feedback.evaluation_id}.json')
                if not old or old['sample_id']!=sid:
                    raise ValueError('FEEDBACK_EVALUATION_MISMATCH')
            review['delivery_provenance'] = ('HUMAN_REPORTED_FINAL_UNVERIFIED' if
                feedback.report_kind=='client_final_human_reported' else 'CLIENT_SUBMITTED_DRAFT_UNVERIFIED')
            self.files.write(f"feedback-{review['feedback_id']}.json",review,immutable=True)
            return review

    def query(self, *, symbol=None, sample_id=None, job_id=None, offset=0, limit=20, export=False, history_offset=0):
        if not 0<=offset or not 0<=history_offset or not 1<=limit<=20:
            raise ValueError('QUERY_PAGE_INVALID')
        with self.files.locked():
            rows=[]
            for name in sorted(os.listdir(self.files.fd)):
                if not name.startswith('sample-') or not name.endswith('.json'):
                    continue
                sample=self.files.read(name)
                sid=sample['sample_id']
                state=self.files.read(f'state-{sid}.json') or {}
                if ((sample_id and sid!=sample_id) or (job_id and sample['job_id']!=job_id)
                        or (symbol and (state.get('symbol') or sample['symbol'])!=symbol)):
                    continue
                reviews=self._records('evaluation',sid)
                rows.append(dict(sample=sample,state=state,evaluations=reviews,feedback=self._records('feedback',sid),
                                 evaluation_status='EVALUATED' if reviews else 'UNEVALUATED'))
            counts=Counter()
            historical_counts=Counter()
            for row in rows:
                historical=row['sample'].get('historical_assessment_reference')
                if historical:
                    historical_counts[historical['quality_status']]+=1
            for row in rows:
                # Keep target/scorer groups separate and retain latest revisions.
                groups={}
                for review in sorted(row['evaluations'],key=lambda r:(r['created_at'],r['evaluation_id'])):
                    groups[(review['input']['target'],review['input']['scorer_type'])]=review
                for (target,scorer),review in groups.items():
                    counts[f'{target}/{scorer}/{review["quality_status"]}']+=1
            # Histories have their own cursor: a frequently reviewed sample
            # must never turn a single MCP response into the whole archive.
            for row in rows:
                history=sorted(row['evaluations']+row['feedback'],key=lambda r:(r['created_at'],r.get('evaluation_id') or r['feedback_id']))
                selected=[]
                for entry in history[history_offset:history_offset+10]:
                    if selected and len(encode(selected+[entry]))>512*1024:
                        break
                    selected.append(entry)
                row['evaluations']=[r for r in selected if 'evaluation_id' in r]
                row['feedback']=[r for r in selected if 'feedback_id' in r]
                row['history_total']=len(history)
                nxt=history_offset+len(selected)
                row['history_next_offset']=nxt if nxt<len(history) else None
            page=[]
            for row in rows[offset:offset+limit]:
                if page and len(encode(page+[row]))>2*1024*1024:
                    break
                page.append(row)
            count=len(page)
            if not export:
                for row in page:
                    row['state']={k:v for k,v in row['state'].items() if k!='reports'} | {
                        'reports':{k:{n:x for n,x in v.items() if n!='text'} for k,v in row['state'].get('reports',{}).items()}}
            return dict(schema_version='wealthpilot.research-evaluation-export.v1', rubric=dict(version=RUBRIC,
                dimensions=list(DIMENSIONS),per_dimension_max=2,total_max=10,threshold=8),
                total_samples=len(rows), distribution=dict(counts), historical_reference_distribution=dict(historical_counts),items=page,
                next_offset=offset+count if offset+count<len(rows) else None,
                capacity=dict(quota_bytes=self.files.quota, used_bytes=sum(os.stat(n,dir_fd=self.files.fd).st_size for n in os.listdir(self.files.fd)),
                    policy='STOP_ARCHIVING_WHEN_FULL_NO_ARTIFACT_DELETION'),
                execution_authorized=False)
