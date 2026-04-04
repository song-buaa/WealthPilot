"""
Tasks API 路由 — 异步任务接口骨架

当前仅定义接口结构，不实现任何逻辑。
供未来 OpenClaw 自动化工具调用。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any

router = APIRouter()


class TaskCreateRequest(BaseModel):
    task_type: str       # 任务类型（预留）
    payload: dict = {}   # 任务参数（预留）


@router.post("/create")
def create_task(req: TaskCreateRequest):
    """创建异步任务（骨架，待实现）"""
    return JSONResponse(
        status_code=501,
        content={"detail": "Task system not yet implemented"},
    )


@router.get("/{task_id}/status")
def get_task_status(task_id: str):
    """查询任务状态（骨架，待实现）"""
    return JSONResponse(
        status_code=501,
        content={"detail": "Task system not yet implemented"},
    )
