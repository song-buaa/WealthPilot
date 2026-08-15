"""
WealthPilot - 数据库基础设施
负责 engine / session 的创建与管理，与业务模型定义解耦。

如需切换数据库（如 PostgreSQL），只改这里即可。
"""

import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# ── 路径配置 ──────────────────────────────────
# __file__ = app/database.py，上两级是项目根目录
DB_PATH = os.environ.get(
    "WEALTHPILOT_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "wealthpilot.db"),
)

# ── ORM Base（所有 Model 继承此对象）─────────────
Base = declarative_base()

# ── 懒加载 engine / session factory ─────────────
_engine = None
_SessionLocal = None


def get_engine():
    """获取（或创建）SQLAlchemy engine，首次调用时才真正连接。"""
    global _engine
    if _engine is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        # NullPool：不缓存连接，每次 get_session() 都打开全新连接。
        # 这解决了 Streamlit 多次 rerun 时 SQLite 读到旧快照的问题。
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, poolclass=NullPool)
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal


def get_session():
    """获取数据库会话。调用方负责 close（建议用 try/finally）。"""
    return _get_session_factory()()


def SessionLocal():
    """获取数据库会话（别名,与 FastAPI 风格一致）。"""
    return _get_session_factory()()


def init_db():
    """创建所有表（幂等操作，可安全多次调用）。"""
    # 延迟 import 避免循环依赖：database ← models ← database
    from app import models  # noqa: F401  触发所有 Model 类的注册
    import backend.services.action.models  # noqa: F401  v3.2 投资行动模块 5 张表
    import backend.services.execution_plan.models  # noqa: F401  v3.14 执行计划表
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_position_ownership_columns(engine)
    _ensure_conversation_message_metadata_column(engine)
    _ensure_execution_linkage_columns(engine)


def _ensure_position_ownership_columns(engine) -> None:
    """为既有 SQLite positions 表幂等补齐 Broker 同步归属字段。"""
    if engine.dialect.name != "sqlite":
        return

    columns = {column["name"] for column in inspect(engine).get_columns("positions")}
    additions = {
        "broker": "VARCHAR(20)",
        "broker_account_id": "VARCHAR(50)",
        "sync_source": "VARCHAR(20)",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {sql_type}"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_positions_sync_owner "
            "ON positions (broker, broker_account_id, sync_source, symbol)"
        ))


def _ensure_conversation_message_metadata_column(engine) -> None:
    """Idempotently add the lightweight message metadata extension."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "conversation_messages" not in inspector.get_table_names():
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("conversation_messages")
    }
    if "metadata_json" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE conversation_messages ADD COLUMN metadata_json TEXT"
            ))


def _ensure_execution_linkage_columns(engine) -> None:
    """为既有 action 表幂等补齐 v3.15 Batch/Leg 追溯与幂等字段。"""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    additions = {
        "symbol_strategies": {"batch_leg_id": "VARCHAR(36)"},
        "order_records": {
            "batch_id": "VARCHAR(36)",
            "batch_leg_id": "VARCHAR(36)",
            "confirmation_version": "INTEGER",
        },
        "execution_legs": {
            "limit_source": "VARCHAR(30)",
            "manual_limit_confirmed_at": "DATETIME",
            "market_open": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in tables:
                continue
            existing = {
                column["name"] for column in inspect(engine).get_columns(table)
            }
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                    ))
        if "order_records" in tables:
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_order_records_batch_submission "
                "ON order_records (batch_id, confirmation_version, batch_leg_id) "
                "WHERE batch_id IS NOT NULL"
            ))
