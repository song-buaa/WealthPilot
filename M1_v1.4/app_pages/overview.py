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
    col1, col2, col3, col4 = st.columns(4)
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

    st.divider()

    # ── 第二行: 资产配置图表 ──
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("大类资产配置")
        categories = ["权益", "固收", "现金", "另类"]
        current_values = [bs.equity_pct, bs.fixed_income_pct, bs.cash_pct, bs.alternative_pct]
        target_values = [
            portfolio.target_equity_pct,
            portfolio.target_fixed_income_pct,
            portfolio.target_cash_pct,
            portfolio.target_alternative_pct,
        ]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="当前配置",
            x=categories,
            y=current_values,
            marker_color=["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4"],
            text=[f"{v}%" for v in current_values],
            textposition="outside",
        ))
        fig.add_trace(go.Bar(
            name="目标配置",
            x=categories,
            y=target_values,
            marker_color=["rgba(255,107,107,0.3)", "rgba(78,205,196,0.3)",
                          "rgba(69,183,209,0.3)", "rgba(150,206,180,0.3)"],
            text=[f"{v}%" for v in target_values],
            textposition="outside",
        ))
        fig.update_layout(
            barmode="group",
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
                "资产名称": p.name,
                "代码": p.ticker or "-",
                "平台": p.platform,
                "大类": p.asset_class,
                "市值(元)": f"{p.market_value_cny:,.0f}",
                "占比": f"{bs.concentration.get(f'{p.id}:{p.name}', 0):.1f}%",
                "盈亏": f"{pnl:+,.0f}",
                "盈亏%": f"{pnl_pct:+.1f}%",
            })
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)

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
