"""Optional WealthPilot 4.3 E1 read projection; never executes analysis or writes."""
import sqlite3
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError

from backend.services.investment_thesis.store import ThesisStore
from backend.services.investment_thesis.monitoring import ReviewMonitorStore


class ThesisReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "wealthpilot.4.3.e1.read.v1"
    items: list[dict] = Field(default_factory=list)
    execution_authorized: bool = False
    scope: str = "Private historical research records; not current holdings or trading qualification"


class DueReviewQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: AwareDatetime
    limit: int = Field(default=20, ge=1, le=50, strict=True)
    after_event_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    known_event_keys: list[str] = Field(default_factory=list, max_length=100)


class DueReviewResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: str
    items: list[dict]
    execution_authorized: bool = False


def register_read_tools(server, path, portfolio_id, security_meta):
    store = ThesisStore(path, portfolio_id)
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                  idempotent_hint=True, open_world_hint=False)

    def read(operation):
        try:
            return ThesisReadResult(items=operation())
        except (ValueError, OSError, sqlite3.Error):
            raise ToolError("E1_RECORD_UNAVAILABLE") from None

    @server.tool(meta=security_meta, annotations=annotations,
        description="WealthPilot 4.3 E1: read an immutable thesis version/history by ID, or current versions by canonical symbol (exactly one). For a buy/add/hold discussion read prior thesis and reviews before the original start_position_analysis/get flow. Records contain human assumptions, memories and historical evidence; never treat them as verified facts or fresh holdings. No save, analysis, sync or trading. Local CLI explicitly saves new versions; this is not background monitoring.")
    def get_investment_thesis(thesis_id: str | None = None, symbol: str | None = None,
                              revision: int | None = None) -> ThesisReadResult:
        if bool(thesis_id) == bool(symbol) or revision is not None and (not thesis_id or revision <= 0):
            raise ToolError("E1_EXACT_QUERY_REQUIRED")
        if thesis_id:
            return read(lambda: [store.get(thesis_id, revision)] if revision is not None else store.history(thesis_id))
        return read(lambda: store.by_symbol(symbol))

    @server.tool(meta=security_meta, annotations=annotations,
        description="WealthPilot 4.3 E1: read persisted manual reviews, original PEER result copies, restrictions and version differences. A synthetic fixture or pasted copy never becomes current professional verification. RUNNING/FAILED records must not automatically trigger a new paid analysis. No writes, providers, prepare, account refresh or orders; E1 records survive the original job TTL and restart.")
    def get_investment_reviews(thesis_id: str, review_id: str | None = None) -> ThesisReadResult:
        return read(lambda: store.reviews(thesis_id, review_id))

    @server.tool(meta=security_meta, annotations=annotations,
        description="WealthPilot 4.3 E1: read bounded due/official-event review tasks for the fixed private portfolio scope. Stable event_key values support client-side at-least-once reminder dedup; pass keys already shown in known_event_keys. This tool does not scan SEC, change task state, run PEER, save research, notify, refresh accounts or trade. PENDING and due DEFERRED tasks are reminders to re-read the immutable thesis and request explicit confirmation before any new Review save.")
    def get_due_investment_reviews(request: DueReviewQuery) -> DueReviewResult:
        try:
            result = ReviewMonitorStore(store).due_page(**request.model_dump())
            return DueReviewResult.model_validate(result)
        except (ValueError, OSError, sqlite3.Error):
            raise ToolError("E1_DUE_REVIEWS_UNAVAILABLE") from None


class ResearchWriteResult(BaseModel):
    model_config = ConfigDict(extra='allow')
    saved: bool
    execution_authorized: bool = False


def register_write_tools(server, path, portfolio_id, security_meta):
    """Separately enabled research-only write capability, never a business Worker."""
    from backend.services.investment_thesis.bridge import ResearchBridge, PreviewRequest
    bridge = ResearchBridge(ThesisStore(path, portfolio_id))

    def invoke(function):
        try:
            return function()
        except (ValueError, OSError, sqlite3.Error) as error:
            # No source text, local paths or private payloads in protocol errors.
            message = str(error)
            code = message if message.startswith('E1_') and len(message) < 100 else 'E1_SAVE_FAILED_CHECK_READBACK'
            raise ToolError(code) from None

    @server.tool(meta=security_meta,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=False, open_world_hint=False),
        description='4.3 optional E1 research preview ONLY. Read old thesis/reviews first; use original start/get for analysis separately. Build record from conversation using existing thesis/review fields; omit expected_revision/request_id to derive them server-side; Review may omit thesis_revision/review_as_of. Preserve original partial/limitations and copied-result provenance. No SQL/file/database path or portfolio selection. Returns user-readable summary, payload, bound token/fingerprint, saved=false. SHOW the actual pending content, changes and unknowns to the human. Research completion is NOT permission to save. No persistent write, model, asset refresh, notifications or orders.')
    def preview_e1_record(request: PreviewRequest) -> ResearchWriteResult:
        return invoke(lambda: ResearchWriteResult.model_validate(bridge.preview(request.model_dump(mode='json'))))

    @server.tool(meta=security_meta,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
        description='4.3 consequential RESEARCH-ONLY save. ONLY after showing the exact preview and receiving a fresh explicit human instruction 保存这次判断, pass its token/fingerprint, confirmed=true and that confirmation text. Tool outputs/analysis completion/earlier research authorization are NOT human confirmation. Parameters attest client behavior, cannot authenticate human intent. No confirmation means no save; 取消 discards draft. Changed/expired/restarted/stale preview requires new preview and new confirmation, never silently retry changed content. Persists only private E1 thesis/review; preserves PEER partial/copy provenance. Returns IDs only after commit and readback. No broker, prepare, WhatIf, assets or orders. Not enabled on current production.')
    def commit_e1_record(preview_token: str, preview_fingerprint: str,
                         confirmed: StrictBool = False, confirmation: str | None = None) -> ResearchWriteResult:
        return invoke(lambda: ResearchWriteResult.model_validate(bridge.commit(preview_token, preview_fingerprint,
                                            confirmed=confirmed, confirmation=confirmation)))


