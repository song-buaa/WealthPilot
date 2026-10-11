"""Standalone synthetic-only MCP server. No WealthPilot runtime imports."""

import argparse
import re
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field


class AssetBucket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: Literal["cash", "fixed_income", "equity", "other"]
    amount_minor: int = Field(ge=0)


class WealthSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["poc01.wealth-summary.v1"]
    synthetic: Literal[True]
    source: Literal["hardcoded_fixture"]
    as_of: Literal["2026-01-01T00:00:00Z"]
    currency: Literal["CNY"]
    amount_unit: Literal["minor"]
    minor_unit_scale: Literal[2]
    total_assets_minor: int = Field(ge=0)
    total_liabilities_minor: int = Field(ge=0)
    net_worth_minor: int
    assets: list[AssetBucket]
    notice: str


mcp = MCPServer(
    "WealthPilot PoC 01 (synthetic only)",
    version="0.1.0",
    instructions="Only synthetic test data is available. Never describe it as the user's wealth.",
)


@mcp.tool(
    title="Synthetic wealth summary",
    description=(
        "Return a fixed synthetic wealth summary for MCP connectivity testing. "
        "No real accounts, database, market data, or trading. Only CNY is supported. "
        "Amounts are integer minor units (100 = CNY 1.00); this is not investment advice."
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    meta={"securitySchemes": [{"type": "noauth"}]},
)
def get_synthetic_wealth_summary(
    currency: Annotated[Literal["CNY"], Field(description="Fixed fixture currency; no FX conversion.")] = "CNY",
) -> WealthSummary:
    return WealthSummary(
        schema_version="poc01.wealth-summary.v1",
        synthetic=True,
        source="hardcoded_fixture",
        as_of="2026-01-01T00:00:00Z",
        currency=currency,
        amount_unit="minor",
        minor_unit_scale=2,
        total_assets_minor=100_000_000,
        total_liabilities_minor=10_000_000,
        net_worth_minor=90_000_000,
        assets=[
            AssetBucket(category="cash", amount_minor=20_000_000),
            AssetBucket(category="fixed_income", amount_minor=40_000_000),
            AssetBucket(category="equity", amount_minor=30_000_000),
            AssetBucket(category="other", amount_minor=10_000_000),
        ],
        notice="SYNTHETIC TEST DATA ONLY — not the user's financial information.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--allowed-host", action="append", default=[],
        help="Exact HTTPS forwarding hostname, if needed; no wildcard, scheme, or path.",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    for host in args.allowed_host:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            parser.error("allowed-host must be an exact lowercase hostname")
    mcp.run(
        transport="streamable-http", host="127.0.0.1", port=args.port,
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", *args.allowed_host],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*",
                             *(f"https://{host}" for host in args.allowed_host)],
        ),
    )


if __name__ == "__main__":
    main()
