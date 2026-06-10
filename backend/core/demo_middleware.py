"""
PUBLIC_DEMO_MODE 中间件：

1. 访问密码门：所有 API 请求需带 X-Demo-Password 或 cookie
2. action/execution-plan 路由拦截：直接 403
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.core.demo_mode import PUBLIC_DEMO_MODE, DEMO_ACCESS_PASSWORD

# 不需要密码的白名单路径
_PUBLIC_PATHS = {
    "/api/demo/verify-password",
    "/api/demo/status",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# PUBLIC_DEMO_MODE 下直接 403 的路由前缀
_BLOCKED_PREFIXES = [
    "/api/action/strategies/",  # place_order, pause, resume, discard
    "/api/action/orders/",      # cancel_order
    "/api/execution-plan/",     # adjust, persist-draft, confirm
]
_BLOCKED_EXACT = {
    "/api/action/drafts",       # POST = create draft (但 GET 可以)
}


class DemoMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not PUBLIC_DEMO_MODE:
            return await call_next(request)

        path = request.url.path

        # 静态资源和前端路由直接放行
        if not path.startswith("/api"):
            return await call_next(request)

        # 白名单路径放行
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # ── 路由拦截：action/execution-plan 写操作 ──
        method = request.method.upper()

        # action drafts: POST(创建) 和 DELETE(丢弃) 拦截，GET 放行
        if path == "/api/action/drafts" and method == "POST":
            return JSONResponse(
                {"error": "PUBLIC_DEMO_MODE: 演示模式下不可创建行动草稿"},
                status_code=403,
            )

        # action 子路由：confirm/place/cancel/discard/pause/resume
        for prefix in _BLOCKED_PREFIXES:
            if path.startswith(prefix) and method == "POST":
                return JSONResponse(
                    {"error": "PUBLIC_DEMO_MODE: 演示模式下不可执行交易操作"},
                    status_code=403,
                )

        # ── 密码门 ──
        if DEMO_ACCESS_PASSWORD:
            pwd = (
                request.headers.get("x-demo-password")
                or request.cookies.get("demo_password")
                or request.query_params.get("demo_password")
            )
            if pwd != DEMO_ACCESS_PASSWORD:
                return JSONResponse(
                    {"error": "需要访问密码", "code": "demo_password_required"},
                    status_code=401,
                )

        return await call_next(request)
