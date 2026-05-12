"""
v3.5 一次性脚本：为 title IS NULL 且有消息的会话补生成 LLM 标题。

用法：
    python backend/scripts/v3_5/backfill_titles.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def main():
    from app.database import get_session
    from app.models import Conversation, ConversationMessage
    from backend.services.decision_service import generate_conversation_title
    from sqlalchemy import func

    db = get_session()
    try:
        # 找出 title IS NULL 且有消息的 conversations
        convs_with_msgs = (
            db.query(Conversation.id)
            .filter(Conversation.title.is_(None))
            .join(ConversationMessage, ConversationMessage.conversation_id == Conversation.id)
            .group_by(Conversation.id)
            .having(func.count(ConversationMessage.id) > 0)
            .all()
        )

        conv_ids = [r[0] for r in convs_with_msgs]
        print(f"找到 {len(conv_ids)} 条需要补标题的会话")

        updated = 0
        for conv_id in conv_ids:
            # 取第一条 user 消息
            first_user = (
                db.query(ConversationMessage)
                .filter(
                    ConversationMessage.conversation_id == conv_id,
                    ConversationMessage.role == "user",
                )
                .order_by(ConversationMessage.created_at.asc())
                .first()
            )
            if not first_user:
                print(f"  [skip] {conv_id[:30]} — 无 user 消息")
                continue

            title = generate_conversation_title(first_user.content)
            conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
            if conv:
                conv.title = title
                db.commit()
                updated += 1
                print(f"  [ok] {conv_id[:30]} → {repr(title)}")

        print(f"\n完成，更新了 {updated} 条记录")
    finally:
        db.close()


if __name__ == "__main__":
    main()
