"""
WealthPilot - 养老&生活规划页面
独立统计 segment in ["养老", "公积金"] 的资产，
以及 purpose in ["购房", "日常消费"] 的负债。
与投资账户完全隔离，不参与投资杠杆计算。
"""

import streamlit as st
import pandas as pd

from app.models import Position, Liability, get_session
from app.state import portfolio_id
from app.csv_importer import positions_to_csv, liabilities_to_csv


RETIREMENT_SEGMENTS = ["养老", "公积金"]
LIFE_PURPOSES = ["购房", "日常消费"]


def render():
    st.title("🏠 养老&生活规划")
    st.caption("养老&生活规划资金与投资账户完全隔离，不参与投资杠杆计算。")

    session = get_session()
    try:
        # 养老&公积金资产
        positions = session.query(Position).filter(
            Position.portfolio_id == portfolio_id,
            Position.segment.in_(RETIREMENT_SEGMENTS),
        ).all()

        # 购房&日常消费负债
        liabilities = session.query(Liability).filter(
            Liability.portfolio_id == portfolio_id,
            Liability.purpose.in_(LIFE_PURPOSES),
        ).all()
    finally:
        session.close()

    total_assets = sum(p.market_value_cny for p in positions)
    total_liabilities = sum(l.amount for l in liabilities)
    net_worth = total_assets - total_liabilities

    # ── 汇总指标行 ──────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("养老&生活资产总计", f"¥{total_assets:,.0f}")
    with col2:
        st.metric("相关负债（购房+生活）", f"¥{total_liabilities:,.0f}")
    with col3:
        net_sign = "+" if net_worth >= 0 else ""
        st.metric("净值", f"{net_sign}¥{net_worth:,.0f}")

    st.divider()

    # ── Tab 区域 ────────────────────────────────────────────────────
    tab_assets, tab_liab = st.tabs(["资产明细", "负债明细"])

    with tab_assets:
        _render_asset_tab(portfolio_id, positions)

    with tab_liab:
        _render_liability_tab(portfolio_id, liabilities)

    st.divider()
    st.info("💡 养老&生活规划资金与投资账户完全隔离，不参与投资杠杆计算。养老金和公积金受政策保护，建议长期持有。")


def _render_asset_tab(pid: int, positions):
    """资产明细 Tab（养老+公积金）"""
    if not positions:
        st.info("暂无养老&生活资产数据。")
        return

    pos_data = []
    for p in positions:
        pos_data.append({
            "平台": p.platform,
            "资产名称": p.name,
            "代码": p.ticker or "-",
            "大类": p.asset_class,
            "分类": p.segment,
            "市值(人民币)": p.market_value_cny,
        })

    pos_df = pd.DataFrame(pos_data).sort_values(["分类", "市值(人民币)"], ascending=[True, False])
    display_df = pos_df.copy()
    display_df["市值(人民币)"] = pos_df["市值(人民币)"].apply(lambda v: f"¥{v:,.0f}")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # 下载 CSV
    session = get_session()
    try:
        reload_pos = session.query(Position).filter(
            Position.portfolio_id == pid,
            Position.segment.in_(RETIREMENT_SEGMENTS),
        ).all()
    finally:
        session.close()

    csv_str = positions_to_csv(reload_pos)
    st.download_button(
        "下载养老&生活资产明细 CSV",
        data=csv_str.encode("utf-8-sig"),
        file_name="retirement_positions.csv",
        mime="text/csv",
    )


def _render_liability_tab(pid: int, liabilities):
    """负债明细 Tab（购房+日常消费）"""
    if not liabilities:
        st.info("暂无购房&生活负债数据。")
        return

    # 按用途分组展示
    purpose_groups = {}
    for l in liabilities:
        purpose = l.purpose or "日常消费"
        if purpose not in purpose_groups:
            purpose_groups[purpose] = []
        purpose_groups[purpose].append(l)

    for purpose, items in purpose_groups.items():
        subtotal = sum(i.amount for i in items)
        st.markdown(f"**{purpose}** — 小计: ¥{subtotal:,.0f}")
        liab_data = [{
            "负债名称": l.name,
            "类型": l.category,
            "金额(元)": f"¥{l.amount:,.0f}",
            "年利率": f"{l.interest_rate}%",
        } for l in items]
        st.dataframe(pd.DataFrame(liab_data), use_container_width=True, hide_index=True)

    # 下载 CSV
    session = get_session()
    try:
        reload_liab = session.query(Liability).filter(
            Liability.portfolio_id == pid,
            Liability.purpose.in_(LIFE_PURPOSES),
        ).all()
    finally:
        session.close()

    csv_str = liabilities_to_csv(reload_liab)
    st.download_button(
        "下载购房&生活负债明细 CSV",
        data=csv_str.encode("utf-8-sig"),
        file_name="life_liabilities.csv",
        mime="text/csv",
    )
