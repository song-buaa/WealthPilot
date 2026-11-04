"""Opt-in thin MCP adapter over the existing receipts and isolated Worker."""
from types import SimpleNamespace
from typing import Annotated, Literal
from mcp.types import ToolAnnotations
from pydantic import Field
from pocs.chatgpt_skills.analysis import AnalysisResult
from pocs.chatgpt_skills.position_jobs import PositionJobs, PositionAnalysisJob
from pocs.chatgpt_skills.diagnostics import request_run
from backend.services.s3_research_entry import CompanyPoolRequest


class CompanyPoolJob(PositionAnalysisJob):
    schema_version: Literal['wealthpilot.company-pool-job.v1'] = 'wealthpilot.company-pool-job.v1'


def register_pool_tools(server, settings, slot, meta, client_class):
    def execute(request, identifier):
        client = client_class(SimpleNamespace(**{**vars(settings), 'async_l1': True}))
        token = request_run.set(identifier)
        try:
            return AnalysisResult.model_validate(client.call('compare_company_pool', request.model_dump(mode='json')))
        finally:
            client.close()
            request_run.reset(token)

    jobs = PositionJobs(execute, slot)

    def response(value):
        return CompanyPoolJob.model_validate({**value.model_dump(mode='json'),
            'schema_version': 'wealthpilot.company-pool-job.v1'})

    @server.tool(meta=meta, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
        idempotent_hint=True, open_world_hint=True), description=(
        'For an explicit US company pool (e.g. AAPL NVDA GOOG MSFT META TSLA), start quality, valuation and five-year '
        'conditional-return comparison. Supply symbols and the user research purpose; no holdings or prepare is needed. '
        'request.method is optional: standard (default, original P1) or professional (reviewed P2 operating model, initially AAPL/MSFT/NVDA). '
        'Use professional when the user asks for professional company-specific earnings/valuation research; no client growth rates or PE inputs. '
        'Professional assumptions are server-versioned and must requalify current sources/windows; changed or unsupported evidence remains UNKNOWN. '
        'META original entitlement protection applies to both methods. '
        'Reuses original SEC/Price/Evidence/S3 via existing Skill and isolated Worker. Writes only the private frozen research archive. '
        'Research Only: automatic candidates are assumptions, not verified forecasts or Investment Policy. Preserve UNKNOWN, '
        'warnings and noncomparables; company priority is not portfolio allocation or a buy instruction. '
        'Generate one request_id and retain returned job_id; poll get_company_pool_research_result, never start again while pending. '
        'Identical ID and request deduplicate only in this process. After restart/expiry/unknown do not automatically restart. '
        'Only a new explicit user research request authorizes new work. No trades or policy changes.'))
    def start_company_pool_research(request_id: Annotated[str, Field(min_length=1, max_length=128,
        pattern=r'^[A-Za-z0-9:_-]+$')], request: CompanyPoolRequest) -> CompanyPoolJob:
        return response(jobs.start(request_id, request))

    @server.tool(meta=meta, annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
        idempotent_hint=True, open_world_hint=False), description=(
        'Poll the SAME company-pool job_id; wait about 15 seconds while running. No provider call or Worker on get. '
        'Completed: present the original comparison report and all company cards, sources, scenarios, assumptions, '
        'quality limits, noncomparables and method/decision clock. Save artifact_id for durable read. '
        'Receipts/results are process-local, expire after two hours; unknown never authorizes a new start.'))
    def get_company_pool_research_result(job_id: Annotated[str, Field(min_length=32, max_length=64,
        pattern=r'^[A-Za-z0-9_-]+$')]) -> CompanyPoolJob:
        return response(jobs.get(job_id))

    @server.tool(meta=meta, annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
        idempotent_hint=True, open_world_hint=False), description=(
        'Read a previously returned immutable company-pool artifact_id, including after server restart. '
        'Checks frozen source/view hashes and recomputes with the same method when available. '
        'Method drift requires original-version replay, never silently recalculates with changed rules. '
        'No provider, holdings, policy or order access. Original decision clock remains; this is not fresh market data.'))
    def read_company_pool_research(artifact_id: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]) -> AnalysisResult:
        client = client_class(settings)
        try:
            return AnalysisResult.model_validate(client.call('read_company_pool_research', dict(artifact_id=artifact_id)))
        finally:
            client.close()