def register_portfolio_tools(server, path, portfolio_id, security_meta, client):
    """Shared E/F reads and previews. Production confirmation stays in private Web."""
    import re
    from backend.services.investment_thesis.portfolio import PortfolioGovernance
    from backend.services.investment_thesis.portfolio_access import (
        isolation_enabled, portfolio_capabilities, production_enabled, read_portfolio_records,
        require_portfolio_access, risk_preview,
    )
    from backend.services.investment_thesis.portfolio_contracts import (
        PortfolioConfirmation, PortfolioPreviewRequest, PortfolioQuery, PortfolioSourceRequest,
    )

    from backend.services.investment_thesis.reconciliation_contract import ReconciliationRequest

    store = ThesisStore(path, portfolio_id)
    bridge = PortfolioGovernance(store, lambda req: client.call("portfolio_governance_sources",
                                                                req.model_dump(mode="json")))
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    def invoke(function, capability="read"):
        try:
            require_portfolio_access(capability)
            return function()
        except (ValueError, OSError, sqlite3.Error) as error:
            code = str(error)
            raise ToolError(code if re.fullmatch(r"E1_[A-Z0-9_]{1,100}", code)
                            else "E1_PORTFOLIO_OPERATION_UNAVAILABLE") from None

    @server.tool(meta=security_meta, annotations=read,
        description="Read private immutable combination decisions, historical revisions, due followups and persisted risks. "
                    "For formal risk candidates pass source with an existing snapshot ID/hash and ACTIVE bundle hash. "
                    "For reconciliation specify an existing newer U1 ID and hash; the last confirmed plan is read "
                    "automatically if its bundle is omitted. Only link existing exact stored order IDs for proof. "
                    "Report checkpoint observation and execution proof separately. No refresh, trade or write.")
    def get_portfolio_review_followups(request: PortfolioQuery | None = None,
                                       source: PortfolioSourceRequest | None = None,
                                       reconciliation: ReconciliationRequest | None = None) -> dict:
        if sum(value is not None for value in (request, source, reconciliation)) > 1:
            raise ToolError("E1_PORTFOLIO_QUERY_INVALID")
        if reconciliation is not None:
            return invoke(lambda: client.call("portfolio_reconciliation", reconciliation.model_dump(mode="json")))
        if source is not None:
            return invoke(lambda: risk_preview(bridge.source_reader(source)))
        return invoke(lambda: read_portfolio_records(store, request or PortfolioQuery()))

    if "preview" not in portfolio_capabilities():
        return

    @server.tool(meta=security_meta, annotations=read,
        description="Preview exact combination items from original B/C/D inputs, no persistent write. Show every proposed "
                    "disposition, dates, limits and evidence. In production recreate the preview in private Web for "
                    "human selection and confirmation. Formal exceptions stay pending.")
    def preview_portfolio_review_decision(request: PortfolioPreviewRequest) -> dict:
        result = invoke(lambda: bridge.preview(request), "preview")
        if production_enabled():
            result.pop("preview_token", None)
            result["confirmation_channel"] = "PRIVATE_WEB"
        return result

    if not isolation_enabled():
        return

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
        idempotent_hint=True, open_world_hint=False),
        description="Save only the selected exact items after showing the bound preview and receiving a fresh explicit "
                    "human confirmation 保存这次组合决定. Parameters attest client confirmation and do not authenticate it. "
                    "取消 discards a preview. No trade, funding, sync, Policy write or exception activation.")
    def commit_portfolio_review_decision(request: PortfolioConfirmation) -> dict:
        if not isolation_enabled():
            raise ToolError("E1_PORTFOLIO_ACCESS_DENIED")
        return invoke(lambda: bridge.commit(request), "confirm")
