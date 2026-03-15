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
            st.subheader("资产配置框架")
            st.caption("设置每类资产的占比区间（下限 ~ 上限）。填写 0 ~ 100 表示不设约束。")

            st.markdown("**权益**")
            col1, col2 = st.columns(2)
            with col1:
                min_equity = st.number_input("权益下限 (%)", 0.0, 100.0, float(p.min_equity_pct), 5.0)
            with col2:
                max_equity = st.number_input("权益上限 (%)", 0.0, 100.0, float(p.max_equity_pct), 5.0)

            st.markdown("**固收**")
            col3, col4 = st.columns(2)
            with col3:
                min_fi = st.number_input("固收下限 (%)", 0.0, 100.0, float(p.min_fixed_income_pct), 5.0)
            with col4:
                max_fi = st.number_input("固收上限 (%)", 0.0, 100.0, float(p.max_fixed_income_pct), 5.0)

            st.markdown("**现金**")
            col5, col6 = st.columns(2)
            with col5:
                min_cash = st.number_input("现金下限 (%)", 0.0, 100.0, float(p.min_cash_pct), 5.0)
            with col6:
                max_cash = st.number_input("现金上限 (%)", 0.0, 100.0, float(p.max_cash_pct), 5.0)

            st.markdown("**另类**")
            col7, col8 = st.columns(2)
            with col7:
                min_alt = st.number_input("另类下限 (%)", 0.0, 100.0, float(p.min_alternative_pct), 5.0)
            with col8:
                max_alt = st.number_input("另类上限 (%)", 0.0, 100.0, float(p.max_alternative_pct), 5.0)

            st.divider()
            st.subheader("投资纪律约束")

            col9, col10 = st.columns(2)
            with col9:
                max_single = st.number_input("单一持仓上限 (%)", 5.0, 50.0, float(p.max_single_stock_pct), 5.0)
            with col10:
                max_leverage = st.number_input("最大杠杆率 (%)", 0.0, 100.0, float(p.max_leverage_ratio), 5.0)

            submitted = st.form_submit_button("保存策略", type="primary")

            if submitted:
                errors = []
                for label, mn, mx in [("权益", min_equity, max_equity),
                                       ("固收", min_fi, max_fi),
                                       ("现金", min_cash, max_cash),
                                       ("另类", min_alt, max_alt)]:
                    if mn > mx:
                        errors.append(f"{label}：下限 ({mn}%) 不能大于上限 ({mx}%)")
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    p.min_equity_pct = min_equity
                    p.max_equity_pct = max_equity
                    p.min_fixed_income_pct = min_fi
                    p.max_fixed_income_pct = max_fi
                    p.min_cash_pct = min_cash
                    p.max_cash_pct = max_cash
                    p.min_alternative_pct = min_alt
                    p.max_alternative_pct = max_alt
                    p.max_single_stock_pct = max_single
                    p.max_leverage_ratio = max_leverage
                    session.commit()
                    st.success("策略已保存！")
    finally:
        session.close()
