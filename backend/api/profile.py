"""
Profile API 路由 — 用户画像与投资目标
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models import UserProfile, get_session
from backend.services import profile_service as svc

router = APIRouter()


# ── 请求体模型 ────────────────────────────────────────────────────────────────

class ProfileUpdateRequest(BaseModel):
    risk_source:           Optional[str] = None
    risk_provider:         Optional[str] = None
    risk_original_level:   Optional[str] = None
    risk_normalized_level: Optional[int] = None
    risk_type:             Optional[str] = None
    risk_assessed_at:      Optional[str] = None   # ISO 字符串
    income_level:          Optional[str] = None
    income_stability:      Optional[str] = None
    total_assets:          Optional[str] = None
    investable_ratio:      Optional[str] = None
    liability_level:       Optional[str] = None
    family_status:         Optional[str] = None
    asset_structure:       Optional[str] = None
    investment_motivation: Optional[str] = None
    fund_usage_timeline:   Optional[str] = None
    goal_type:             Optional[list[str]] = None
    target_return:         Optional[str] = None
    max_drawdown:          Optional[str] = None
    investment_horizon:    Optional[str] = None
    ai_summary:            Optional[str] = None
    ai_style:              Optional[str] = None
    ai_confidence:         Optional[str] = None


class ExtractRequest(BaseModel):
    type:            str       = "text"   # "text" | "images"
    text:            str       = ""
    images:          list[str] = []       # base64 编码的图片列表
    existing_fields: dict      = {}


class ConflictRequest(BaseModel):
    max_drawdown:        str
    target_return:       str
    fund_usage_timeline: str


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.get("")
def get_profile():
    """获取当前画像（不存在返回空结构，不报 404）"""
    profile = svc.get_profile()
    if profile is None:
        return {}
    return profile


@router.put("")
def update_profile(req: ProfileUpdateRequest):
    """保存/更新画像（upsert，始终只有一条记录）"""
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    return svc.upsert_profile(data)


@router.post("/extract")
def extract_profile(req: ExtractRequest):
    """AI 槽位提取（支持文本和图片两种模式）"""
    if req.type == "images":
        if not req.images:
            raise HTTPException(status_code=422, detail="images 不能为空")
        return svc.extract_profile_from_images(req.images, req.existing_fields)
    else:
        if not req.text.strip():
            raise HTTPException(status_code=422, detail="text 不能为空")
        return svc.extract_profile_from_text(req.text, req.existing_fields)


@router.post("/generate")
def generate_profile():
    """生成 AI 画像总结，并持久化 ai_summary / ai_style / ai_confidence"""
    session = get_session()
    try:
        profile = session.query(UserProfile).first()
        if profile is None:
            raise HTTPException(status_code=404, detail="未找到用户画像，请先保存基础信息")
        result = svc.generate_ai_profile(profile)
        profile.ai_summary    = result["summary"]
        profile.ai_style      = result["style"]
        profile.ai_confidence = result["confidence"]
        session.commit()
        return result
    finally:
        session.close()


@router.post("/conflicts")
def check_conflicts(req: ConflictRequest):
    """冲突检测（body: {max_drawdown, target_return, fund_usage_timeline}）"""
    conflicts = svc.check_conflicts(req.max_drawdown, req.target_return, req.fund_usage_timeline)
    return {"conflicts": conflicts}


@router.get("/risk-expired")
def risk_expired():
    """检查风险评估是否过期（超过12个月返回 true）"""
    return {"expired": svc.is_risk_expired()}
