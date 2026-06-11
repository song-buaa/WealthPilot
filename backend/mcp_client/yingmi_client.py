"""
盈米 MCP 客户端（v2.6 M7）

封装与盈米 MCP Server 的连接 + 工具调用，
对外暴露同步友好的 call_yingmi_tool() 接口。

设计原则：
  - 单例 connection（避免每次调用都重连）
  - 同步包装异步调用（方便 Tool Layer 复用）
  - 失败降级（盈米限额或网络问题时返回 error，不阻塞主流程）
"""
import asyncio
import json
import os
import threading
from typing import Any, Optional

# mcp 依赖延迟加载（mini_racer V8 引擎在某些环境崩溃）
# PUBLIC_DEMO_MODE 下通过 __init__.py 短路，此文件不会被 import
ClientSession = None
streamablehttp_client = None

def _ensure_mcp():
    global ClientSession, streamablehttp_client
    if ClientSession is None:
        from mcp import ClientSession as _CS
        from mcp.client.streamable_http import streamablehttp_client as _SHC
        ClientSession = _CS
        streamablehttp_client = _SHC

YINGMI_URL = os.environ.get("YINGMI_MCP_URL")
YINGMI_API_KEY = os.environ.get("YINGMI_API_KEY")


class YingmiMCPClient:
    """
    盈米 MCP 客户端封装。

    使用方式：
        client = YingmiMCPClient()
        result = client.call_tool("BatchGetFundsDetail", {"fundCodes": ["000001"]})
    """

    def __init__(self):
        self._lock = threading.Lock()

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        同步调用盈米 MCP 工具。

        Returns:
            dict 形如 {"success": True, "data": ..., "raw_text": "..."} 或
                    {"success": False, "error": "..."}
        """
        try:
            return asyncio.run(self._call_tool_async(tool_name, arguments))
        except RuntimeError:
            # 已经在 event loop 里（比如 FastAPI async context）
            # 用 thread + new loop
            result_box = {}
            def runner():
                loop = asyncio.new_event_loop()
                try:
                    result_box["data"] = loop.run_until_complete(
                        self._call_tool_async(tool_name, arguments)
                    )
                finally:
                    loop.close()
            t = threading.Thread(target=runner)
            t.start()
            t.join(timeout=60)
            return result_box.get("data", {
                "success": False,
                "error": "调用超时或线程异常",
            })
        except Exception as e:
            return {"success": False, "error": f"调用异常: {e}"}

    async def _call_tool_async(self, tool_name: str, arguments: dict) -> dict:
        if not YINGMI_API_KEY or not YINGMI_URL:
            return {
                "success": False,
                "error": "盈米 API Key 或 URL 未配置（请检查 .env 里的 YINGMI_API_KEY 和 YINGMI_MCP_URL）",
            }
        _ensure_mcp()
        headers = {
            "x-api-key": YINGMI_API_KEY,
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with streamablehttp_client(YINGMI_URL, headers=headers) as (
                read_stream, write_stream, _
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)

                    # 提取返回内容
                    raw_text = ""
                    parsed_data = None
                    if hasattr(result, 'content') and result.content:
                        for item in result.content:
                            if hasattr(item, 'text'):
                                raw_text += item.text

                        # 尝试解析为 JSON（盈米通常返回 JSON 格式文本）
                        try:
                            parsed_data = json.loads(raw_text)
                        except (json.JSONDecodeError, ValueError):
                            parsed_data = None

                    return {
                        "success": True,
                        "data": parsed_data,
                        "raw_text": raw_text,
                    }
        except Exception as e:
            return {
                "success": False,
                "error": f"盈米 MCP 调用失败: {e}",
            }


# 全局单例
_client: Optional[YingmiMCPClient] = None
_client_lock = threading.Lock()


def get_yingmi_client() -> YingmiMCPClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = YingmiMCPClient()
    return _client


def call_yingmi_tool(tool_name: str, arguments: dict) -> dict:
    """便捷函数，外部调用入口。开关控制在 __init__.py 层。"""
    return get_yingmi_client().call_tool(tool_name, arguments)
