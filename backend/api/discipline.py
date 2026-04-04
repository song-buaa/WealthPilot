"""
Discipline API 路由 — 投资纪律
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app import state as _state
from backend.services import discipline_service as svc

router = APIRouter()


def _pid() -> int:
    return _state.portfolio_id


# ── 请求体模型 ────────────────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    text: str


class UpdateRulesRequest(BaseModel):
    rules: dict


class SaveHandbookRequest(BaseModel):
    content: str


# ── 交易评估 ──────────────────────────────────────────────────────────────────

@router.post("/evaluate")
def evaluate_trade(req: EvaluateRequest):
    """
    自然语言交易意图 → 投资纪律评估。
    示例入参：{"text": "我想加仓理想汽车 2 万元"}
    """
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text 不能为空")
    return svc.evaluate_trade(req.text, _pid())


# ── 规则配置 ──────────────────────────────────────────────────────────────────

@router.get("/rules")
def get_rules():
    """获取当前生效的投资纪律规则配置"""
    return svc.get_rules_config()


@router.put("/rules")
def update_rules(req: UpdateRulesRequest):
    """更新规则配置（持久化到 data/rules_config.json）"""
    return svc.update_rules_config(req.rules)


@router.delete("/rules")
def reset_rules():
    """重置为内置默认规则（删除 data/rules_config.json）"""
    return svc.reset_rules()


# ── 手册管理 ──────────────────────────────────────────────────────────────────

@router.get("/handbook")
def get_handbook():
    """
    获取当前手册内容。
    返回 {"source": "custom"|"official", "content": "...markdown..."}
    """
    return svc.get_handbook()


@router.post("/handbook")
async def upload_handbook(file: UploadFile = File(...)):
    """
    上传定制手册（Markdown 文件）。
    Content-Type: multipart/form-data
    """
    data = await file.read()
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("gbk", errors="replace")
    svc.save_handbook(content)
    return svc.get_handbook()


@router.put("/handbook")
def save_handbook_json(req: SaveHandbookRequest):
    """
    直接以 JSON body 保存手册内容（前端编辑器场景）。
    """
    if not req.content.strip():
        raise HTTPException(status_code=422, detail="content 不能为空")
    svc.save_handbook(req.content)
    return svc.get_handbook()


@router.delete("/handbook")
def reset_handbook():
    """删除定制手册，恢复官方版本"""
    return svc.reset_handbook()
