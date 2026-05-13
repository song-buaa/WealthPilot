"""
v3.5.2 M1 Migration: 长对话记忆压缩字段

1. conversations 表新增 context_summary TEXT
2. conversation_messages 表新增 is_summarized BOOLEAN DEFAULT 0

用法：
    python backend/scripts/v3_5_2/add_summary_fields.py
回滚：
    python backend/scripts/v3_5_2/add_summary_fields.py --down
"""
import os
import sys
import sqlite3

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "wealthpilot.db")


def _has_column(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up(db_path: str = DB_PATH) -> None:
    if not os.path.exists(db_path):
        print(f"[skip] 数据库不存在: {db_path}（init_db 会自动建表）")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if _has_column(cur, "conversations", "context_summary"):
        print("[skip] conversations.context_summary 已存在")
    else:
        cur.execute("ALTER TABLE conversations ADD COLUMN context_summary TEXT")
        print("[ok] conversations.context_summary 已添加")

    if _has_column(cur, "conversation_messages", "is_summarized"):
        print("[skip] conversation_messages.is_summarized 已存在")
    else:
        cur.execute("ALTER TABLE conversation_messages ADD COLUMN is_summarized BOOLEAN DEFAULT 0 NOT NULL")
        print("[ok] conversation_messages.is_summarized 已添加")

    conn.commit()
    conn.close()
    print("[done] migration up 完成")


def down(db_path: str = DB_PATH) -> None:
    print("[warn] SQLite 不支持 DROP COLUMN（3.35.0 以下）。")
    print("       如需回滚，请手动重建表或升级 SQLite。")


if __name__ == "__main__":
    if "--down" in sys.argv:
        down()
    else:
        up()
