"""
v3.5 M0 Migration: session_id → conversation_id

重命名两张表的列：
1. conversation_messages.session_id → conversation_id
2. decision_history.session_id → conversation_id

SQLite 3.25.0+ 支持 ALTER TABLE ... RENAME COLUMN。
macOS 自带的 SQLite 版本 >= 3.28，可直接使用。

用法：
    python backend/scripts/v3_5/rename_session_id_to_conversation_id.py

回滚（down）：
    python backend/scripts/v3_5/rename_session_id_to_conversation_id.py --down
"""

import os
import sys
import sqlite3

# 项目根目录
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "wealthpilot.db")


def _get_columns(cursor: sqlite3.Cursor, table: str) -> list[str]:
    """获取表的列名列表。"""
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def up(db_path: str = DB_PATH) -> None:
    """session_id → conversation_id"""
    if not os.path.exists(db_path):
        print(f"[skip] 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrations = [
        ("conversation_messages", "session_id", "conversation_id"),
        ("decision_history", "session_id", "conversation_id"),
    ]

    for table, old_col, new_col in migrations:
        columns = _get_columns(cursor, table)
        if old_col not in columns:
            if new_col in columns:
                print(f"[skip] {table}.{new_col} 已存在（已迁移过）")
            else:
                print(f"[skip] {table} 中无 {old_col} 列")
            continue

        cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")
        print(f"[ok] {table}.{old_col} → {new_col}")

    conn.commit()
    conn.close()
    print("[done] migration up 完成")


def down(db_path: str = DB_PATH) -> None:
    """conversation_id → session_id（回滚）"""
    if not os.path.exists(db_path):
        print(f"[skip] 数据库不存在: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    migrations = [
        ("conversation_messages", "conversation_id", "session_id"),
        ("decision_history", "conversation_id", "session_id"),
    ]

    for table, old_col, new_col in migrations:
        columns = _get_columns(cursor, table)
        if old_col not in columns:
            if new_col in columns:
                print(f"[skip] {table}.{new_col} 已存在（已回滚过）")
            else:
                print(f"[skip] {table} 中无 {old_col} 列")
            continue

        cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")
        print(f"[ok] {table}.{old_col} → {new_col}")

    conn.commit()
    conn.close()
    print("[done] migration down 完成")


if __name__ == "__main__":
    if "--down" in sys.argv:
        down()
    else:
        up()
