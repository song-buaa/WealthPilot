"""
WealthPilot - Streamlit 主应用
个人资产配置与智能投顾系统 MVP
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from app.models import Portfolio, Position, Liability, get_session, init_db
from app.csv_importer import (
    get_sample_position_csv, get_sample_liability_csv,
    parse_positions_csv, parse_liabilities_csv,
    import_to_db, ensure_default_portfolio,
)
from app.analyzer import analyze_portfolio, check_deviations, BalanceSheet
from app.ai_advisor import generate_portfolio_analysis

# ──────────────────────────────────────────────
# 页面配置
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="WealthPilot - 个人资产配置与智能投顾",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 初始化数据库和默认组合
init_db()
portfolio_id = ensure_default_portfolio()


# ──────────────────────────────────────────────
# 侧边栏 - 导航与配置
# ──────────────────────────────────────────────
st.sidebar.title("WealthPilot")
st.sidebar.caption("个人资产配置与智能投顾系统")

page = st.sidebar.radio(
    "导航",
    ["资产全景", "数据导入", "策略设定", "AI 分析"],
    index=0,
)

# 加载投资组合信息
session = get_session()
portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
position_count = session.query(Position).filter_by(portfolio_id=portfolio_id).count()
session.close()

st.sidebar.divider()
st.sidebar.metric("持仓数量", position_count)


# ──────────────────────────────────────────────
# 页面: 资产全景
# ──────────────────────────────────────────────
def page_overview():
    st.title("📊 资产全景")

    if position_count == 0:
        st.info("暂无持仓数据，请先在「数据导入」页面上传CSV文件。")
        return

    bs = analyze_portfolio(portfolio_id)
    if not bs:
        st.error("分析失败，请检查数据。")
        return

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

        # 当前配置 vs 目标配置 对比
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
                "占比": f"{bs.concentration.get(p.name, 0):.1f}%",
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
            severity_icon = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(alert.severity, "⚪")
            with st.expander(f"{severity_icon} [{alert.alert_type}] {alert.title}", expanded=(alert.severity == "高")):
                st.write(alert.description)


# ──────────────────────────────────────────────
# 页面: 数据导入
# ──────────────────────────────────────────────
def page_import():
    st.title("📥 数据导入")
    st.write("通过上传CSV文件导入您的持仓和负债数据。系统会全量覆盖已有数据。")

    # ── 持仓导入 ──
    st.subheader("持仓数据")

    with st.expander("查看CSV模板格式"):
        st.code(get_sample_position_csv(), language="csv")
        st.download_button(
            "下载持仓模板",
            get_sample_position_csv(),
            file_name="positions_template.csv",
            mime="text/csv",
        )

    position_file = st.file_uploader("上传持仓CSV", type=["csv"], key="pos_upload")

    # ── 负债导入 ──
    st.subheader("负债数据")

    with st.expander("查看CSV模板格式"):
        st.code(get_sample_liability_csv(), language="csv")
        st.download_button(
            "下载负债模板",
            get_sample_liability_csv(),
            file_name="liabilities_template.csv",
            mime="text/csv",
        )

    liability_file = st.file_uploader("上传负债CSV", type=["csv"], key="liab_upload")

    # ── 导入按钮 ──
    st.divider()
    col1, col2 = st.columns([1, 3])
    with col1:
        use_sample = st.button("使用示例数据", type="secondary")
    with col2:
        do_import = st.button("导入数据", type="primary", disabled=(not position_file and not use_sample))

    if use_sample:
        positions, pos_errors = parse_positions_csv(get_sample_position_csv())
        liabilities, liab_errors = parse_liabilities_csv(get_sample_liability_csv())
        result = import_to_db(portfolio_id, positions, liabilities)
        st.success(f"示例数据已导入！{result}")
        st.rerun()

    if do_import and position_file:
        positions, pos_errors = [], []
        liabilities, liab_errors = [], []

        # 解析持仓
        pos_content = position_file.read().decode("utf-8-sig")
        positions, pos_errors = parse_positions_csv(pos_content)

        # 解析负债
        if liability_file:
            liab_content = liability_file.read().decode("utf-8-sig")
            liabilities, liab_errors = parse_liabilities_csv(liab_content)

        # 显示错误
        all_errors = pos_errors + liab_errors
        if all_errors:
            for err in all_errors:
                st.warning(err)

        # 导入
        if positions or liabilities:
            result = import_to_db(portfolio_id, positions, liabilities)
            st.success(result)
            st.rerun()
        else:
            st.error("没有解析到有效数据，请检查CSV格式。")


# ──────────────────────────────────────────────
# 页面: 策略设定
# ──────────────────────────────────────────────
def page_strategy():
    st.title("⚙️ 策略设定")
    st.write("设定您的目标资产配置和投资纪律约束。系统将基于这些设定进行偏离检测和风险告警。")

    session = get_session()
    p = session.query(Portfolio).filter_by(id=portfolio_id).first()

    with st.form("strategy_form"):
        st.subheader("目标资产配置")
        st.caption("各类资产的目标占比之和应为 100%")

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            target_equity = st.number_input("权益 (%)", 0.0, 100.0, float(p.target_equity_pct), 5.0)
        with col2:
            target_fi = st.number_input("固收 (%)", 0.0, 100.0, float(p.target_fixed_income_pct), 5.0)
        with col3:
            target_cash = st.number_input("现金 (%)", 0.0, 100.0, float(p.target_cash_pct), 5.0)
        with col4:
            target_alt = st.number_input("另类 (%)", 0.0, 100.0, float(p.target_alternative_pct), 5.0)

        total = target_equity + target_fi + target_cash + target_alt
        if abs(total - 100) > 0.1:
            st.warning(f"当前合计 {total:.1f}%，请调整至 100%")

        st.divider()
        st.subheader("投资纪律约束")

        col5, col6 = st.columns(2)
        with col5:
            max_single = st.number_input("单一持仓上限 (%)", 5.0, 50.0, float(p.max_single_stock_pct), 5.0)
        with col6:
            max_leverage = st.number_input("最大杠杆率 (%)", 0.0, 100.0, float(p.max_leverage_ratio), 5.0)

        submitted = st.form_submit_button("保存策略", type="primary")

        if submitted:
            if abs(total - 100) > 0.1:
                st.error("目标配置合计必须为 100%，请调整后重新提交。")
            else:
                p.target_equity_pct = target_equity
                p.target_fixed_income_pct = target_fi
                p.target_cash_pct = target_cash
                p.target_alternative_pct = target_alt
                p.max_single_stock_pct = max_single
                p.max_leverage_ratio = max_leverage
                session.commit()
                st.success("策略已保存！")

    session.close()


# ──────────────────────────────────────────────
# 页面: AI 分析
# ──────────────────────────────────────────────
def page_ai_analysis():
    st.title("🤖 AI 投资分析")

    if position_count == 0:
        st.info("暂无持仓数据，请先在「数据导入」页面上传CSV文件。")
        return

    bs = analyze_portfolio(portfolio_id)
    if not bs:
        st.error("分析失败，请检查数据。")
        return

    alerts = check_deviations(portfolio_id, bs)

    # 显示当前状态摘要
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("净资产", f"¥{bs.net_worth:,.0f}")
    with col2:
        st.metric("权益占比", f"{bs.equity_pct}%")
    with col3:
        alert_count = len(alerts)
        st.metric("风险告警", f"{alert_count} 条", delta=f"{alert_count} 条待处理" if alert_count > 0 else None, delta_color="inverse")

    st.divider()

    # AI 分析按钮
    if st.button("生成 AI 分析报告", type="primary", use_container_width=True):
        with st.spinner("AI 正在分析您的资产配置..."):
            session = get_session()
            p = session.query(Portfolio).filter_by(id=portfolio_id).first()
            target_allocation = {
                "权益": f"{p.target_equity_pct}%",
                "固收": f"{p.target_fixed_income_pct}%",
                "现金": f"{p.target_cash_pct}%",
                "另类": f"{p.target_alternative_pct}%",
            }
            session.close()

            report = generate_portfolio_analysis(bs, alerts, target_allocation)

        st.subheader("AI 分析报告")
        st.markdown(report)

        # 保存到session state
        st.session_state["last_report"] = report

    # 显示上次报告
    elif "last_report" in st.session_state:
        st.subheader("AI 分析报告 (上次生成)")
        st.markdown(st.session_state["last_report"])


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
if page == "资产全景":
    page_overview()
elif page == "数据导入":
    page_import()
elif page == "策略设定":
    page_strategy()
elif page == "AI 分析":
    page_ai_analysis()
