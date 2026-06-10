def call_yingmi_tool(tool_name: str, arguments: dict) -> dict:
    """便捷入口。PUBLIC_DEMO_MODE 下短路，不加载 mcp 依赖。"""
    try:
        from backend.core.demo_mode import PUBLIC_DEMO_MODE
        if PUBLIC_DEMO_MODE:
            return {"success": False, "error": "PUBLIC_DEMO_MODE: 盈米 MCP 已禁用"}
    except ImportError:
        pass
    from .yingmi_client import call_yingmi_tool as _real
    return _real(tool_name, arguments)


def get_yingmi_client():
    from .yingmi_client import get_yingmi_client as _real
    return _real()


__all__ = ["call_yingmi_tool", "get_yingmi_client"]
