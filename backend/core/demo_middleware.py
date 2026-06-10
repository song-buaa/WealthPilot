"""
PUBLIC_DEMO_MODE 中间件：

1. 访问密码门：所有 API 请求需带 X-Demo-Password 或 cookie
2. 交易操作拦截：confirm / place_order / 策略管理 → 403
3. 展示能力放行：generate / persist-draft / adjust → 放行（访客可完整体验
   "AI 建议 → 规则引擎分批 → 对话调整"）

拦截边界：confirm 是"看 vs 动"的分界线。
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

# POST 方法下直接 403 的路由前缀（交易操作）
_BLOCKED_PREFIXES = [
    "/api/action/strategies/",  # place_order, pause, resume, discard
    "/api/action/orders/",      # cancel_order
]


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

        # ── 路由拦截：交易操作 ──
        method = request.method.upper()

        # action drafts: POST(创建) 拦截，GET 放行
        if path == "/api/action/drafts" and method == "POST":
            return JSONResponse(
                {"error": "PUBLIC_DEMO_MODE: 演示模式下不可创建行动草稿"},
                status_code=403,
            )

        # action 子路由：place_order / cancel / discard / pause / resume
        for prefix in _BLOCKED_PREFIXES:
            if path.startswith(prefix) and method == "POST":
                return JSONResponse(
                    {"error": "PUBLIC_DEMO_MODE: 演示模式下不可执行交易操作"},
                    status_code=403,
                )

        # execution-plan: 只拦 confirm（"看vs动"分界线）
        # generate / persist-draft / adjust 放行（纯计算 + 草案展示）
        if (path.startswith("/api/execution-plan/")
                and path.endswith("/confirm")
                and method == "POST"):
            return JSONResponse(
                {"error": "PUBLIC_DEMO_MODE: 演示模式下可生成和调整计划草案，但不可确认下单。"},
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
