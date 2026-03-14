"""
WealthPilot - 主入口
负责页面配置、侧边栏导航，业务逻辑全部委托给 pages/ 下各模块。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from app.state import portfolio_id, get_position_count

# 页面模块
from pages import overview, import_data, strategy, ai_analysis

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
    ["资产全景", "数据导入", "策略设定", "AI 分析"],
    index=0,
)

st.sidebar.divider()
st.sidebar.metric("持仓数量", get_position_count(portfolio_id))

# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
_PAGES = {
    "资产全景": overview,
    "数据导入": import_data,
    "策略设定": strategy,
    "AI 分析":  ai_analysis,
}
_PAGES[page].render()
