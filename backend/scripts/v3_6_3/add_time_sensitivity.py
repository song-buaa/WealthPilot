"""
v3.6.3 Migration: time_sensitivity 字段

1. research_documents 表新增 time_sensitivity VARCHAR(20)
2. research_cards 表新增 time_sensitivity VARCHAR(20)
3. viewpoint_cards_v2 表新增 time_sensitivity VARCHAR(20)

用法：
    python backend/scripts/v3_6_3/add_time_sensitivity.py
回滚：
    python backend/scripts/v3_6_3/add_time_sensitivity.py --down
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

    tables = ["research_documents", "research_cards", "viewpoint_cards_v2"]
    for table in tables:
        if _has_column(cur, table, "time_sensitivity"):
            print(f"[skip] {table}.time_sensitivity 已存在")
        else:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN time_sensitivity VARCHAR(20)")
            print(f"[ok] {table}.time_sensitivity 已添加")

    # knowledge_file_path（仅 research_documents）
    if _has_column(cur, "research_documents", "knowledge_file_path"):
        print("[skip] research_documents.knowledge_file_path 已存在")
    else:
        cur.execute("ALTER TABLE research_documents ADD COLUMN knowledge_file_path VARCHAR(500)")
        print("[ok] research_documents.knowledge_file_path 已添加")

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
