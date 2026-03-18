"""
WealthPilot - 数据库基础设施
负责 engine / session 的创建与管理，与业务模型定义解耦。

如需切换数据库（如 PostgreSQL），只改这里即可。
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# ── 路径配置 ──────────────────────────────────
# __file__ = app/database.py，上两级是项目根目录
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "wealthpilot.db")

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


def init_db():
    """创建所有表（幂等操作，可安全多次调用）。"""
    # 延迟 import 避免循环依赖：database ← models ← database
    from app import models  # noqa: F401  触发所有 Model 类的注册
    Base.metadata.create_all(get_engine())
