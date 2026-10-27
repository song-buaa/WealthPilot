"""Single-tool loopback MCP endpoint. Defaults to synthetic, not real wealth."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict


class Overview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["poc02.wealth-overview.v1"]
    synthetic: bool
    currency: Literal["CNY"]
    amount_unit: Literal["minor"]
    minor_unit_scale: Literal[2]
    total_assets_minor: int | None
    total_liabilities_minor: int | None
    net_worth_minor: int | None
    queried_at: str
    data_updated_date: str | None
    oldest_component_date: str | None
    status: Literal["ok", "partial", "unavailable"]
    issues: list[str]
    sources: dict[str, str]
    scope: str
    notice: str
    snapshot_id: str | None = None
    data_quality: dict | None = None


settings = None
mcp = MCPServer("WealthPilot PoC 02", version="0.1.0",
    instructions="Return synthetic flag and quality warnings with the figures. Amounts are CNY minor units. No trading capabilities.")


@mcp.tool(description="Read the existing WealthPilot core wealth overview: assets, liabilities, net worth, CNY and valuation-date quality. No holdings/account IDs/credentials/trading. Only CNY; no currency conversion. Respect synthetic and partial/unavailable status.",
          annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
          meta={"securitySchemes": [{"type": "noauth"}]})
def get_wealth_overview(currency: Literal["CNY"] = "CNY") -> Overview:
    if settings is None or not settings.real_config:
        result = dict(schema_version="poc02.wealth-overview.v1", synthetic=True,
            currency=currency, amount_unit="minor", minor_unit_scale=2,
            total_assets_minor=100000000, total_liabilities_minor=10000000,
            net_worth_minor=90000000, queried_at=datetime.now(timezone.utc).isoformat(),
            data_updated_date="2026-01-01", oldest_component_date="2026-01-01",
            status="partial", issues=["SYNTHETIC_FIXTURE"], sources={"fixture": "hardcoded"},
            scope="Synthetic access-verification fixture", notice="SYNTHETIC TEST DATA ONLY. No real database accessed.")
    else:
        worker = Path(__file__).with_name("worker.py")
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        try:
            completed = subprocess.run([settings.business_python, str(worker), "--config", settings.real_config],
                env=environment, capture_output=True, text=True, timeout=45, check=True)
            payload = json.loads(completed.stdout)
            if not payload.get("ok"):
                raise RuntimeError("WEALTH_READ_UNAVAILABLE")
            result = payload["result"]
        except (subprocess.SubprocessError, ValueError, KeyError) as error:
            raise RuntimeError("WEALTH_READ_UNAVAILABLE") from None
    validated = Overview.model_validate(result)
    if settings and settings.events:
        # Evidence records completion only, never private payloads or hashes of balances.
        with Path(settings.events).open("a") as output:
            output.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                "tool": "get_wealth_overview", "synthetic": validated.synthetic,
                "status": validated.status, "returned": True}) + "\n")
    return validated


def record_analysis_event(configuration, value):
    """Normal analysis stages; stderr/optional existing private JSONL sink only."""
    import logging
    from pocs.chatgpt_skills.diagnostics import safe_diagnostic
    clean = safe_diagnostic(value, value.get("analysis_run_id"))
    if clean is None:
        return
    event = {"time": datetime.now(timezone.utc).isoformat(), "event": "mcp_analysis", **clean}
    line = json.dumps(event, ensure_ascii=True)
    # Never use stdout: it belongs to MCP/IPC. No exception strings or payloads.
    logging.getLogger("wealthpilot.mcp.analysis").warning(line)
    path = getattr(configuration, "events", None)
    if path:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                import stat
                mode = os.fstat(fd).st_mode
                if stat.S_ISREG(mode) and not mode & 0o077:
                    os.write(fd, (line + "\n").encode())
            finally:
                os.close(fd)
        except OSError:
            logging.getLogger("wealthpilot.mcp.analysis").warning("MCP_ANALYSIS_EVENT_SINK_UNAVAILABLE")


def validate_real_access(settings, parser):
    if settings.real_config:
        if not settings.access_evidence:
            parser.error("real mode requires reviewed private-access evidence")
        proof = json.loads(Path(settings.access_evidence).read_text())
        required = ("access_scope_confirmed", "synthetic_web_pass", "anonymous_denied", "no_public_listener")
        if any(proof.get(key) is not True for key in required):
            parser.error("private access checks have not passed")
        # This is an operator activation guard, not authentication. Secure Tunnel
        # associations and control-plane authorization supply the access boundary.
        for file in (settings.real_config, settings.access_evidence):
            if Path(file).stat().st_mode & 0o077:
                parser.error("private configuration and evidence must have mode 0600")
            if Path(file).resolve().is_relative_to(Path(__file__).resolve().parents[2]):
                parser.error("private configuration must be outside the repository")


def main():
    global settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--real-config")
    parser.add_argument("--access-evidence")
    parser.add_argument("--business-python", default="/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python")
    parser.add_argument("--events", help="Metadata-only completion ledger, outside Git.")
    settings = parser.parse_args()
    validate_real_access(settings, parser)
    mcp.run(transport="streamable-http", host="127.0.0.1", port=settings.port,
        json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]))


if __name__ == "__main__":
    main()
