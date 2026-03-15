"""
WealthPilot - 数据导入页面
"""

import streamlit as st
from app.csv_importer import (
    get_sample_position_csv, get_sample_liability_csv,
    parse_positions_csv, parse_liabilities_csv,
    import_to_db,
)
from app.state import portfolio_id


def render():
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
        # 示例数据直接导入，无需确认
        positions, _ = parse_positions_csv(get_sample_position_csv())
        liabilities, _ = parse_liabilities_csv(get_sample_liability_csv())
        result = import_to_db(portfolio_id, positions, liabilities)
        st.success(f"示例数据已导入！{result}")
        st.rerun()

    if do_import and position_file:
        positions, pos_errors = [], []
        liabilities, liab_errors = [], []

        pos_content = position_file.read().decode("utf-8-sig")
        positions, pos_errors = parse_positions_csv(pos_content)

        if liability_file:
            liab_content = liability_file.read().decode("utf-8-sig")
            liabilities, liab_errors = parse_liabilities_csv(liab_content)

        for err in pos_errors + liab_errors:
            st.warning(err)

        if not (positions or liabilities):
            st.error("没有解析到有效数据，请检查CSV格式。")
            return

        # ── 解析预览 + 二次确认 ──
        st.divider()
        st.warning(
            f"⚠️ 即将**全量覆盖**现有数据，导入 **{len(positions)}** 条持仓、"
            f"**{len(liabilities)}** 条负债。此操作不可撤销。",
            icon="⚠️",
        )
        if st.button("确认覆盖并导入", type="primary", key="confirm_import"):
            result = import_to_db(portfolio_id, positions, liabilities)
            st.success(result)
            st.rerun()
