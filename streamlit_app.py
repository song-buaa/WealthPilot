"""
WealthPilot - 主入口
负责页面配置、侧边栏导航，业务逻辑全部委托给 app_pages/ 下各模块。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

# 已实现的页面模块
from app_pages import overview, retirement_life, discipline, strategy, research
from app_pages import placeholder

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
# 导航结构定义
# ──────────────────────────────────────────────
_NAV_SECTIONS = [
    ("📈  投资规划", [
        "用户画像和投资目标",
        "新增资产配置",
        "投资账户总览",
        "投资纪律",
        "投研观点",
        "投资决策",
        "投资记录",
        "收益分析",
    ]),
    ("🏠  财务规划", [
        "生活账户总览",
        "养老规划",
        "购房规划",
        "消费规划",
    ]),
    ("📊  资产负债总览", [
        "个人资产负债总览",
        "家族资产负债总览",
    ]),
]

# 已实现页面的模块映射
_IMPLEMENTED = {
    "投资账户总览": overview,
    "投资纪律":     discipline,
    "投资决策":     strategy,
    "养老规划":     retirement_life,
    "投研观点":     research,
}

# 首页策略：有持仓数据时直接进投资账户总览，否则引导至用户画像
_DEFAULT_PAGE = "投资账户总览"


# ──────────────────────────────────────────────
# 侧边栏导航
# ──────────────────────────────────────────────
def _sidebar_nav() -> str:
    """渲染分组侧边栏导航，返回当前选中的页面名称。
    使用 session_state["current_page"] 持久化选中状态，
    任何页面内的 button 点击不会导致导航跳回。
    """
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = _DEFAULT_PAGE

    for section_title, items in _NAV_SECTIONS:
        st.sidebar.markdown(f"**{section_title}**")
        for item in items:
            is_active = st.session_state["current_page"] == item
            if st.sidebar.button(
                item,
                key=f"_nav_{item}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["current_page"] = item
        st.sidebar.write("")  # 组间间距

    return st.session_state["current_page"]


# ──────────────────────────────────────────────
# 渲染侧边栏
# ──────────────────────────────────────────────
st.sidebar.title("WealthPilot")
st.sidebar.caption("个人资产配置与智能投顾系统")
st.sidebar.divider()

current_page = _sidebar_nav()

# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
if current_page in _IMPLEMENTED:
    _IMPLEMENTED[current_page].render()
else:
    placeholder.render(current_page)
