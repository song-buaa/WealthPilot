"""
WealthPilot - 资产全景页面
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from app.models import Portfolio, Position, Liability, get_session
from app.analyzer import analyze_portfolio, check_deviations
from app.state import portfolio_id, get_position_count
from app.config import SEVERITY_ICONS


def render():
    st.title("📊 资产全景")

    position_count = get_position_count(portfolio_id)
    if position_count == 0:
        st.info("暂无持仓数据，请先在「数据导入」页面上传CSV文件。")
        return

    bs = analyze_portfolio(portfolio_id)
    if not bs:
        st.error("分析失败，请检查数据。")
        return

    session = get_session()
    try:
        portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
    finally:
        session.close()

    # ── 第一行: 核心指标 ──
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("总资产", f"¥{bs.total_assets:,.0f}")
    with col2:
        st.metric("总负债", f"¥{bs.total_liabilities:,.0f}")
    with col3:
        st.metric("净资产", f"¥{bs.net_worth:,.0f}")
    with col4:
        leverage_delta = None
        if portfolio and bs.leverage_ratio > portfolio.max_leverage_ratio:
            leverage_delta = f"超限 {bs.leverage_ratio - portfolio.max_leverage_ratio:.1f}%"
        st.metric("杠杆率", f"{bs.leverage_ratio}%", delta=leverage_delta, delta_color="inverse")
    with col5:
        # 浮动盈亏（有成本价的持仓）
        session2 = get_session()
        try:
            all_pos = session2.query(Position).filter_by(portfolio_id=portfolio_id).all()
            total_pnl = sum(p.profit_loss for p in all_pos)
        finally:
            session2.close()
        pnl_sign = "+" if total_pnl >= 0 else ""
        st.metric("浮动盈亏", f"{pnl_sign}¥{total_pnl:,.0f}")

    st.divider()

    # ── 第二行: 资产配置图表 ──
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("大类资产配置")
        categories = ["权益", "固收", "现金", "另类"]
        current_values = [bs.equity_pct, bs.fixed_income_pct, bs.cash_pct, bs.alternative_pct]
        bar_colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4"]

        # 区间边界
        min_values = [
            portfolio.min_equity_pct, portfolio.min_fixed_income_pct,
            portfolio.min_cash_pct, portfolio.min_alternative_pct,
        ]
        max_values = [
            portfolio.max_equity_pct, portfolio.max_fixed_income_pct,
            portfolio.max_cash_pct, portfolio.max_alternative_pct,
        ]

        fig = go.Figure()
        # 当前配置柱状图
        fig.add_trace(go.Bar(
            name="当前配置",
            x=categories,
            y=current_values,
            marker_color=bar_colors,
            text=[f"{v}%" for v in current_values],
            textposition="outside",
        ))
        # 目标区间上限（透明柱）
        fig.add_trace(go.Bar(
            name="目标上限",
            x=categories,
            y=max_values,
            marker_color=["rgba(200,200,200,0.25)"] * 4,
            marker_line_color=["rgba(150,150,150,0.6)"] * 4,
            marker_line_width=1,
            text=[f"{mn}%~{mx}%" if not (mn == 0 and mx == 100) else "不设约束"
                  for mn, mx in zip(min_values, max_values)],
            textposition="outside",
            textfont=dict(size=10, color="gray"),
        ))
        fig.update_layout(
            barmode="overlay",
            yaxis_title="占比 (%)",
            height=400,
            margin=dict(t=20, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("平台分布")
        if bs.platform_distribution:
            platform_df = pd.DataFrame([
                {"平台": k, "市值": v}
                for k, v in bs.platform_distribution.items()
            ])
            fig2 = px.pie(
                platform_df, values="市值", names="平台",
                color_discrete_sequence=px.colors.qualitative.Set2,
                hole=0.4,
            )
            fig2.update_layout(height=400, margin=dict(t=20, b=20))
            st.plotly_chart(fig2, use_container_width=True)

    st.divider()

    # ── 第三行: 持仓明细 ──
    st.subheader("持仓明细")
    session = get_session()
    positions = session.query(Position).filter_by(portfolio_id=portfolio_id).all()
    session.close()

    if positions:
        pos_data = []
        for p in positions:
            pnl = p.market_value_cny - (p.cost_price * p.quantity) if p.cost_price > 0 and p.quantity > 0 else 0
            pnl_pct = ((p.current_price - p.cost_price) / p.cost_price * 100) if p.cost_price > 0 else 0
            pos_data.append({
                "平台": p.platform,
                "资产名称": p.name,
                "代码": p.ticker or "-",
                "大类": p.asset_class,
                "市值(元)": p.market_value_cny,
                "占比%": bs.concentration.get(f"{p.id}:{p.name}", 0),
                "盈亏(元)": pnl,
                "盈亏%": pnl_pct,
            })
        pos_df = pd.DataFrame(pos_data).sort_values(["平台", "市值(元)"], ascending=[True, False])

        # 格式化显示列
        display_df = pos_df.copy()
        display_df["市值(元)"] = pos_df["市值(元)"].apply(lambda v: f"{v:,.0f}")
        display_df["占比%"] = pos_df["占比%"].apply(lambda v: f"{v:.1f}%")
        display_df["盈亏(元)"] = pos_df["盈亏(元)"].apply(lambda v: f"{v:+,.0f}" if v != 0 else "-")
        display_df["盈亏%"] = pos_df["盈亏%"].apply(lambda v: f"{v:+.1f}%" if v != 0 else "-")
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── 第四行: 负债明细 ──
    session = get_session()
    liabilities = session.query(Liability).filter_by(portfolio_id=portfolio_id).all()
    session.close()

    if liabilities:
        st.subheader("负债明细")
        liab_data = [{
            "负债名称": l.name,
            "类型": l.category,
            "金额(元)": f"{l.amount:,.0f}",
            "年利率": f"{l.interest_rate}%",
        } for l in liabilities]
        st.dataframe(pd.DataFrame(liab_data), use_container_width=True, hide_index=True)

    # ── 第五行: 风险告警 ──
    alerts = check_deviations(portfolio_id, bs)
    if alerts:
        st.divider()
        st.subheader("风险告警")
        for alert in alerts:
            icon = SEVERITY_ICONS.get(alert.severity, "⚪")
            with st.expander(f"{icon} [{alert.alert_type}] {alert.title}", expanded=(alert.severity == "高")):
                st.write(alert.description)
