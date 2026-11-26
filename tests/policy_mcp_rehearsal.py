"""Keep a real read-only MCP client alive during the isolated publish rehearsal."""
import asyncio
from contextlib import nullcontext
import json
import os
import sys
from types import SimpleNamespace

from mcp import Client
from pocs.chatgpt_skills.server import create_server
from backend.services.investment_policy.config_probe import candidate_session
from backend.services.investment_policy.runtime import bind


async def main():
    settings = SimpleNamespace(real_config="unused-rehearsal-config", mock_database=None, portfolio_id=7,
        business_python=os.environ["WP_POLICY_REHEARSAL_BUSINESS_PYTHON"], web_base_url="")
    async with Client(create_server(settings), mode="legacy") as client:
        for line in sys.stdin:
            candidate = json.loads(line)
            projection = bind(candidate_session(7, "portfolio:7", candidate)) if candidate else nullcontext()
            with projection:
                result = await client.call_tool("get_investment_policy_context", {"scope":"portfolio"})
            assert not result.is_error
            value = result.structured_content or json.loads(result.content[0].text)
            print(json.dumps({"policy_reference":value["policy_reference"], "pid":os.getpid()}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
