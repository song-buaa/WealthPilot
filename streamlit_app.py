"""
WealthPilot - 主入口
负责页面配置、侧边栏导航，业务逻辑全部委托给 pages/ 下各模块。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from app.models import Position, get_session
from app.state import portfolio_id, get_position_count

# 页面模块
from app_pages import overview, retirement_life, strategy, ai_analysis

# ──────────────────────────────────────────────
# 页面配置
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="WealthPilot - 个人资产配置与智能投顾",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# 侧边栏导航
# ──────────────────────────────────────────────
st.sidebar.title("WealthPilot")
st.sidebar.caption("个人资产配置与智能投顾系统")

page = st.sidebar.radio(
    "导航",
    ["投资账户总览", "养老&生活规划", "投资策略", "AI 分析报告"],
    index=0,
)

st.sidebar.divider()

# 侧边栏只统计投资持仓数量
def _get_invest_position_count(pid: int) -> int:
    session = get_session()
    try:
        return session.query(Position).filter_by(portfolio_id=pid, segment="投资").count()
    finally:
        session.close()

st.sidebar.metric("投资持仓数量", _get_invest_position_count(portfolio_id))

# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
_PAGES = {
    "投资账户总览": overview,
    "养老&生活规划": retirement_life,
    "投资策略":      strategy,
    "AI 分析报告":   ai_analysis,
}
_PAGES[page].render()
