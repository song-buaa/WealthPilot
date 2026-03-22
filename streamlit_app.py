"""
WealthPilot - 主入口（UI v2 — 司南风格重构）
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
    page_title="WealthPilot · 个人智能投顾",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────
# 全局侧边栏样式（深蓝主题）
# ──────────────────────────────────────────────
st.markdown("""
<style>
/* 侧边栏深蓝渐变背景 */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1B2A4A 0%, #243558 100%) !important;
    border-right: 1px solid #2D4A7A !important;
}

/* 侧边栏所有文字 */
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown strong,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span {
    color: #C8D6E8 !important;
}

/* 侧边栏标题 */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #FFFFFF !important;
}

/* 侧边栏分割线 */
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.12) !important;
}

/* 侧边栏按钮 — 通用 */
[data-testid="stSidebar"] .stButton button {
    border-radius: 8px !important;
    font-size: 13px !important;
    border: none !important;
    text-align: left !important;
    transition: all 0.15s ease !important;
    color: #C8D6E8 !important;
    background: transparent !important;
}

/* 侧边栏按钮 — 悬浮 */
[data-testid="stSidebar"] .stButton button:hover {
    background: rgba(255,255,255,0.08) !important;
    color: #FFFFFF !important;
}

/* 侧边栏按钮 — 激活（primary） */
[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: rgba(59,130,246,0.20) !important;
    color: #93C5FD !important;
    font-weight: 600 !important;
    border-left: 2px solid #3B82F6 !important;
}

/* 侧边栏 caption */
[data-testid="stSidebar"] .stCaption {
    color: rgba(200,214,232,0.6) !important;
}

/* 主内容区背景 */
.stApp { background-color: #F4F6FA !important; }

/* 隐藏 Streamlit 默认 header */
header[data-testid="stHeader"] { display: none !important; }

/* 减少顶部空白 */
.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2rem !important;
}

/* ★ 侧边栏宽度固定 220px */
section[data-testid="stSidebar"] {
    width: 220px !important;
    min-width: 220px !important;
}
section[data-testid="stSidebar"] > div:first-child {
    width: 220px !important;
    min-width: 220px !important;
}
</style>
""", unsafe_allow_html=True)

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

_DEFAULT_PAGE = "投资账户总览"


# ──────────────────────────────────────────────
# 侧边栏品牌标识
# ──────────────────────────────────────────────
st.sidebar.markdown("""
<div style="display:flex;align-items:center;gap:10px;padding:4px 0 12px">
  <div style="width:34px;height:34px;border-radius:9px;
              background:linear-gradient(135deg,#3B82F6,#1D4ED8);
              display:flex;align-items:center;justify-content:center;
              font-size:17px;flex-shrink:0">📊</div>
  <div>
    <div style="font-size:15px;font-weight:700;color:#FFFFFF;line-height:1.2">WealthPilot</div>
    <div style="font-size:11px;color:rgba(200,214,232,0.65);line-height:1.2">个人智能投顾系统</div>
  </div>
</div>
""", unsafe_allow_html=True)

st.sidebar.divider()


# ──────────────────────────────────────────────
# 侧边栏导航
# ──────────────────────────────────────────────
def _sidebar_nav() -> str:
    """渲染分组侧边栏导航，返回当前选中的页面名称。"""
    if "current_page" not in st.session_state:
        st.session_state["current_page"] = _DEFAULT_PAGE

    for section_title, items in _NAV_SECTIONS:
        st.sidebar.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;padding:14px 0 4px 0">'
            f'<span style="font-size:13px;font-weight:700;color:rgba(255,255,255,0.88);'
            f'letter-spacing:0.1px">{section_title}</span></div>',
            unsafe_allow_html=True,
        )
        for item in items:
            is_active = st.session_state["current_page"] == item
            if st.sidebar.button(
                item,
                key=f"_nav_{item}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["current_page"] = item
        st.sidebar.write("")

    return st.session_state["current_page"]


current_page = _sidebar_nav()

# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
if current_page in _IMPLEMENTED:
    _IMPLEMENTED[current_page].render()
else:
    placeholder.render(current_page)
