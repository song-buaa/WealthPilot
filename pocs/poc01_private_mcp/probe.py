"""Discover and call the synthetic tool using the official MCP HTTP client."""

import argparse
import asyncio
import json

from mcp import Client


async def probe(url: str) -> dict:
    # Use initialize rather than relying only on the newer discovery handshake.
    async with Client(url, mode="legacy", read_timeout_seconds=10) as client:
        listed = await client.list_tools()
        if [tool.name for tool in listed.tools] != ["get_synthetic_wealth_summary"]:
            raise RuntimeError("Unexpected tool inventory")
        result = await client.call_tool("get_synthetic_wealth_summary", {"currency": "CNY"})
        if result.is_error or not result.structured_content:
            raise RuntimeError("Synthetic summary call failed")
        if result.structured_content.get("synthetic") is not True:
            raise RuntimeError("Result is not marked synthetic")
        return {
            "status": "PASS",
            "tools": [tool.model_dump(by_alias=True, exclude_none=True) for tool in listed.tools],
            "result": result.model_dump(by_alias=True, exclude_none=True),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787/mcp")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
