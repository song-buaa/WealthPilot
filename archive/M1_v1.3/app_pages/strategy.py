"""
WealthPilot - 策略设定页面
"""

import streamlit as st
from app.models import Portfolio, get_session
from app.state import portfolio_id


def render():
    st.title("⚙️ 策略设定")
    st.write("设定您的目标资产配置和投资纪律约束。系统将基于这些设定进行偏离检测和风险告警。")

    session = get_session()
    try:
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
    finally:
        session.close()
