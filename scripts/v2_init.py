"""
v2 数据库初始化脚本 — 幂等，重跑不报错。

功能:
1. ALTER research_documents 加 raw_content_hash / parsed_primary_symbol
2. ALTER positions 加 symbol_v2
3. CREATE viewpoint_cards_v2 表

用法: python scripts/v2_init.py
"""

import logging
import os
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from app.database import get_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _table_exists(engine, table_name: str) -> bool:
    """检查表是否存在。"""
    insp = inspect(engine)
    return table_name in insp.get_table_names()


def _column_exists(engine, table_name: str, column_name: str) -> bool:
    """检查某表的某列是否存在。"""
    insp = inspect(engine)
    if table_name not in insp.get_table_names():
        return False
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns


def _alter_add_column(engine, table_name: str, column_name: str, column_type: str) -> None:
    """幂等 ALTER TABLE ADD COLUMN。"""
    if _column_exists(engine, table_name, column_name):
        logger.info("  跳过: %s.%s 已存在", table_name, column_name)
        return
    sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    logger.info("  新增: %s.%s (%s)", table_name, column_name, column_type)


def step1_alter_research_documents(engine) -> None:
    """ALTER research_documents 加 raw_content_hash / parsed_primary_symbol。"""
    logger.info("Step 1: ALTER research_documents")
    if not _table_exists(engine, "research_documents"):
        logger.warning("  表 research_documents 不存在，跳过")
        return
    _alter_add_column(engine, "research_documents", "raw_content_hash", "TEXT")
    _alter_add_column(engine, "research_documents", "parsed_primary_symbol", "TEXT")


def step2_alter_positions(engine) -> None:
    """ALTER positions 加 symbol_v2。"""
    logger.info("Step 2: ALTER positions")
    if not _table_exists(engine, "positions"):
        logger.warning("  表 positions 不存在，跳过")
        return
    _alter_add_column(engine, "positions", "symbol_v2", "TEXT")


def step3_create_viewpoint_cards_v2(engine) -> None:
    """CREATE viewpoint_cards_v2 表（幂等）。"""
    logger.info("Step 3: CREATE viewpoint_cards_v2")
    if _table_exists(engine, "viewpoint_cards_v2"):
        logger.info("  表已存在，检查是否缺列")
        _alter_add_column(engine, "viewpoint_cards_v2", "ingested_at",
                          "DATETIME DEFAULT CURRENT_TIMESTAMP")
        return

    ddl = """
    CREATE TABLE viewpoint_cards_v2 (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id           TEXT    NOT NULL UNIQUE,
        primary_symbol    TEXT,
        primary_entity_id TEXT,
        source_type       TEXT    NOT NULL,
        as_of             DATETIME NOT NULL,
        ingested_at       DATETIME NOT NULL,
        facts_json        TEXT    NOT NULL,
        narrative_json    TEXT    NOT NULL,
        judgment_json     TEXT    NOT NULL,
        validity_status   TEXT    NOT NULL DEFAULT 'active',
        confidence_score  REAL    NOT NULL DEFAULT 0.3,
        user_endorsement  TEXT    NOT NULL DEFAULT 'reference_only',
        stance            TEXT,
        action_type       TEXT,
        event_type        TEXT,
        relations_json    TEXT,
        status            TEXT    NOT NULL DEFAULT 'pending_review',
        created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """
    with engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()
    logger.info("  建表完成: viewpoint_cards_v2")

    # 创建索引
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_vpc2_card_id ON viewpoint_cards_v2(card_id)",
        "CREATE INDEX IF NOT EXISTS idx_vpc2_primary_symbol ON viewpoint_cards_v2(primary_symbol)",
        "CREATE INDEX IF NOT EXISTS idx_vpc2_primary_entity_id ON viewpoint_cards_v2(primary_entity_id)",
        "CREATE INDEX IF NOT EXISTS idx_vpc2_validity_status ON viewpoint_cards_v2(validity_status)",
        "CREATE INDEX IF NOT EXISTS idx_vpc2_event_type ON viewpoint_cards_v2(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_vpc2_ingested_at ON viewpoint_cards_v2(ingested_at)",
    ]
    with engine.connect() as conn:
        for idx_sql in indexes:
            conn.execute(text(idx_sql))
        conn.commit()
    logger.info("  索引创建完成: %d 个", len(indexes))


def main() -> None:
    logger.info("=== v2 数据库初始化开始 ===")
    engine = get_engine()
    step1_alter_research_documents(engine)
    step2_alter_positions(engine)
    step3_create_viewpoint_cards_v2(engine)
    logger.info("=== v2 数据库初始化完成 ===")


if __name__ == "__main__":
    main()
