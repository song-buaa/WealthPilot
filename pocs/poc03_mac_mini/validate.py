"""Local MCP smoke; emit only contract/quality/comparison flags, never balances."""
import argparse
import asyncio
import json
from pathlib import Path

from mcp import Client
import jsonschema
from common import private_json, require, safe_cli, write_json


async def validate(port, expectation, reference=None, runtime_data=False):
    require(not (runtime_data and reference is not None), "RUNTIME_AND_MIGRATION_COMPARISON_EXCLUSIVE")
    require(expectation != "real" or reference is not None or runtime_data, "MIGRATION_REFERENCE_REQUIRED")
    async with Client(f"http://127.0.0.1:{port}/mcp", mode="legacy", read_timeout_seconds=60) as client:
        tools = await client.list_tools()
        require([t.name for t in tools.tools] == ["get_wealth_overview"], "UNEXPECTED_MCP_TOOL_CATALOG")
        require(tools.tools[0].annotations.read_only_hint is True, "READONLY_ANNOTATION_MISSING")
        result = await client.call_tool("get_wealth_overview", {"currency": "CNY"})
        require(not result.is_error, "MCP_READ_FAILED")
        data = result.structured_content
        jsonschema.validate(data, tools.tools[0].output_schema)
        require(data["synthetic"] is (expectation == "synthetic"), "DATA_MODE_MISMATCH")
        require(data["status"] != "unavailable", "WEALTH_SOURCE_UNAVAILABLE")
        require(all(data[k] is not None for k in ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")), "WEALTH_AMOUNT_MISSING")
        checks = None
        if reference is not None:
            checks = {k: data[k] == reference["result"][k] for k in ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")}
        summary = {"schema": "poc03.mcp-validation.v1", "result": "PASS" if checks is None or all(checks.values()) else "FAIL",
                   "one_readonly_tool": True, "contract_valid": True, "synthetic": data["synthetic"],
                   "status": data["status"], "issues": data["issues"], "amounts_equal_to_migration_reference": checks,
                   "comparison_basis": "runtime_contract_only" if runtime_data else "migration" if reference is not None else "synthetic",
                   "comparison_note": "Live cash/FX may change after migration; a difference needs review, not an automatic PASS."}
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--expect", choices=("synthetic", "real"), required=True)
    parser.add_argument("--reference")
    parser.add_argument("--runtime-data", action="store_true", help="Check live contract/quality; do not compare changing amounts to historical migration")
    parser.add_argument("--output", required=True, help="Private metadata-only validation record")
    args = parser.parse_args()
    reference = private_json(args.reference) if args.reference else None
    summary = asyncio.run(validate(args.port, args.expect, reference, args.runtime_data))
    write_json(args.output, summary)
    print(json.dumps(summary))
    if summary["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    safe_cli(main)
