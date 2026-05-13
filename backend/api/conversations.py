"""
Conversations API — 会话 CRUD
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.utils.datetime_utils import utc_iso as _utc_iso

router = APIRouter()


# ── 请求/响应模型 ──────────────────────────────────────────────────────────

class CreateConversationRequest(BaseModel):
    portfolio_id: Optional[int] = None


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None


# ── 端点 ────────────────────────────────────────────────────────────────────

@router.get("")
def list_conversations():
    """返回所有 active 会话，按 updated_at 倒序。"""
    from app.database import get_session as get_db_session
    from app.models import Conversation

    db = get_db_session()
    try:
        rows = (
            db.query(Conversation)
            .filter(Conversation.status == "active")
            .order_by(Conversation.updated_at.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "title": r.title,
                "portfolio_id": r.portfolio_id,
                "status": r.status,
                "created_at": _utc_iso(r.created_at),
                "updated_at": _utc_iso(r.updated_at),
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("")
def create_conversation(req: CreateConversationRequest):
    """创建新会话。"""
    from app.database import get_session as get_db_session
    from app.models import Conversation

    db = get_db_session()
    try:
        conv = Conversation(
            id=str(uuid4()),
            portfolio_id=req.portfolio_id,
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return {
            "id": conv.id,
            "title": conv.title,
            "portfolio_id": conv.portfolio_id,
            "status": conv.status,
            "created_at": _utc_iso(conv.created_at),
            "updated_at": _utc_iso(conv.updated_at),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.patch("/{conversation_id}")
def update_conversation(conversation_id: str, req: UpdateConversationRequest):
    """更新会话标题或状态。"""
    from app.database import get_session as get_db_session
    from app.models import Conversation

    db = get_db_session()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        if req.title is not None:
            conv.title = req.title
        if req.status is not None:
            if req.status not in ("active", "archived"):
                raise HTTPException(status_code=422, detail="status must be 'active' or 'archived'")
            conv.status = req.status
        conv.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(conv)
        return {
            "id": conv.id,
            "title": conv.title,
            "portfolio_id": conv.portfolio_id,
            "status": conv.status,
            "created_at": _utc_iso(conv.created_at),
            "updated_at": _utc_iso(conv.updated_at),
        }
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str):
    """软删除：status → archived。"""
    from app.database import get_session as get_db_session
    from app.models import Conversation

    db = get_db_session()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        conv.status = "archived"
        conv.updated_at = datetime.utcnow()
        db.commit()
        return {"message": f"conversation {conversation_id} archived"}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str):
    """返回该会话所有消息，按 created_at 升序。"""
    from app.database import get_session as get_db_session
    from app.models import ConversationMessage

    db = get_db_session()
    try:
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )
        return [
            {
                "id": r.id,
                "role": r.role,
                "content": r.content,
                "intent": r.intent,
                "asset": r.asset,
                "created_at": _utc_iso(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()
