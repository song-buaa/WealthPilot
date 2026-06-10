"""Demo API — 密码验证端点。"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.core.demo_mode import DEMO_ACCESS_PASSWORD, PUBLIC_DEMO_MODE

router = APIRouter()


class PasswordRequest(BaseModel):
    password: str


@router.post("/verify-password")
def verify_password(req: PasswordRequest):
    """验证 demo 访问密码。"""
    if not PUBLIC_DEMO_MODE:
        return {"valid": True, "demo_mode": False}

    if not DEMO_ACCESS_PASSWORD:
        return {"valid": True, "no_password_required": True}

    if req.password == DEMO_ACCESS_PASSWORD:
        response = JSONResponse({"valid": True})
        response.set_cookie("demo_password", req.password, httponly=True, samesite="lax")
        return response

    return JSONResponse({"valid": False, "error": "密码错误"}, status_code=401)


@router.get("/status")
def demo_status():
    """返回 demo 模式状态。"""
    return {
        "public_demo_mode": PUBLIC_DEMO_MODE,
        "password_required": bool(DEMO_ACCESS_PASSWORD),
    }
