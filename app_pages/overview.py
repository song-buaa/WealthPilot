"""
WealthPilot - 投资账户总览页面
只统计 segment=="投资" 的资产，只统计 purpose=="投资杠杆" 的负债。
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from app.models import Portfolio, Position, Liability, get_session
from app.analyzer import analyze_portfolio, check_deviations
from app.state import portfolio_id, get_position_count
from app.config import SEVERITY_ICONS, ASSET_CLASS_EXAMPLES
from app.csv_importer import parse_positions_csv, parse_liabilities_csv, import_to_db, positions_to_csv, liabilities_to_csv


def render():
    st.title("📊 投资账户总览")

    position_count = get_position_count(portfolio_id)
    if position_count == 0:
        st.info("暂无持仓数据，请先上传 CSV 文件。")
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

    # ── 核心指标行 ──────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("总资产（投资）", f"¥{bs.total_assets:,.0f}")
    with col2:
        st.metric("投资杠杆负债", f"¥{bs.total_liabilities:,.0f}")
    with col3:
        st.metric("净资产", f"¥{bs.net_worth:,.0f}")
    with col4:
        leverage_delta = None
        if portfolio and bs.leverage_ratio > portfolio.max_leverage_ratio:
            leverage_delta = f"超限 {bs.leverage_ratio - portfolio.max_leverage_ratio:.1f}%"
        st.metric("杠杆率", f"{bs.leverage_ratio}%", delta=leverage_delta, delta_color="inverse")
    with col5:
        pnl = bs.total_profit_loss
        pnl_sign = "+" if pnl >= 0 else ""
        st.metric("浮动盈亏", f"{pnl_sign}¥{pnl:,.0f}")

    st.divider()

    # ── 图表行 ──────────────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("大类资产配置")

        categories = ["货币", "固收", "权益", "另类", "衍生"]
        current_values = [
            bs.monetary_pct, bs.fixed_income_pct,
            bs.equity_pct, bs.alternative_pct, bs.derivative_pct,
        ]
        bar_colors = ["#45B7D1", "#4ECDC4", "#FF6B6B", "#96CEB4", "#F7DC6F"]

        min_values = [
            portfolio.min_cash_pct, portfolio.min_fixed_income_pct,
            portfolio.min_equity_pct, portfolio.min_alternative_pct, 0.0,
        ]
        max_values = [
            portfolio.max_cash_pct, portfolio.max_fixed_income_pct,
            portfolio.max_equity_pct, portfolio.max_alternative_pct, 100.0,
        ]

        examples = [ASSET_CLASS_EXAMPLES.get(c, "") for c in categories]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="当前配置",
            x=categories,
            y=current_values,
            marker_color=bar_colors,
            text=[f"{v}%" for v in current_values],
            textposition="outside",
            hoverinfo="skip",  # hover 由下方 scatter 统一处理
        ))
        fig.add_trace(go.Bar(
            name="目标区间",
            x=categories,
            y=max_values,
            marker_color=["rgba(200,200,200,0.25)"] * 5,
            marker_line_color=["rgba(150,150,150,0.6)"] * 5,
            marker_line_width=1,
            text=[f"{mn}%~{mx}%" if not (mn == 0 and mx == 100) else "不设约束"
                  for mn, mx in zip(min_values, max_values)],
            textposition="outside",
            textfont=dict(size=10, color="gray"),
            hoverinfo="skip",
        ))

        # 透明 scatter：固定悬浮在每个类别上方，保证 0% 时也能 hover
        hover_y = [max(v, 5) for v in current_values]  # 至少 y=5，确保可点击
        fig.add_trace(go.Scatter(
            x=categories,
            y=hover_y,
            mode="markers",
            marker=dict(size=30, color="rgba(0,0,0,0)", line=dict(width=0)),
            customdata=[[c, v, e] for c, v, e in zip(categories, current_values, examples)],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "当前占比: %{customdata[1]}%<br>"
                "示例: %{customdata[2]}"
                "<extra></extra>"
            ),
            showlegend=False,
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

    # ── 资产明细区块 ─────────────────────────────────────────────────
    st.subheader("资产明细")
    _render_asset_section(portfolio_id, bs)

    st.divider()

    # ── 负债明细区块 ─────────────────────────────────────────────────
    st.subheader("负债明细（投资杠杆）")
    _render_liability_section(portfolio_id)

    # ── 风险告警（最下方）──────────────────────────────────
    alerts = check_deviations(portfolio_id, bs)
    if alerts:
        st.divider()
        st.subheader("风险告警")
        for alert in alerts:
            icon = SEVERITY_ICONS.get(alert.severity, "⚪")
            with st.expander(f"{icon} [{alert.alert_type}] {alert.title}", expanded=(alert.severity == "高")):
                st.write(alert.description)


def _render_asset_section(pid: int, bs):
    """资产明细区块"""
    session = get_session()
    try:
        positions = session.query(Position).filter_by(portfolio_id=pid, segment="投资").all()
    finally:
        session.close()

    if not positions:
        st.info("暂无投资持仓数据。")
        return

    pos_rows = []
    for p in positions:
        pnl_orig = p.profit_loss_original_value or 0
        pnl_rate = p.profit_loss_rate
        pnl_cny = p.profit_loss_value

        def _fmt_usd(v):
            if v is None: return "-"
            return f"${v:,.2f}" if v != 0 else "-"

        def _fmt_hkd(v):
            if v is None: return "-"
            return f"HK${v:,.0f}" if v != 0 else "-"

        def _fmt_cny(v):
            if v is None: return "-"
            return f"¥{v:,.0f}"

        def _fmt_pnl_cny(v):
            if not v: return "-"
            sign = "+" if v > 0 else ""
            return f"{sign}¥{v:,.0f}"

        def _fmt_pnl_usd(v):
            if not v: return "-"
            sign = "+" if v > 0 else ""
            return f"{sign}${v:,.2f}"

        def _fmt_pct(v):
            if v is None: return "-"
            sign = "+" if v > 0 else ""
            return f"{sign}{v:.2f}%"

        pos_rows.append({
            "平台": p.platform,
            "资产名称": p.name,
            "代码": p.ticker or "-",
            "大类": p.asset_class,
            "头寸": int(p.quantity) if p.quantity else "-",
            "市值(美元)": _fmt_usd(p.original_value if p.original_currency == "USD" else None),
            "市值(港币)": _fmt_hkd(p.original_value if p.original_currency == "HKD" else None),
            "市值(人民币)": _fmt_cny(p.market_value_cny),
            "占比%": f"{bs.concentration.get(f'{p.id}:{p.name}', 0):.1f}%",
            "盈亏(美元)": _fmt_pnl_usd(pnl_orig if p.original_currency == "USD" and pnl_orig != 0 else None),
            "盈亏(港币)": _fmt_pnl_usd(pnl_orig if p.original_currency == "HKD" and pnl_orig != 0 else None),
            "盈亏(人民币)": _fmt_pnl_cny(pnl_cny),
            "盈亏%": _fmt_pct(pnl_rate),
        })

    # 按平台升序、市值降序排列
    combined = sorted(zip(positions, pos_rows), key=lambda x: (x[0].platform, -x[0].market_value_cny))
    pos_df = pd.DataFrame([r for _, r in combined])

    st.dataframe(pos_df, use_container_width=True, hide_index=True)

    # ── 操作栏：下载 + 导入 ──────────────────────────────────────────
    csv_str = positions_to_csv(session_positions_reload(pid, segment="投资"))
    col_dl, col_imp = st.columns([1, 2])
    with col_dl:
        st.download_button(
            "⬇ 下载资产明细 CSV",
            data=csv_str.encode("utf-8-sig"),
            file_name="positions.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_imp:
        with st.expander("📥 导入资产数据"):
            tab_generic, tab_broker = st.tabs(["通用 CSV（全量覆盖）", "券商官方 CSV（按平台替换）"])

            with tab_generic:
                st.caption("上传后将全量覆盖全部投资持仓，养老/公积金数据不受影响。")
                uploaded = st.file_uploader("选择持仓 CSV 文件", type=["csv"], key="pos_upload")
                if uploaded:
                    content = uploaded.read().decode("utf-8-sig")
                    new_positions, errors = parse_positions_csv(content)
                    if errors:
                        for e in errors:
                            st.error(e)
                    elif new_positions:
                        st.success(f"解析成功，共 {len(new_positions)} 条持仓。")
                        if st.button("确认覆盖全部资产数据", key="confirm_pos_import"):
                            _import_positions_by_segment(pid, new_positions, "投资")
                            st.success("资产数据已更新！")
                            st.rerun()

            with tab_broker:
                st.caption("直接导入老虎证券对账单或富途持仓 CSV，只替换该平台数据，其他平台不受影响。")
                broker = st.radio("选择券商", ["老虎证券", "富途证券"], horizontal=True, key="broker_select")
                broker_file = st.file_uploader(f"上传 {broker} CSV", type=["csv"], key=f"broker_upload_{broker}")
                if broker_file:
                    from app.platform_importers import parse_tiger_csv, parse_futu_csv
                    content = broker_file.read().decode("utf-8-sig")
                    positions_parsed, rate = parse_tiger_csv(content) if broker == "老虎证券" else parse_futu_csv(content)
                    if positions_parsed:
                        preview_rows = [{
                            "资产名称": p["name"], "代码": p["ticker"], "大类": p["asset_class"],
                            "市值(USD)": f"${p['original_value']:,.2f}",
                            "市值(CNY)": f"¥{p['market_value_cny']:,}",
                            "盈亏(USD)": f"{'+' if p['profit_loss_original_value'] >= 0 else ''}${p['profit_loss_original_value']:,.2f}",
                        } for p in positions_parsed]
                        st.success(f"解析成功，共 {len(positions_parsed)} 条持仓，汇率 USD/CNY = {rate:.4f}")
                        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
                        if st.button(f"确认导入 {broker} 数据", key="confirm_broker_import", type="primary"):
                            _import_positions_by_platform(pid, positions_parsed, broker)
                            st.success(f"{broker} 数据已更新！")
                            st.rerun()
                    else:
                        st.error("未能解析到持仓数据，请检查文件格式。")


def _render_liability_section(pid: int):
    """负债明细区块（只展示 purpose==投资杠杆）"""
    session = get_session()
    try:
        liabilities = session.query(Liability).filter_by(
            portfolio_id=pid, purpose="投资杠杆"
        ).all()
    finally:
        session.close()

    if not liabilities:
        st.info("暂无投资杠杆类负债数据。")
    else:
        liab_data = [{
            "负债名称": l.name,
            "类型": l.category,
            "用途": l.purpose,
            "金额(元)": f"¥{l.amount:,.0f}",
            "年利率": f"{l.interest_rate}%",
        } for l in liabilities]
        st.dataframe(pd.DataFrame(liab_data), use_container_width=True, hide_index=True)

    # ── 操作栏：下载 + 导入 ──────────────────────────────────────────
    session2 = get_session()
    try:
        all_invest_liab = session2.query(Liability).filter_by(portfolio_id=pid, purpose="投资杠杆").all()
    finally:
        session2.close()

    col_dl, col_imp = st.columns([1, 2])
    with col_dl:
        csv_str = liabilities_to_csv(all_invest_liab)
        st.download_button(
            "⬇ 下载负债明细 CSV",
            data=csv_str.encode("utf-8-sig"),
            file_name="liabilities_invest.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_imp:
        with st.expander("📥 导入负债数据"):
            st.caption("上传后将覆盖全部「投资杠杆」类负债，养老/生活类负债不受影响。")
            uploaded = st.file_uploader("选择负债 CSV 文件", type=["csv"], key="liab_upload")
            if uploaded:
                content = uploaded.read().decode("utf-8-sig")
                new_liabilities, errors = parse_liabilities_csv(content)
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    st.success(f"解析成功，共 {len(new_liabilities)} 条负债。")
                    if st.button("确认覆盖负债数据", key="confirm_liab_import"):
                        _import_liabilities_by_purpose(pid, new_liabilities, ["投资杠杆"])
                        st.success("负债数据已更新！")
                        st.rerun()


def _save_asset_edits(edited_df: pd.DataFrame, pid: int):
    """将 data_editor 中的修改写回数据库"""
    from app.fx_service import fx_service
    session = get_session()
    try:
        for _, row in edited_df.iterrows():
            pos_id = int(row["_id"])
            p = session.query(Position).filter_by(id=pos_id).first()
            if not p:
                continue

            p.platform = str(row["平台"]) if pd.notna(row["平台"]) else p.platform
            p.name = str(row["资产名称"]) if pd.notna(row["资产名称"]) else p.name
            p.ticker = str(row["代码"]) if pd.notna(row["代码"]) else ""
            p.asset_class = str(row["大类"]) if pd.notna(row["大类"]) else p.asset_class
            p.quantity = float(row["头寸"]) if pd.notna(row["头寸"]) else 0

            # 更新市值
            usd_val = row["市值(美元)"]
            hkd_val = row["市值(港币)"]
            cny_val = row["市值(人民币)"]

            if pd.notna(usd_val) and float(usd_val) > 0:
                p.original_currency = "USD"
                p.original_value = float(usd_val)
                fx, _ = fx_service._get_rate_with_date("USD", "CNY", "latest")
                p.fx_rate_to_cny = fx
                p.market_value_cny = round(float(usd_val) * fx)
            elif pd.notna(hkd_val) and float(hkd_val) > 0:
                p.original_currency = "HKD"
                p.original_value = float(hkd_val)
                fx, _ = fx_service._get_rate_with_date("HKD", "CNY", "latest")
                p.fx_rate_to_cny = fx
                p.market_value_cny = round(float(hkd_val) * fx)
            elif pd.notna(cny_val):
                p.market_value_cny = float(cny_val)

            # 更新盈亏
            pnl_usd = row.get("盈亏(美元)")
            pnl_hkd = row.get("盈亏(港币)")
            pnl_cny = row.get("盈亏(人民币)")
            pnl_rate = row.get("盈亏%")

            if pd.notna(pnl_usd) and float(pnl_usd) != 0:
                p.profit_loss_original_value = float(pnl_usd)
                p.profit_loss_value = round(float(pnl_usd) * p.fx_rate_to_cny)
            elif pd.notna(pnl_hkd) and float(pnl_hkd) != 0:
                p.profit_loss_original_value = float(pnl_hkd)
                p.profit_loss_value = round(float(pnl_hkd) * p.fx_rate_to_cny)
            elif pd.notna(pnl_cny):
                p.profit_loss_value = float(pnl_cny)

            if pd.notna(pnl_rate):
                p.profit_loss_rate = float(pnl_rate)

        session.commit()
    except Exception as e:
        session.rollback()
        st.error(f"保存失败: {e}")
    finally:
        session.close()


def session_positions_reload(pid: int, segment: str = None):
    """重新从DB查询持仓，用于导出"""
    session = get_session()
    try:
        q = session.query(Position).filter_by(portfolio_id=pid)
        if segment:
            q = q.filter_by(segment=segment)
        return q.all()
    finally:
        session.close()


def _import_positions_by_segment(pid: int, new_positions: list, segment: str):
    """只替换指定 segment 的持仓"""
    from app.models import Position
    session = get_session()
    try:
        session.query(Position).filter_by(portfolio_id=pid, segment=segment).delete()
        for p in new_positions:
            p_data = dict(p)
            p_data["segment"] = segment  # 强制设置 segment
            pos = Position(portfolio_id=pid, **p_data)
            session.add(pos)
        session.commit()
    except Exception as e:
        session.rollback()
        st.error(f"导入失败: {e}")
    finally:
        session.close()


def _import_positions_by_platform(pid: int, positions: list, platform: str):
    """只替换指定平台的持仓，其他平台不受影响"""
    from app.models import Position
    session = get_session()
    try:
        session.query(Position).filter_by(portfolio_id=pid, platform=platform, segment="投资").delete()
        for p_data in positions:
            pos = Position(portfolio_id=pid, **p_data)
            session.add(pos)
        session.commit()
    except Exception as e:
        session.rollback()
        st.error(f"导入失败: {e}")
    finally:
        session.close()


def _import_liabilities_by_purpose(pid: int, new_liabilities: list, purposes: list):
    """只替换指定 purpose 的负债"""
    from app.models import Liability
    session = get_session()
    try:
        for purpose in purposes:
            session.query(Liability).filter_by(portfolio_id=pid, purpose=purpose).delete()
        for l in new_liabilities:
            if l.get("purpose") in purposes:
                liab = Liability(portfolio_id=pid, **l)
                session.add(liab)
        session.commit()
    except Exception as e:
        session.rollback()
        st.error(f"导入失败: {e}")
    finally:
        session.close()
