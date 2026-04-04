"""
Decision API 路由 — 投资决策（含 SSE 流式接口）
"""

import traceback
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import state as _state
from backend.services import decision_service as svc

router = APIRouter()


def _pid() -> int:
    return _state.portfolio_id


# ── 请求体模型 ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str
    portfolio_id: Optional[int] = None  # 不传则用默认 portfolio


# ── SSE 对话接口 ──────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(req: ChatRequest):
    """
    投资决策核心接口，返回 SSE 流式响应。

    响应 Content-Type: text/event-stream
    每条事件格式：event: <type>\\ndata: <json>\\n\\n

    Event 类型：
      intent  — 意图识别结果
      stage   — 管道阶段进度
      text    — AI 回答文字片段（前端累积拼接）
      done    — 流式结束，含 decision_id
      error   — 出错时返回

    前端使用方式：
      const es = new EventSource('/api/decision/chat', {method: 'POST', ...})
      es.addEventListener('text', e => appendText(JSON.parse(e.data).delta))
      es.addEventListener('done', e => setDecisionId(JSON.parse(e.data).decision_id))
    """
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="message 不能为空")
    if not req.session_id.strip():
        raise HTTPException(status_code=422, detail="session_id 不能为空")

    pid = req.portfolio_id if req.portfolio_id is not None else _pid()

    async def event_stream():
        async for chunk in svc.run_chat_stream(req.message, req.session_id, pid):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Explain Panel 数据 ────────────────────────────────────────────────────────

@router.get("/explain/{decision_id}")
def get_explain(decision_id: str, session_id: str):
    """
    获取某次决策的完整分析链路数据，供 Explain Panel 渲染。

    Query 参数：session_id — 与发起对话时一致

    返回结构包含：
      intent, data, pre_check, rules, signals, llm（各阶段详情）
    """
    try:
        result = svc.get_decision_explain(session_id, decision_id)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[get_explain ERROR] decision_id={decision_id}\n{tb}")
        raise HTTPException(status_code=500, detail=f"序列化失败: {e}")
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"decision {decision_id} not found in session {session_id}"
        )
    return result


# ── 会话管理 ──────────────────────────────────────────────────────────────────

@router.delete("/session/{session_id}")
def clear_session(session_id: str):
    """
    清除服务端会话（前端点击「清空对话」时调用）。
    清除 intent_engine 的多轮上下文和 decision_map 缓存。
    """
    svc.clear_session(session_id)
    return {"message": f"session {session_id} cleared"}
