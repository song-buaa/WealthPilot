"""
M7 Spike：盈米 MCP 连通性测试

确认 4 件事：
  1. MCP 客户端能连上盈米
  2. list_tools 能拿到工具清单
  3. 6 个目标工具的 schema 详情
  4. 实际调用 BatchGetFundsDetail 能拿到数据
"""
import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

YINGMI_URL = "https://stargate.yingmi.com/mcp/v2"
YINGMI_API_KEY = os.environ.get("YINGMI_API_KEY", "8TiRdtPwvewqeP_ckn5KsQ")

TARGET_TOOLS = [
    "BatchGetFundsDetail",
    "BatchGetFundNavHistory",
    "GetBatchFundPerformance",
    "GetFundDiagnosis",
    "SearchFinancialNews",
    "GetLatestQuotations",
]


async def main():
    headers = {
        "x-api-key": YINGMI_API_KEY,
        "Accept": "application/json, text/event-stream",
    }

    print("=" * 60)
    print("Step 1: 连接到盈米 MCP")
    print("=" * 60)

    async with streamablehttp_client(YINGMI_URL, headers=headers) as (
        read_stream, write_stream, _
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("✅ 连接成功，session initialized")

            print("\n" + "=" * 60)
            print("Step 2: list_tools")
            print("=" * 60)
            tools_result = await session.list_tools()
            all_tools = tools_result.tools
            print(f"✅ 拿到 {len(all_tools)} 个工具")

            print("\n" + "=" * 60)
            print("Step 3: 6 个目标工具的 schema")
            print("=" * 60)
            target_tools_found = {}
            for tool in all_tools:
                if tool.name in TARGET_TOOLS:
                    target_tools_found[tool.name] = tool

            for name in TARGET_TOOLS:
                if name in target_tools_found:
                    tool = target_tools_found[name]
                    print(f"\n--- {name} ---")
                    print(f"description: {tool.description[:200]}")
                    print(f"input_schema: {json.dumps(tool.inputSchema, ensure_ascii=False, indent=2)[:500]}")
                else:
                    print(f"\n❌ {name} 未在工具列表中找到")

            print("\n" + "=" * 60)
            print("Step 4: 实际调用 BatchGetFundsDetail")
            print("=" * 60)
            try:
                result = await session.call_tool(
                    "BatchGetFundsDetail",
                    arguments={"fundCodes": ["000001"]}
                )
                print(f"✅ 调用成功")
                print(f"返回类型: {type(result)}")
                if hasattr(result, 'content') and result.content:
                    for content_item in result.content:
                        if hasattr(content_item, 'text'):
                            print(f"返回内容: {content_item.text[:1000]}")
                        else:
                            print(f"content_item: {content_item}")
            except Exception as e:
                print(f"❌ 调用失败: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
