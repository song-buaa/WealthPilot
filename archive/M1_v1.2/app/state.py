"""
WealthPilot - 应用共享状态
提供跨页面共享的初始化逻辑和缓存查询。
"""

import streamlit as st
from app.models import Position, get_session, init_db
from app.csv_importer import ensure_default_portfolio

# 初始化（幂等，多次调用安全）
init_db()
portfolio_id: int = ensure_default_portfolio()


@st.cache_data(ttl=5)
def get_position_count(pid: int) -> int:
    """缓存持仓数量查询，ttl=5s 保证导入后能很快刷新。"""
    session = get_session()
    try:
        return session.query(Position).filter_by(portfolio_id=pid).count()
    finally:
        session.close()
