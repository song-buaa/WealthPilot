"""Optional WealthPilot 4.3 E1 read projection; never executes analysis or writes."""
import sqlite3
from pydantic import BaseModel, ConfigDict, Field
from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError

from backend.services.investment_thesis.store import ThesisStore


class ThesisReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "wealthpilot.4.3.e1.read.v1"
    items: list[dict] = Field(default_factory=list)
    execution_authorized: bool = False
    scope: str = "Private historical research records; not current holdings or trading qualification"


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
