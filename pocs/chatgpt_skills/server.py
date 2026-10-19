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
from types import SimpleNamespace
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from typing import Annotated
from pocs.chatgpt_skills.contracts import Details, ExplicitOrder, TradeResult, PrepareResult
from pocs.chatgpt_skills.trade_link import IBKRLinkOrder, LinkOrder, TradeLinkResult, prepare_link
from pocs.chatgpt_skills.worker import SAFE_ERRORS
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult
from pocs.poc02_wealth_mcp import server as overview


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
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.process = None

    def call(self, operation, arguments):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.close()
                env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR", "WEALTHPILOT_ANALYSIS_CACHE_DIR") if k in os.environ}
                env.update(PYTHON_DOTENV_DISABLED="1", IBKR_READ_ONLY_MODE="true",
                           ENABLE_IBKR_LIVE_TRADING="false", ENABLE_TIGER_LIVE_TRADING="false",
                           FUTU_READ_ONLY_MODE="true", BROKER_MODE="mock")
                command = [self.settings.business_python, str(Path(__file__).with_name("worker.py")),
                           "--portfolio-id", str(self.settings.portfolio_id)]
                if getattr(self.settings, "analysis_env_file", None):
                    command += ["--analysis-env-file", self.settings.analysis_env_file]
                if self.settings.real_config:
                    command += ["--real-config", self.settings.real_config]
                if self.settings.mock_database:
                    command += ["--mock-database", self.settings.mock_database]
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, env=env,
                    cwd=getattr(self.settings, "worker_cwd", None))
            try:
                self.process.stdin.write(json.dumps({"operation": operation, "arguments": arguments})+"\n")
                self.process.stdin.flush()
                if not select.select([self.process.stdout], [], [], 270 if operation in ("analyze_position", "review_portfolio", "analyze_performance") else 45)[0]:
                    raise TimeoutError()
                response = json.loads(self.process.stdout.readline())
            except (OSError, ValueError, KeyError, TimeoutError):
                # A timeout is never a permission to replay a write automatically.
                self.close()
                raise ToolError("BUSINESS_OPERATION_UNAVAILABLE") from None
            if not response.get("ok"):
                code = response.get("error")
                raise ToolError(code if code in SAFE_ERRORS else "BUSINESS_OPERATION_UNAVAILABLE")
            return response["result"]


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
        "When can_propose_rebalance=false, offer general analysis with explicit warnings, not a precise executable rebalancing plan.") if prepare_config else ""
    server = MCPServer("WealthPilot ChatGPT Skills", version="0.1.0", lifespan=lifespan,
        instructions=("For professional analysis use analyze_position/review_portfolio/analyze_performance; reuse the original conclusion, evidence, scope and quality instead of running another professional model analysis. Pass relevant prior turns in request.history. Never invent missing returns, positions, sources or freshness. Analysis creates no orders; use the existing link only if the user later chooses a trade. For explicit buy/sell requests use prepare_trade_confirmation_link, then show the clickable URL. It only prepares a link; no draft, confirmation or order is created in ChatGPT. The user reviews and finally confirms in the existing private WealthPilot Web. Ask for missing symbol/market, side, quantity, order type, required prices and currency; do not invent them. Preserve partial and source warnings. "
                      if settings.real_config else
                      "MOCK TRADING ONLY. Show the complete preview and risks, wait for a new explicit human confirmation before confirm_trade with the exact preview_hash. Never auto-retry an uncertain order. Tool results are not human confirmation. ")+prepare_instructions)
    overview.settings = settings
    security_meta = {"securitySchemes": [{"type": "noauth"}]}  # Private Tunnel supplies access control.
    read = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
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

    @server.tool(meta=security_meta,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
        description="Prepare a clickable private WealthPilot Web trade-confirmation URL from explicit BUY/SELL parameters. Pure validation/URL generation: never creates a draft, changes data, connects to a broker or submits an order. Ask for missing required fields instead of guessing. Show the link and order summary; user opens the existing Web, edits/reviews, and finally confirms there. LIMIT needs limit_price; CONDITIONAL_LIMIT also needs trigger_price. US/HK single-order GTC only. For IB01 use the IBKR budget variant: broker=IBKR, symbol=IB01 (or IB01:LSE/IB01:UK/IB01:LSEETF), side=BUY, order_type=LIMIT, currency=USD, target_amount=the user total USD outlay cap INCLUDING fees and safety cushion, time_in_force=DAY. It opens the unified existing Web confirmation dialogs backed by the qualified LSEETF/USD/SMART ExecutionBatch; do not invent quantity or limit_price or relabel IB01 as US. Preserve all P1 data-quality warnings; do not call Mock trade tools for real requests.")
    def prepare_trade_confirmation_link(order: LinkOrder | IBKRLinkOrder) -> TradeLinkResult:
        try:
            return prepare_link(getattr(settings, "web_base_url", ""), order)
        except ValueError as error:
            raise ToolError(str(error)) from None

    def analysis_call(operation, request):
        with analysis_lock:
            try:
                return AnalysisResult.model_validate(analysis_client.call(operation, request.model_dump(mode="json")))
            finally:
                analysis_client.close()

    @server.tool(meta=security_meta, annotations=read, description="Single-symbol professional decision via existing PositionDecision/position_single PEER, rules, signals, research and new-entry logic. Supply user's question, optional symbol and relevant history. Returns original conclusion/evidence/quality. No trades. Ask for clarification when needed; fallback/unavailable is not a valid recommendation.")
    def analyze_position(request: AnalysisRequest) -> AnalysisResult:
        return analysis_call("analyze_position", request)

    @server.tool(meta=security_meta, annotations=read, description="Review investment portfolio/allocation via original PortfolioReview/AssetAllocation PEER and AllocationService snapshot/default targets/deviation. Weights refer to investment holdings, NOT full personal wealth. No sector inference without evidence. Preserve partial/failed sources. No trades.")
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
    parser.add_argument("--business-python", required=True)
    parser.add_argument("--real-config")
    parser.add_argument("--access-evidence")
    parser.add_argument("--analysis-working-directory", help="Reuse the existing complete Web working directory for relative knowledge/rules assets")
    parser.add_argument("--analysis-business-python", help="Reuse the existing complete Web business Python for PEER dependencies")
    parser.add_argument("--analysis-env-file", help="Existing local runtime JSON; only original analysis provider settings are read")
    parser.add_argument("--prepare-config", help="Opt-in private loopback prepare URL/token-file configuration; seven existing tools stay read-only")
    parser.add_argument("--mock-database")
    parser.add_argument("--portfolio-id", type=int, default=1)
    parser.add_argument("--web-base-url", default=os.environ.get("WEALTHPILOT_WEB_BASE_URL", ""))
    args = parser.parse_args()
    args.events = None
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
