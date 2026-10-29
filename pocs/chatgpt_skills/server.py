"""Opt-in MCP iteration endpoint; does not alter the deployed PoC02 server."""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.llm_observation import run_id, safe_metadata
from app.analysis_runtime import Policy, policy, ENV_KEYS as TIME_ENV_KEYS
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from backend.services.instruments.tradability import TradabilityQuery
from pocs.chatgpt_skills.contracts import TradabilityResult
from typing import Annotated
from pocs.chatgpt_skills.contracts import Details, ExplicitOrder, TradeResult, PrepareResult, TradeSyncQuery, TradeSyncResult, TradeHistoryQuery, TradeHistoryResult
from pocs.chatgpt_skills.trade_link import IBKRLinkOrder, LinkOrder, TradeLinkResult, prepare_link
from pocs.chatgpt_skills.worker import SAFE_ERRORS, MODEL_ENV_KEYS
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult
from pocs.chatgpt_skills.position_jobs import PositionJobs, PositionAnalysisJob
from pocs.poc02_wealth_mcp import server as overview
from pocs.chatgpt_skills.diagnostics import (ANALYSIS_OPERATIONS, request_run, diagnostic,
    safe_diagnostic, milliseconds)


class BusinessClient:
    def __init__(self, settings):
        self.settings, self.process = settings, None
        self.lock = threading.Lock()

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.wait(timeout=Policy().cleanup_grace_seconds if getattr(self.settings, "async_l1", False) else 5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                if getattr(self.settings, "async_l1", False):
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
                else:
                    self.process.wait(timeout=5)
            self.process = None

    def call(self, operation, arguments):
        with self.lock:
            identifier = (request_run.get() or run_id()) if operation in ANALYSIS_OPERATIONS else None
            started = time.monotonic()
            async_l1 = operation == "analyze_position" and getattr(self.settings, "async_l1", False)
            time_policy = policy(getattr(self.settings, "analysis_env_file", None)) if async_l1 else None
            stage = "worker_start"
            def record(value):
                if identifier:
                    overview.record_analysis_event(self.settings, value)
            def fail(category, *, metadata=None, exit_code=None):
                error = ToolError("ANALYSIS_RUN_ID_MISMATCH" if category == "run_id_mismatch" else "BUSINESS_OPERATION_UNAVAILABLE")
                if identifier:
                    error.analysis_diagnostic = diagnostic(identifier, stage, category=category, protocol=True,
                        elapsed_ms=milliseconds(started), observation=metadata, exit_code=exit_code)
                    if metadata:
                        error.llm_observation = metadata
                return error
            try:
                if self.process is None or self.process.poll() is not None:
                    self.close()
                    record(diagnostic(identifier, stage)) if identifier else None
                    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR", "WEALTHPILOT_ANALYSIS_CACHE_DIR", "WEALTHPILOT_STAGE_TARGETS_PATH") if k in os.environ}
                    env.update({k:os.environ[k] for k in MODEL_ENV_KEYS | TIME_ENV_KEYS if k in os.environ})
                    env.update(PYTHON_DOTENV_DISABLED="1", IBKR_READ_ONLY_MODE="true",
                               ENABLE_IBKR_LIVE_TRADING="false", ENABLE_TIGER_LIVE_TRADING="false",
                               FUTU_READ_ONLY_MODE="true", BROKER_MODE="mock")
                    command = [self.settings.business_python, str(Path(__file__).with_name("worker.py")),
                               "--portfolio-id", str(self.settings.portfolio_id)]
                    if async_l1:
                        command += ["--single-analysis"]
                    if getattr(self.settings, "analysis_env_file", None):
                        command += ["--analysis-env-file", self.settings.analysis_env_file]
                    if self.settings.real_config:
                        command += ["--real-config", self.settings.real_config]
                    if self.settings.mock_database:
                        command += ["--mock-database", self.settings.mock_database]
                    self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, env=env,
                        cwd=getattr(self.settings, "worker_cwd", None))
                stage = "request_send"
                envelope = {"operation": operation, "arguments": arguments}
                if async_l1:
                    envelope["analysis_deadline_monotonic"] = started + time_policy.total_seconds
                if identifier:
                    envelope.update(analysis_run_id=identifier, diagnostic_events=True)
                record(diagnostic(identifier, stage)) if identifier else None
                self.process.stdin.write(json.dumps(envelope)+"\n")
                self.process.stdin.flush()
                stage = "ipc_wait"
                record(diagnostic(identifier, stage)) if identifier else None
                # One absolute deadline, including progress frames; no timeout extension or replay.
                deadline = started + time_policy.ipc_seconds if async_l1 else time.monotonic() + (270 if identifier else 45)
                pending = b""
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    if b"\n" not in pending:
                        remaining = max(0, deadline - time.monotonic())
                        if not select.select([self.process.stdout], [], [], remaining)[0]:
                            raise TimeoutError()
                        try:
                            descriptor = self.process.stdout.fileno()
                        except (AttributeError, OSError, ValueError):
                            chunk = self.process.stdout.readline().encode()  # in-memory test stream
                        else:
                            chunk = os.read(descriptor, 65536)
                        if not chunk:
                            raise EOFError()
                        pending += chunk
                        continue
                    line, pending = pending.split(b"\n", 1)
                    stage = "ipc_receive"
                    response = json.loads(line)
                    if not isinstance(response, dict):
                        raise TypeError()
                    if response.get("type") != "analysis_diagnostic":
                        break
                    value = safe_diagnostic(response.get("diagnostic"), identifier)
                    if value is None:
                        raise fail("run_id_mismatch")
                    record(value)
                    stage = "ipc_wait"
                record(diagnostic(identifier, "ipc_receive")) if identifier else None
                if type(response.get("ok")) is not bool:
                    raise TypeError()
                if not response["ok"]:
                    code = response.get("error")
                    error = ToolError(code if isinstance(code, str) and code in SAFE_ERRORS else "BUSINESS_OPERATION_UNAVAILABLE")
                    if identifier and code == "WORKER_START_REFUSED":
                        stage = "worker_start"
                        raise fail("worker_start_refused", exit_code=self.process.poll())
                    metadata = safe_metadata(response.get("llm_observation"))
                    if identifier and metadata and metadata["analysis_run_id"] != identifier:
                        raise fail("run_id_mismatch")
                    if identifier:
                        value = safe_diagnostic(response.get("analysis_diagnostic"), identifier)
                        if "analysis_diagnostic" in response and value is None:
                            raise fail("run_id_mismatch")
                        if value:
                            error.analysis_diagnostic = value
                        if metadata:
                            error.llm_observation = metadata
                    raise error
                result = response["result"]
                if not isinstance(result, dict):
                    raise TypeError()
                if identifier:
                    metadata = safe_metadata(result.get("llm_observation"))
                    if metadata and metadata["analysis_run_id"] != identifier:
                        raise fail("run_id_mismatch")
                    result.pop("llm_observation", None)
                    if metadata is not None:
                        result["llm_observation"] = metadata
                return result
            except ToolError:
                raise
            except (OSError, ValueError, KeyError, TypeError, TimeoutError, EOFError) as caught:
                category = ("worker_start_failed" if stage == "worker_start" else
                    "ipc_timeout" if isinstance(caught, TimeoutError) else
                    "worker_exited" if isinstance(caught, EOFError) else
                    "invalid_json" if isinstance(caught, ValueError) else
                    "invalid_envelope" if isinstance(caught, (TypeError, KeyError)) else "ipc_io")
                exit_code = self.process.poll() if self.process is not None else None
                if not identifier:
                    self.close()  # Preserve non-analysis client cleanup and no-replay policy.
                raise fail(category, exit_code=exit_code) from None


