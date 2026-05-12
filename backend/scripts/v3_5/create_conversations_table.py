"""
v3.5 M1 Migration: 创建 conversations 主表

1. CREATE TABLE conversations
2. conversation_messages.conversation_id 加 FK（SQLite 不支持 ADD CONSTRAINT，
   但 ORM 层的 ForeignKey 声明足够新表使用，旧表数据靠应用层保证一致性）

用法：
    python backend/scripts/v3_5/create_conversations_table.py
回滚：
    python backend/scripts/v3_5/create_conversations_table.py --down
"""

import os
import sys
import sqlite3

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "wealthpilot.db")


def up(db_path: str = DB_PATH) -> None:
    if not os.path.exists(db_path):
        print(f"[skip] 数据库不存在: {db_path}（init_db 会自动建表）")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查 conversations 表是否已存在
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    if cursor.fetchone():
        print("[skip] conversations 表已存在")
    else:
        cursor.execute("""
            CREATE TABLE conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                portfolio_id INTEGER,
                status      TEXT NOT NULL DEFAULT 'active',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id)
            )
        """)
        print("[ok] conversations 表已创建")

    conn.commit()
    conn.close()
    print("[done] migration up 完成")


def down(db_path: str = DB_PATH) -> None:
    if not os.path.exists(db_path):
        print(f"[skip] 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
    )
    if not cursor.fetchone():
        print("[skip] conversations 表不存在")
    else:
        cursor.execute("DROP TABLE conversations")
        print("[ok] conversations 表已删除")

    conn.commit()
    conn.close()
    print("[done] migration down 完成")


if __name__ == "__main__":
    if "--down" in sys.argv:
        down()
    else:
        up()
