def call_yingmi_tool(tool_name: str, arguments: dict) -> dict:
    """便捷入口。DEMO_ALLOW_MARKET_DATA=false 时短路。"""
    try:
        from backend.core.demo_mode import PUBLIC_DEMO_MODE, DEMO_ALLOW_MARKET_DATA
        if PUBLIC_DEMO_MODE and not DEMO_ALLOW_MARKET_DATA:
            return {"success": False, "error": "DEMO_ALLOW_MARKET_DATA=false: 盈米 MCP 已禁用"}
    except ImportError:
        pass
    from .yingmi_client import call_yingmi_tool as _real
    return _real(tool_name, arguments)


def get_yingmi_client():
    from .yingmi_client import get_yingmi_client as _real
    return _real()
