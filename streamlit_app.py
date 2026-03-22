"""
WealthPilot - 主入口（UI v2 — 司南风格重构）
修正：Sidebar 品牌区顶部 padding 调整为 20px。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st

from app_pages import overview, retirement_life, discipline, strategy, research
from app_pages import placeholder

st.set_page_config(
    page_title="WealthPilot · 个人智能投顾",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# 全局 CSS：修正物理平移方案
# ──────────────────────────────────────────────
st.markdown("""
<style>
/* 隐藏 Streamlit 的 header */
header[data-testid="stHeader"] { display: none !important; }

/* 主应用背景 */
.stApp { background-color: #F4F6FA !important; }

/* ═══ 主内容区顶部物理平移修正 ═══ */
div[data-testid="stMainBlockContainer"] {
    margin-top: 0px !important;
    padding-top: 0 !important;
    padding-bottom: 2rem !important;
}

/* 针对 Emotion Cache 的暴力覆盖 */
.st-emotion-cache-zy6yx3 {
    margin-top: 0px !important;
    padding-top: 0 !important;
}

/* ═══ Sidebar 容器基础样式 ═══ */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1B2A4A 0%, #0F1E35 100%) !important;
    border-right: 1px solid rgba(255,255,255,0.05) !important;
    width: 220px !important;
    min-width: 220px !important;
}

/* ═══ Sidebar 顶部留白强制微调 ═══ */
[data-testid="stSidebarHeader"] {
    display: none !important;
}

/* 按照用户要求：将内容容器的 padding-top 设为 6px */
div[data-testid="stSidebarUserContent"] {
    padding-top: 6px !important;
}

.st-emotion-cache-6q9u6q {
    padding-top: 6px !important;
}

/* ═══ Sidebar 内所有 Markdown 容器 ═══ */
[data-testid="stSidebar"] .stMarkdown {
    margin: 0 !important;
    padding: 0 !important;
}

[data-testid="stSidebar"] .stMarkdown > div {
    margin: 0 !important;
    padding: 0 !important;
}

/* ═══ Sidebar 内所有按钮 — 基础样式 ═══ */
[data-testid="stSidebar"] .stButton {
    margin: 0 !important;
    padding: 0 !important;
    width: 100% !important;
}

[data-testid="stSidebar"] .stButton button {
    display: flex !important;
    align-items: center !important;
    width: calc(100% - 16px) !important;
    margin: 1px 8px !important;
    padding: 0 8px 0 20px !important;
    height: 28px !important;
    min-height: 28px !important;
    line-height: 28px !important;
    border: none !important;
    border-radius: 6px !important;
    background: transparent !important;
    color: rgba(255,255,255,0.48) !important;
    font-size: 12px !important;
    font-weight: 400 !important;
    text-align: left !important;
    cursor: pointer !important;
    transition: background 0.14s, color 0.14s !important;
    box-sizing: border-box !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    border-left: 2px solid transparent !important;
}

[data-testid="stSidebar"] .stButton button p {
    font-size: 12px !important;
    font-weight: inherit !important;
    color: inherit !important;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1 !important;
}

/* ═══ 菜单项 — 悬浮 ═══ */
[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {
    background: rgba(255,255,255,0.06) !important;
    color: rgba(255,255,255,0.78) !important;
}

/* ═══ 菜单项 — 激活（克制高亮）═══ */
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    background: rgba(59,130,246,0.14) !important;
    color: #93C5FD !important;
    font-weight: 600 !important;
    border-left: 2px solid #3B82F6 !important;
    padding-left: 18px !important;
    padding-right: 8px !important;
}

[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] p {
    color: #93C5FD !important;
    font-weight: 600 !important;
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# 导航结构定义
# ──────────────────────────────────────────────
_NAV_SECTIONS = [
    ("📈", "投资规划", [
        "用户画像和投资目标",
        "新增资产配置",
        "投资账户总览",
        "投资纪律",
        "投研观点",
        "投资决策",
        "投资记录",
        "收益分析",
    ]),
    ("🏠", "财务规划", [
        "生活账户总览",
        "养老规划",
        "购房规划",
        "消费规划",
    ]),
    ("📊", "资产负债总览", [
        "个人资产负债总览",
        "家族资产负债总览",
    ]),
]

_IMPLEMENTED = {
    "投资账户总览": overview,
    "投资纪律":     discipline,
    "投资决策":     strategy,
    "养老规划":     retirement_life,
    "投研观点":     research,
}

_DEFAULT_PAGE = "投资账户总览"


# ──────────────────────────────────────────────
# Sidebar 导航渲染
# ──────────────────────────────────────────────
def render_sidebar() -> str:
    """
    使用 st.button 渲染 Sidebar 导航。
    """
    # 初始化 session_state
    if "current_page" not in st.session_state:
        st.session_state.current_page = _DEFAULT_PAGE

    # 品牌区：按照用户要求将 padding-top 调整为 20px
    st.sidebar.markdown("""
    <div style="
      display:flex; align-items:center; gap:9px;
      padding:20px 12px 12px 12px;
      border-bottom:1px solid rgba(255,255,255,0.07);
    ">
      <div style="
        width:34px; height:34px; flex-shrink:0;
        border-radius:9px;
        background:linear-gradient(135deg,#3B82F6 0%,#1D4ED8 100%);
        display:flex; align-items:center; justify-content:center;
        font-size:16px; line-height:1;
      ">📊</div>
      <div style="min-width:0;">
        <div style="font-size:14px; font-weight:700; color:#FFFFFF; line-height:1.15; white-space:nowrap;">WealthPilot</div>
        <div style="font-size:10.5px; font-weight:400; color:rgba(255,255,255,0.38); line-height:1.2; margin-top:2px; white-space:nowrap;">个人智能投顾系统</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # 导航菜单
    for i, (icon, section_title, items) in enumerate(_NAV_SECTIONS):
        # 分组标题
        st.sidebar.markdown(
            f'<div style="display:flex; align-items:center; gap:7px; padding:{"12px" if i == 0 else "6px"} 12px 5px 12px;">'
            f'<span style="font-size:13px; line-height:1;">{icon}</span>'
            f'<span style="font-size:12.5px; font-weight:700; color:rgba(255,255,255,0.90); letter-spacing:0.1px; line-height:1;">{section_title}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # 菜单项
        for item in items:
            is_active = (st.session_state.current_page == item)
            if st.sidebar.button(
                item,
                key=f"nav_{item}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state.current_page = item
                st.rerun()

        # 分组分隔线
        if i < len(_NAV_SECTIONS) - 1:
            st.sidebar.markdown(
                '<div style="height:1px; background:rgba(255,255,255,0.07); margin:8px 12px 0 12px;"></div>',
                unsafe_allow_html=True
            )

    # 底部间距
    st.sidebar.markdown('<div style="height:16px;"></div>', unsafe_allow_html=True)

    return st.session_state.current_page


# ──────────────────────────────────────────────
# 主程序
# ──────────────────────────────────────────────
current_page = render_sidebar()

# 路由分发
if current_page in _IMPLEMENTED:
    _IMPLEMENTED[current_page].render()
else:
    placeholder.render(current_page)