def create_server(settings):
    client = BusinessClient(settings)
    analysis_settings = SimpleNamespace(**vars(settings))
    analysis_settings.business_python = getattr(settings, "analysis_business_python", None) or getattr(settings, "business_python", "")
    analysis_settings.worker_cwd = getattr(settings, "analysis_working_directory", None)
    analysis_client = BusinessClient(analysis_settings)
    analysis_lock = threading.Lock()
    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            client.close()
            analysis_client.close()
    prepare_config = getattr(settings, "prepare_config", None)
    prepare_instructions = (" For a new portfolio evaluation, FIRST call prepare_asset_analysis once with a new stable analysis_request_id. "
        "Reuse the SAME ID after timeout/pending; do not create another task to retry. When ready pass its snapshot_id to all wealth, holdings and professional analysis reads for this task. "
        "If pending, wait/retry the same prepare; if expired or unavailable, explain the limitation. Never silently substitute live data for a snapshot_id. "
        "Follow data_quality.analysis_eligibility A/B/C and their scoped blockers: A strategic allocation, B approximate amounts, C precise quantities/prices. "
        "These are independent purposes, not nested permissions: single-security C never grants portfolio-wide B. Show the qualification_scope and actual blockers; never assume FX conversion or margin from another currency's cash. "
        "Use request.analysis_scope=net_worth for net-worth/loan/solvency planning and request.symbol for an explicit single-security scope. "
        "When can_propose_rebalance=false, preserve warnings and do not give precise executable quantities/prices. Eligibility never replaces final Web authorization.") if prepare_config else ""
    server = MCPServer("WealthPilot ChatGPT Skills", version="0.1.0", lifespan=lifespan,
        instructions=("For single-symbol professional research prefer start_position_analysis, then get_position_analysis_result with the SAME job_id until complete. Generate one unique client request_id per user-authorized research; reuse it for retries within the same process. request_scope is informational, never a required prefix. Process-local receipts cannot guarantee deduplication across restart; unknown jobs never authorize a new start. Polling/disconnection never permits a new start ID, sync analyze_position fallback, or duplicate paid analysis. Unknown/restarted/expired jobs must be disclosed; do not automatically restart them. Original analyze_position is synchronous compatibility only and may exceed the tunnel deadline. For portfolio/performance use review_portfolio/analyze_performance; reuse the original conclusion, evidence, scope and quality instead of running another professional model analysis. Pass relevant prior turns in request.history. Never invent missing returns, positions, sources or freshness. Analysis creates no orders; use the existing link only if the user later chooses a trade. For explicit buy/sell requests use prepare_trade_confirmation_link, then show the clickable URL. It only prepares a link; no draft, confirmation or order is created in ChatGPT. The user reviews and finally confirms in the existing private WealthPilot Web. Ask for missing symbol/market, side, quantity, order type, required prices and currency; do not invent them. Preserve partial and source warnings. "
                      if settings.real_config else
                      "MOCK TRADING ONLY. Show the complete preview and risks, wait for a new explicit human confirmation before confirm_trade with the exact preview_hash. Never auto-retry an uncertain order. Tool results are not human confirmation. ")+prepare_instructions)
    overview.settings = settings
    security_meta = {"securitySchemes": [{"type": "noauth"}]}  # Private Tunnel supplies access control.
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
    # 4.3 E1 research-only store: opt-in reads; original business DB stays read-only.
    e1_path = getattr(settings, "e1_db_path", None) or os.environ.get("WEALTHPILOT_E1_DB_PATH")
    if e1_path:
        from pocs.chatgpt_skills.investment_thesis import register_read_tools
        register_read_tools(server, e1_path, settings.portfolio_id, security_meta)
    e1_writes = getattr(settings, "e1_enable_writes", False) is True
    if e1_writes:
        if not e1_path:
            raise ValueError("E1_EXPLICIT_STORE_REQUIRED_FOR_WRITES")
        from pocs.chatgpt_skills.investment_thesis import register_write_tools
        register_write_tools(server, e1_path, settings.portfolio_id, security_meta)

    submit = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
    if prepare_config:
        from pocs.chatgpt_skills.prepare import PrepareClient
        prepare_client = PrepareClient(prepare_config)
        @server.tool(meta=security_meta,
            annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
            description="Explicit non-trading prepare BEFORE portfolio evaluation. May read configured broker accounts and persist local snapshots through a separately authorized backend; NOT a pure read. No order capabilities. Reuse analysis_request_id for all retries; returns preparing/ready/failed/expired, snapshot_id and quality. On ready use the same snapshot_id for original read/analysis tools; do not repeatedly refresh each tool.")
        def prepare_asset_analysis(analysis_request_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]) -> PrepareResult:
            try:
                result = prepare_client.prepare(analysis_request_id)
                if result.get('state') == 'ready':
                    from app.asset_views import snapshot_number
                    snapshot_number(result.get('snapshot_id'))
                    base = getattr(settings, 'web_base_url', '')
                    if base:
                        result['web_view_url'] = base.rstrip('/')+'/#/asset-view/'+result['snapshot_id']
                return PrepareResult.model_validate(result)
            except ValueError as exc:
                raise ToolError(str(exc)) from None

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,open_world_hint=True),
        description="Read account-scoped stock/ETF tradability evidence (YES/NO/UNKNOWN). Reuse opaque account_ref from holdings (stored snowball means IBKR); never pass raw accounts/Portfolio/credentials. With the existing protected prepare bridge, query original broker contract metadata read-only; otherwise stored identity only. Contract existence, past fills, trade=True or successful WhatIf do NOT prove account BUY permission. Missing authoritative account permission returns UNKNOWN. Fees remain UNKNOWN without a real estimate; never place/test/cancel an order or assume mutual fund subscription support.")
    def get_instrument_tradability(request: TradabilityQuery) -> TradabilityResult:
        arguments=request.model_dump()
        if getattr(settings,'prepare_config',None):
            try:
                return TradabilityResult.model_validate(prepare_client.tradability(arguments))
            except ValueError:
                result=client.call('get_instrument_tradability',arguments)
                result['issues'] = list(dict.fromkeys(
                    [v for v in result['issues'] if v != 'LIVE_ACCOUNT_QUERY_NOT_CONFIGURED']
                    + ['LIVE_ACCOUNT_QUERY_UNAVAILABLE']))
                return TradabilityResult.model_validate(result)
        return TradabilityResult.model_validate(client.call('get_instrument_tradability',arguments))

    @server.tool(meta=security_meta, annotations=read, description="Existing wealth overview contract and WealthService calculations. Preserve synthetic, integer CNY minor units and partial/unavailable warnings.")
    def get_wealth_overview(currency: Literal["CNY"] = "CNY", snapshot_id: str | None = None) -> overview.Overview:
        if snapshot_id:
            return overview.Overview.model_validate(client.call("get_wealth_overview", {"snapshot_id":snapshot_id}))
        if settings.real_config:
            return overview.get_wealth_overview(currency)
        return overview.Overview.model_validate(client.call("get_wealth_overview", {}))

    @server.tool(meta=security_meta, annotations=read, description="Read existing manual asset/liability detail, including excluded or linked items. CNY current_value and original currency value in major units. No writes or notes/credentials.")
    def get_manual_wealth_items(kind: Literal["ASSET", "LIABILITY"] | None = None, item_id: int | None = None, snapshot_id: str | None = None) -> Details:
        return Details.model_validate(client.call("get_manual_wealth_items", dict(kind=kind, item_id=item_id, snapshot_id=snapshot_id)))

    @server.tool(meta=security_meta, annotations=read, description="Read stored investment account groups and holdings via existing Portfolio services. Returns quantities, CNY market value and P/L, original prices/currency, existing cross-account symbol weights and source dates. Optional opaque account_ref and canonical symbol filter; never raw account credentials. Not live broker balances.")
    def get_investment_details(account_ref: str | None = None, symbol: str | None = None, snapshot_id: str | None = None) -> Details:
        return Details.model_validate(client.call("get_investment_details", dict(account_ref=account_ref, symbol=symbol, snapshot_id=snapshot_id)))

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,open_world_hint=False),
        description="Read stored original order/fill and asset-sync coverage. Optional order_id/batch_id/canonical symbol, or latest ten orders. NEVER refresh broker or submit/cancel/retry. Distinguish filled from SYNC_PENDING/RUNNING/DELAYED/FAILED/UNVERIFIED/SYNCED; only SYNCED proves target coverage. UNKNOWN commission is not WhatIf fees. After trading use a NEW prepare for analysis, preserving pending warnings; existing fixed views never change.")
    def get_trade_and_sync_status(request: TradeSyncQuery) -> TradeSyncResult:
        return TradeSyncResult.model_validate(client.call("get_trade_and_sync_status",request.model_dump()))

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,open_world_hint=False),
        description="Read imported broker execution history and deterministic moving-weighted-cost attribution. Never refresh/import or trade. Optional opaque history account_ref, canonical symbol/conid, inclusive start/exclusive end and snapshot_id. Reuse the current analysis snapshot_id; dates filter displayed facts, not reset cost basis. Actual fees may be UNKNOWN. Distinguish current broker book P&L, broker-reported realized P&L, system-calculated realized/unrealized, and holding return; missing opening/coverage/FX blocks unsupported conclusions. No portfolio TWR/MWR or synthetic dividends/interest. Returns separate account/security/currency groups and coverage evidence.")
    def get_trade_history(request: TradeHistoryQuery) -> TradeHistoryResult:
        return TradeHistoryResult.model_validate(client.call('get_trade_history',request.model_dump(mode='json')))

    @server.tool(meta=security_meta,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
        description="Prepare a clickable private WealthPilot Web trade-confirmation URL from explicit BUY/SELL parameters. Pure validation/URL generation: never creates a draft, changes data, connects to a broker or submits an order. Ask for missing required fields instead of guessing. Show the link and order summary; user opens the existing Web, edits/reviews, and finally confirms there. LIMIT needs limit_price; CONDITIONAL_LIMIT also needs trigger_price. US/HK single-order GTC only. For IB01 use the IBKR budget variant: broker=IBKR, symbol=IB01 (or IB01:LSE/IB01:UK/IB01:LSEETF), side=BUY, order_type=LIMIT, currency=USD, target_amount=the user total USD outlay cap INCLUDING fees and safety cushion, time_in_force=DAY. It opens the unified existing Web confirmation dialogs backed by the qualified LSEETF/USD/SMART ExecutionBatch; do not invent quantity or limit_price or relabel IB01 as US. Preserve all P1 data-quality warnings; do not call Mock trade tools for real requests.")
    def prepare_trade_confirmation_link(order: LinkOrder | IBKRLinkOrder) -> TradeLinkResult:
        try:
            return prepare_link(getattr(settings, "web_base_url", ""), order)
        except ValueError as error:
            raise ToolError(str(error)) from None

    def analysis_call(operation, request, *, _client=None, _identifier=None, _reserved=False):
        if not _reserved and not analysis_lock.acquire(blocking=False):
            raise ToolError("ANALYSIS_BUSY")
        worker_client = _client if _client is not None else analysis_client
        identifier, started = _identifier or run_id(), time.monotonic()
        token = request_run.set(identifier)
        final = diagnostic(identifier, "mcp_received")
        overview.record_analysis_event(settings, final)
        stage = "ipc_receive"
        try:
            try:
                value = worker_client.call(operation, request.model_dump(mode="json"))
                stage = "pydantic_validation"
                overview.record_analysis_event(settings, diagnostic(identifier, stage))
                result = AnalysisResult.model_validate(value)
                final = diagnostic(identifier, "mcp_return", status=result.status,
                    category="business_unavailable" if result.status == "unavailable" else "NONE",
                    observation=result.llm_observation)
                return result
            except ToolError as error:
                detail = safe_diagnostic(getattr(error, "analysis_diagnostic", None), identifier)
                final = detail or diagnostic(identifier, stage, category="business_error", protocol=True)
                # Only an observed business analysis failure may degrade into a business result.
                # Launch/IPC/JSON/identity failures remain actual MCP errors.
                metadata = safe_metadata(getattr(error, "llm_observation", None))
                if (str(error) in SAFE_ERRORS or detail and detail["protocol_is_error"] or
                        metadata is None):
                    final["protocol_is_error"] = True
                    raise
                from datetime import datetime, timezone
                result = AnalysisResult(synthetic=bool(getattr(settings, "mock_database", None)), scenario=operation,
                    status="unavailable", issues=["ANALYSIS_WORKER_UNAVAILABLE"],
                    queried_at=datetime.now(timezone.utc).isoformat(), conclusion="专业分析未完成；未创建交易。",
                    llm_observation=metadata)
                final = diagnostic(identifier, "analyze_peer", category="business_error",
                    status="unavailable", observation=metadata)
                return result
            except ValidationError:
                final = diagnostic(identifier, stage, category="validation_failed", protocol=True,
                    observation=value.get("llm_observation") if isinstance(value, dict) else None)
                raise ToolError("ANALYSIS_RESULT_INVALID") from None
            except Exception:
                final = diagnostic(identifier, stage, category="unexpected_error", protocol=True)
                raise ToolError("BUSINESS_OPERATION_UNAVAILABLE") from None
            finally:
                try:
                    worker_client.close()
                except Exception:
                    # Cleanup is diagnostic only; do not replace an already known business/protocol result.
                    overview.record_analysis_event(settings, diagnostic(identifier, "worker_cleanup",
                        category="cleanup_failed", elapsed_ms=milliseconds(started)))
        finally:
            final["elapsed_ms"] = milliseconds(started)
            overview.record_analysis_event(settings, final)
            request_run.reset(token)
            if not _reserved:
                analysis_lock.release()

    def execute_position_job(request, identifier):
        # Never owned/closed by a streamable-HTTP request/session lifespan.
        return analysis_call("analyze_position", request, _client=BusinessClient(SimpleNamespace(**{**vars(analysis_settings), "async_l1":True})),
            _identifier=identifier, _reserved=True)

    position_jobs = PositionJobs(execute_position_job, analysis_lock)

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=True,
        destructive_hint=False, idempotent_hint=True, open_world_hint=True),
        description=("Preferred single-symbol professional research. Quickly start exactly one original PEER/readonly Worker; no sync, prepare or trades. "
            "Generate a unique request_id (for example a UUID; 1-128 letters/digits/:/_/-). No scope prefix or tool refresh is required; legacy scoped IDs are also accepted. "
            "Keep the EXACT ID and identical request parameters when retrying a lost start response in the same process; it returns the existing job. "
            "Same ID with changed parameters is rejected within the process. request_scope is informational only. Receipts are not persistent: across restart even the same ID may start new paid work. Never automatically re-submit after restart or unknown/expired job; only an explicit new user research request authorizes new work. "
            "Supply the same original AnalysisRequest (question, optional symbol/history/snapshot). No snapshot is required for normal LI research. "
            "Preserve user's current holding/reduction/exit intent and explicit review date in question/history; optional research_context carries only user research assumptions (objective, review_date, reference_quantity, tranche_fractions, historical_price_bands), never verified holdings or confirmed allocation. Do not invent missing assumptions or ask the user to construct JSON. "
            "Save returned job_id and query get_position_analysis_result; do not call sync analyze_position or start again while waiting. "
            "One analysis at a time; busy/capacity errors do not start work. Results remain private in memory for two hours after completion; restart loses them."))
    def start_position_analysis(request_id: Annotated[str, Field(min_length=1, max_length=128,
            pattern=r"^[A-Za-z0-9:_-]+$")], request: AnalysisRequest) -> PositionAnalysisJob:
        return position_jobs.start(request_id, request)

    @server.tool(meta=security_meta, annotations=ToolAnnotations(read_only_hint=True,
        destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        description="Quickly read the SAME opaque job_id returned by start_position_analysis. No model, Provider, Worker launch or PEER execution on get. Running: wait about 15 seconds between queries; no busy polling or new start ID. Completed: return the original full AnalysisResult, including professional_result/conclusion/evidence/quality/limitations, at its original queried_at; this is NOT refreshed market data or new trading qualification. Respect partial/unavailable and original qualification/trading restrictions. Unknown/expired/restarted: disclose unavailable, never automatically re-run. Process-local private results expire two hours after completion.")
    def get_position_analysis_result(job_id: Annotated[str, Field(min_length=32, max_length=64,
            pattern=r"^[A-Za-z0-9_-]+$")]) -> PositionAnalysisJob:
        return position_jobs.get(job_id)

    @server.tool(meta=security_meta, annotations=read, description="Synchronous compatibility only: for ChatGPT long single-symbol research use start_position_analysis + get_position_analysis_result to avoid the 120-second tunnel deadline. Do not use this as a fallback while a job is running. Single-symbol professional decision via existing PositionDecision/position_single PEER, rules, signals, research and new-entry logic. Supply user's question, optional symbol and relevant history. Pass target_versions and scenario_legs in request; bind each scenario to the same snapshot_id. Client claimed target confirmation is an assumption unless server evidence verifies it. Returns original conclusion/evidence/quality. No trades. Ask for clarification when needed; fallback/unavailable is not a valid recommendation.")
    def analyze_position(request: AnalysisRequest) -> AnalysisResult:
        return analysis_call("analyze_position", request)

    @server.tool(meta=security_meta, annotations=read, description="Review investment portfolio/allocation via original PortfolioReview/AssetAllocation PEER and AllocationService snapshot/default targets/deviation. Pass target_versions and scenario_legs in request with one snapshot_id; client claimed target confirmation alone is unverified. Weights refer to investment holdings, NOT full personal wealth. No sector inference without evidence. Preserve partial/failed sources. No trades.")
    def review_portfolio(request: AnalysisRequest) -> AnalysisResult:
        return analysis_call("review_portfolio", request)

    @server.tool(meta=security_meta, annotations=read, description="Analyze CURRENT holdings P&L and profit/loss contributors via original PerformanceAnalysis PEER and existing performance calculation. NOT period returns, TWR/MWR, realized P&L or historical trade attribution. Explicit source dates/quality/scope; never fabricate returns. No trades.")
    def analyze_performance(request: AnalysisRequest) -> AnalysisResult:
        return analysis_call("analyze_performance", request)

    # Real production: P1 + pure P2 URL + three analysis reads.
    # The original Mock workflow stays available to isolated automated tests.
    if settings.real_config:
        return server

    @server.tool(meta=security_meta, annotations=write, description="Persist a pending ActionDraft from explicit user order parameters; no broker submission. ChatGPT extracts parameters using this schema, without additional server LLM. Reuse the same request_id for retries; changed order needs a new ID. Return complete preview and existing risk warnings; show them to the user and wait.")
    def create_trade_draft(instruction: str, request_id: Annotated[str, Field(min_length=1, max_length=36)], order: ExplicitOrder) -> TradeResult:
        return TradeResult.model_validate(client.call("create_trade_draft", dict(instruction=instruction, request_id=request_id, order=order.model_dump(mode="json"))))

    @server.tool(meta=security_meta, annotations=read, description="Read full pending order preview and current original risk checks. Show symbol/side/quantity/type/prices/currency/GTC/broker/scope/fees-not-estimated, risk warnings and preview_hash. No execution.")
    def get_trade_preview(draft_id: str) -> TradeResult:
        return TradeResult.model_validate(client.call("get_trade_preview", dict(draft_id=draft_id)))

    @server.tool(meta=security_meta, annotations=submit, description="Consequential write: only after showing this exact preview and receiving a new explicit human confirmation in ChatGPT, pass confirmed=true and preview_hash. Preserve original risk_confirmation text when required. Calls original confirm_draft and OrderManager.place_order. This iteration exposes only an isolated Mock broker; do not claim real submission. Duplicate confirmations return the existing attempt; unknown is not retryable.")
    def confirm_trade(draft_id: str, preview_hash: str, confirmed: bool, risk_confirmation: str = "") -> TradeResult:
        return TradeResult.model_validate(client.call("confirm_trade", dict(draft_id=draft_id, preview_hash=preview_hash, confirmed=confirmed, risk_confirmation=risk_confirmation)))

    @server.tool(meta=security_meta, annotations=write, description="Refresh broker-authoritative order state through original OrderManager.sync_order_status; persists state/fills/audit, so NOT a read-only tool. No order submission or automatic retry. Preserve pending/partial/rejected/unknown states. Mock results are synthetic.")
    def get_trade_order_status(draft_id: str) -> TradeResult:
        return TradeResult.model_validate(client.call("get_trade_order_status", dict(draft_id=draft_id)))
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8810)
    parser.add_argument("--events", help="Optional private metadata-only completion ledger, outside Git")
    parser.add_argument("--business-python", required=True)
    parser.add_argument("--real-config")
    parser.add_argument("--access-evidence")
    parser.add_argument("--analysis-working-directory", help="Reuse the existing complete Web working directory for relative knowledge/rules assets")
    parser.add_argument("--analysis-business-python", help="Reuse the existing complete Web business Python for PEER dependencies")
    parser.add_argument("--analysis-env-file", help="Existing local runtime JSON; only original analysis provider settings are read")
    parser.add_argument("--prepare-config", help="Opt-in private loopback prepare URL/token-file configuration; seven existing tools stay read-only")
    parser.add_argument("--e1-db-path", help="4.3 opt-in private research store; read-only unless separately enabled, no creation or business writes")
    parser.add_argument("--e1-enable-writes", action="store_true", help="Separately authorized E1 preview/confirmed research save ONLY; default off; never enables business/trading writes")
    parser.add_argument("--mock-database")
    parser.add_argument("--portfolio-id", type=int, default=1)
    parser.add_argument("--web-base-url", default=os.environ.get("WEALTHPILOT_WEB_BASE_URL", ""))
    args = parser.parse_args()

    overview.validate_real_access(args, parser)
    if not args.real_config and not args.mock_database:
        parser.error("an explicit synthetic fixture or reviewed read configuration is required")
    # Real reads and Mock writes intentionally use different business processes/configurations.
    if args.real_config and args.mock_database:
        parser.error("use separate endpoint processes for real reads and mock trade fixtures")
    create_server(args).run(transport="streamable-http", host="127.0.0.1", port=args.port,
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))


if __name__ == "__main__":
    main()
