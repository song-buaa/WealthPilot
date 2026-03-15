"""
WealthPilot - AI 投资分析页面
"""

import streamlit as st
from app.models import Portfolio, get_session
from app.analyzer import analyze_portfolio, check_deviations
from app.ai_advisor import generate_portfolio_analysis
from app.state import portfolio_id, get_position_count


def render():
    st.title("🤖 AI 投资分析")

    position_count = get_position_count(portfolio_id)
    if position_count == 0:
        st.info("暂无持仓数据，请先在「数据导入」页面上传CSV文件。")
        return

    bs = analyze_portfolio(portfolio_id)
    if not bs:
        st.error("分析失败，请检查数据。")
        return

    alerts = check_deviations(portfolio_id, bs)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("净资产", f"¥{bs.net_worth:,.0f}")
    with col2:
        st.metric("权益占比", f"{bs.equity_pct}%")
    with col3:
        alert_count = len(alerts)
        st.metric(
            "风险告警", f"{alert_count} 条",
            delta=f"{alert_count} 条待处理" if alert_count > 0 else None,
            delta_color="inverse",
        )

    st.divider()

    if st.button("生成 AI 分析报告", type="primary", use_container_width=True):
        with st.spinner("AI 正在分析您的资产配置..."):
            session = get_session()
            try:
                p = session.query(Portfolio).filter_by(id=portfolio_id).first()
                def _fmt_range(mn, mx):
                    if mn == 0.0 and mx == 100.0:
                        return "不设约束"
                    return f"{mn}%~{mx}%"
                target_allocation = {
                    "权益": _fmt_range(p.min_equity_pct, p.max_equity_pct),
                    "固收": _fmt_range(p.min_fixed_income_pct, p.max_fixed_income_pct),
                    "现金": _fmt_range(p.min_cash_pct, p.max_cash_pct),
                    "另类": _fmt_range(p.min_alternative_pct, p.max_alternative_pct),
                }
            finally:
                session.close()

            report = generate_portfolio_analysis(bs, alerts, target_allocation)

        st.subheader("AI 分析报告")
        st.markdown(report)
        st.session_state["last_report"] = report

    elif "last_report" in st.session_state:
        st.subheader("AI 分析报告 (上次生成)")
        st.markdown(st.session_state["last_report"])
