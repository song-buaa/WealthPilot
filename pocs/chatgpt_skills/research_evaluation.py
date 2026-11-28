"""Private quality archive MCP adapters; no model, account or E1 operations."""
from typing import Annotated
from pydantic import Field
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError
from backend.services.research_evaluation import Evaluation, Feedback, SampleStore


def register(server, jobs, security_meta):
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    def call(operation, **kwargs):
        store = None
        try:
            store = SampleStore(jobs.root)
            return getattr(store, operation)(**kwargs)
        except Exception:
            # Validation/IO failures must never disclose question, report, path,
            # credential or raw exception. No side effect on the original job.
            raise ToolError('RESEARCH_EVALUATION_UNAVAILABLE_OR_INVALID') from None
        finally:
            if store is not None:
                store.close()

    @server.tool(meta=security_meta, annotations=write, description=(
        'Save an AI initial evaluation or append a human-reported review of an existing private L1 sample. '
        'After research, query by job_id for sample_id and exact native/reader SHA; score the actual report using rubric v1: '
        'financial_evidence, business_drivers, competition_counterevidence, valuation_returns, decision_opportunity_cost; '
        'each 0-2 with basis, deductions and report references. Server sums five scores; >=8 is quality PASS only. '
        'No real report: ratings=null, report_sha256=null, explain N/R. Unscored means UNEVALUATED, not zero. '
        'Identify AI model honestly; model claims/CI/completion are never quality scores. Same request_id retries are idempotent; '
        're-review needs a new request_id, revision_of and revision_reason. AI scores are unconfirmed; human status is client-reported. '
        'Does not save E1, confirm investment decisions or authorize trades.'))
    def save_research_evaluation(sample_id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')], evaluation: Evaluation) -> dict:
        return call('evaluate', sid=sample_id, evaluation=evaluation)

    @server.tool(meta=security_meta, annotations=read, description=(
        'Read long-lived private L1 samples and append-only quality history by canonical symbol, sample_id or job_id. '
        'Independent of normal Job report TTL. Returns fixed rubric, exact report hashes and target/scorer-separated quality distribution. '
        'Use export_research_evaluations for original questions, full saved reports and feedback. No new research or Provider access.'))
    def query_research_evaluations(symbol: str | None = None, sample_id: str | None = None,
                                   job_id: str | None = None, offset: int = 0, limit: int = 5, history_offset: int = 0) -> dict:
        return call('query', symbol=symbol, sample_id=sample_id, job_id=job_id, offset=offset, limit=limit, history_offset=history_offset)

    @server.tool(meta=security_meta, annotations=write, description=(
        'Append user feedback, an unverified client report draft, or a human-reported final report to a private L1 sample. '
        'Backend cannot observe the actual final ChatGPT reply: client_draft is NOT verified delivered text. '
        'Only explicit human attestation may use client_final_human_reported; still not independently verified. '
        'Never include credentials, full account data or unrelated chat. New feedback never overwrites an evaluation. No E1/policy/trade writes.'))
    def save_research_feedback(sample_id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')], feedback: Feedback) -> dict:
        return call('feedback', sid=sample_id, feedback=feedback)

    @server.tool(meta=security_meta, annotations=read, description=(
        'Export paginated private research samples, restricted original requests, immutable Artifact references, '
        'native/Reader report content, all evaluations and feedback for offline analysis. Follow next_offset for samples and each history_next_offset with that sample_id/history_offset for all review records; '
        'not a public download link. Original large Artifact is not duplicated or refreshed. Respect report provenance and UNKNOWN fields. '
        'Data remains within the same authenticated private MCP boundary; no account queries, Provider or model calls.'))
    def export_research_evaluations(symbol: str | None = None, sample_id: str | None = None,
                                    job_id: str | None = None, offset: int = 0, limit: int = 5, history_offset: int = 0) -> dict:
        return call('query', symbol=symbol, sample_id=sample_id, job_id=job_id, offset=offset, limit=limit, export=True, history_offset=history_offset)
