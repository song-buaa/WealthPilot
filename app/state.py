"""
WealthPilot - 应用共享状态
提供跨模块共享的初始化逻辑和基础查询。

Phase 1 改造：移除 Streamlit 依赖，改为由 FastAPI lifespan 调用 startup()。
Streamlit 页面仍可导入 portfolio_id，行为与之前一致。
"""

from app.models import Position, get_session, init_db
from app.csv_importer import ensure_default_portfolio

# 模块级变量，startup() 调用后填充
portfolio_id: int = 0


def startup() -> int:
    """
    FastAPI lifespan 启动时调用，替代原来的模块级 init_db()。
    返回默认 portfolio_id。
    """
    global portfolio_id
    init_db()
    portfolio_id = ensure_default_portfolio()
    return portfolio_id


def get_position_count(pid: int) -> int:
    """持仓数量查询（去掉 @st.cache_data，FastAPI 场景直接查询）。"""
    session = get_session()
    try:
        return session.query(Position).filter_by(portfolio_id=pid).count()
    finally:
        session.close()
