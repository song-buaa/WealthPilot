"""
投资纪律 — WealthPilot Risk & Decision Engine
基于《投资纪律手册 v1.2》实现，严格执行所有规则，禁止修改或绕过任何约束。
"""

from __future__ import annotations

import re
import streamlit as st
import pandas as pd
from datetime import date, datetime
from typing import Optional

from app.models import Position, Liability, get_session
from app.state import portfolio_id
from app.discipline.config import RULES
from app.discipline.models import (
    PortfolioState, PositionState, MarketContext,
    UserState, TradeAction,
)
from app.discipline.engine_runner import evaluate_action


# ─────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────

_ASSET_CLASS_MAP = {
    "权益": "equity",
    "固收": "fixed_income",
    "货币": "cash",
    "另类": "alternatives",
    "衍生": "leverage_etf",
}

_LEVERAGE_ETF_KEYWORDS = ["杠杆", "TQQQ", "SOXL", "UPRO", "TECL", "LABU", "FNGU"]

# 规则1 Level 0 自动检测关键词
_OPTIONS_KEYWORDS = ["期权", "认购", "认沽", "call", "put"]
_MARGIN_KEYWORDS  = ["融资", "融券"]

# 规则3 持仓聚合：银行/三方平台按 name 聚合，券商按 ticker 聚合
_BANK_PLATFORMS = {"招商银行", "建设银行", "支付宝"}

# 建设银行产品分类 → 标准三类映射
_CCB_NAME_MAP = {
    "活钱":   "活钱管理",
    "理财产品": "稳健投资",
    "债券":   "稳健投资",
    "基金":   "进取投资",
}

_VERDICT_LABELS = {
    "BLOCKED":  ("🚫 操作已拦截", "error"),
    "COOLDOWN": ("❄️ 冷却期中", "warning"),
    "PROCEED":  ("✅ 通过审核", "success"),
}

_REC_LABELS = {
    "BUY":    ("🟢 建议买入", "normal"),
    "ADD":    ("🟢 建议加仓", "normal"),
    "HOLD":   ("⚪ 建议持有", "normal"),
    "REDUCE": ("🟡 建议减仓", "normal"),
    "SELL":   ("🔴 建议卖出", "normal"),
}

_EMOTION_OPTIONS = {
    "normal":  "😐 正常",
    "regret":  "😤 不甘心（亏损想翻本）",
    "greed":   "🤑 贪婪（连续盈利飘了）",
    "panic":   "😱 恐慌（跟盘砍仓冲动）",
    "lucky":   "🎲 侥幸（这次不一样）",
}


# ─────────────────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────────────────

def _is_leverage_etf(p: Position) -> bool:
    name = (p.name or "").upper()
    ticker = (p.ticker or "").upper()
    return any(k.upper() in name or k.upper() in ticker for k in _LEVERAGE_ETF_KEYWORDS)


@st.cache_data(ttl=30)
def _load_positions(pid: int) -> list[dict]:
    """从 DB 加载投资持仓，返回序列化 dict 列表（规避 SQLAlchemy session 跨 cache 问题）"""
    session = get_session()
    try:
        rows = session.query(Position).filter_by(portfolio_id=pid, segment="投资").all()
        return [
            {
                "name": p.name,
                "ticker": p.ticker or "",
                "platform": p.platform,
                "asset_class": p.asset_class,
                "market_value_cny": p.market_value_cny or 0.0,
                "profit_loss_rate": p.profit_loss_rate or 0.0,
                "profit_loss_value": p.profit_loss_value or 0.0,
                "is_leverage_etf": _is_leverage_etf(p),
            }
            for p in rows
        ]
    finally:
        session.close()


@st.cache_data(ttl=30)
def _detect_level0_status(pid: int) -> tuple[bool, bool, bool]:
    """
    从 DB 自动检测规则1 Level 0 工具的使用情况。
    返回 (has_credit, has_margin, has_options)

    - has_credit : Liability.purpose == '投资杠杆' 且 amount > 0
    - has_margin : 持仓名称含融资/融券关键词
    - has_options: 持仓名称或代码含期权关键词
    """
    session = get_session()
    try:
        # 信用贷/借贷投资
        has_credit = session.query(Liability).filter(
            Liability.portfolio_id == pid,
            Liability.purpose == "投资杠杆",
            Liability.amount > 0,
        ).count() > 0

        positions = session.query(Position).filter_by(
            portfolio_id=pid, segment="投资"
        ).all()

        def _match(p: Position, keywords: list[str]) -> bool:
            text = f"{p.name or ''} {p.ticker or ''}".lower()
            return any(kw.lower() in text for kw in keywords)

        has_margin  = any(_match(p, _MARGIN_KEYWORDS)  for p in positions)
        has_options = any(_match(p, _OPTIONS_KEYWORDS) for p in positions)

        return has_credit, has_margin, has_options
    finally:
        session.close()


def _aggregate_positions(raw: list[dict]) -> list[dict]:
    """
    规则3 持仓聚合——跨平台合并同一标的/分类：

    - 券商平台（非银行/三方）：按 ticker 合并；无 ticker 时用正则标准化名称查找已知 ticker
    - 银行/支付宝平台：按 name 合并（活钱管理/稳健投资/进取投资 分类汇总）

    名称标准化规则（处理国金证券等平台的名称变体）：
        "理想汽车-W_1" / "理想汽车-W_2" → "理想汽车"
        "理想汽车 (LI)"                  → "理想汽车"

    返回聚合后的列表，每项包含：
        name, ticker, asset_class, market_value_cny,
        is_leverage_etf, platforms（平台列表）, pl_rate（加权盈亏率）
    """

    def _norm(name: str) -> str:
        """标准化名称：去掉序号后缀、港股标识、括号内容"""
        name = re.sub(r'_\d+$', '', name)          # 去掉 _1 _2 等序号
        name = re.sub(r'-W$', '', name)             # 去掉港股 -W 标识
        name = re.sub(r'\s*\(.*?\)\s*$', '', name)  # 去掉括号内容如 (LI)
        return name.strip()

    # 第一遍：建立「标准名 → ticker」映射（仅来自有 ticker 的持仓）
    norm_to_ticker: dict[str, str] = {}
    for r in raw:
        if r["platform"] not in _BANK_PLATFORMS and r["ticker"]:
            norm_to_ticker[_norm(r["name"])] = r["ticker"]

    # 第二遍：聚合
    buckets: dict[str, dict] = {}

    for r in raw:
        is_bank = r["platform"] in _BANK_PLATFORMS
        if is_bank:
            # 建设银行：将产品名称映射到标准三类；其他银行/支付宝直接用 name
            key = _CCB_NAME_MAP.get(r["name"], r["name"]) if r["platform"] == "建设银行" else r["name"]
        elif r["ticker"]:
            key = r["ticker"]
        else:
            # 无 ticker：用标准化名称查已知映射，fallback 到标准化名称本身
            key = norm_to_ticker.get(_norm(r["name"]), _norm(r["name"]))

        if key not in buckets:
            # 显示名：银行用 key（已映射为标准名），券商用标准化名称
            display_name = key if is_bank else _norm(r["name"])
            buckets[key] = {
                "name": display_name,
                "ticker": r["ticker"] or key,
                "asset_class": r["asset_class"],
                "market_value_cny": 0.0,
                "profit_loss_value": 0.0,
                "cost_value": 0.0,
                "is_leverage_etf": r["is_leverage_etf"],
                "platforms": [],
            }

        b = buckets[key]
        b["market_value_cny"] += r["market_value_cny"]
        b["profit_loss_value"] += r.get("profit_loss_value", 0.0)
        b["cost_value"] += r["market_value_cny"] - r.get("profit_loss_value", 0.0)
        if r["platform"] not in b["platforms"]:
            b["platforms"].append(r["platform"])

    result = []
    for b in buckets.values():
        pl_rate = (b["profit_loss_value"] / b["cost_value"] * 100
                   if b["cost_value"] > 0 else 0.0)
        result.append({
            **b,
            "pl_rate": pl_rate,
            "platform_display": " / ".join(b["platforms"]),
        })

    return sorted(result, key=lambda x: -x["market_value_cny"])


def _build_portfolio_state(
    raw: list[dict], portfolio_drawdown_pct: float
) -> PortfolioState:
    total = sum(r["market_value_cny"] for r in raw) or 1.0
    # 规则2：流动性 = 货币 + 固收
    liquidity = sum(r["market_value_cny"] for r in raw if r["asset_class"] in ("货币", "固收"))

    pos_states = []
    for r in raw:
        ac = "leverage_etf" if r["is_leverage_etf"] else _ASSET_CLASS_MAP.get(r["asset_class"], "equity")
        drawdown = (r["profit_loss_rate"] / 100.0) if r["profit_loss_rate"] < 0 else 0.0
        pos_states.append(PositionState(
            symbol=r["ticker"] or r["name"],
            name=r["name"],
            weight=r["market_value_cny"] / total,
            asset_class=ac,
            drawdown_pct=drawdown,
        ))

    return PortfolioState(
        total_assets=total,
        cash_ratio=liquidity / total,
        drawdown_pct=portfolio_drawdown_pct,
        positions=pos_states,
    )


# ─────────────────────────────────────────────────────────
# UI 工具函数
# ─────────────────────────────────────────────────────────

def _status_icon(value: float, ok_threshold: float, warn_threshold: float,
                 higher_is_better: bool = True) -> str:
    if higher_is_better:
        if value >= ok_threshold:
            return "🟢"
        elif value >= warn_threshold:
            return "🟡"
        return "🔴"
    else:
        if value <= ok_threshold:
            return "🟢"
        elif value <= warn_threshold:
            return "🟡"
        return "🔴"


def _render_block_reasons(reasons: list[str]) -> None:
    for r in reasons:
        st.error(r)


def _render_warnings(warnings: list[str]) -> None:
    for w in warnings:
        st.warning(w)


def _render_reasons(reasons: list[str]) -> None:
    for r in reasons:
        st.info(r)


# ─────────────────────────────────────────────────────────
# Tab 1：账户风险仪表盘
# ─────────────────────────────────────────────────────────

def _render_dashboard(
    raw: list[dict],
    portfolio_drawdown_pct: float,
    has_margin: bool = False,
    has_options: bool = False,
    has_credit: bool = False,
) -> None:
    cfg_cb = RULES["portfolio_circuit_breaker"]
    cfg_liq = RULES["liquidity_limits"]
    cfg_pos = RULES["single_asset_limits"]
    cfg_alloc = RULES["asset_allocation_ranges"]

    total = sum(r["market_value_cny"] for r in raw) or 1.0
    cash = sum(r["market_value_cny"] for r in raw if r["asset_class"] == "货币")
    cash_ratio = cash / total

    equity      = sum(r["market_value_cny"] for r in raw if r["asset_class"] == "权益") / total
    fixed       = sum(r["market_value_cny"] for r in raw if r["asset_class"] == "固收") / total
    liquidity_ratio = cash_ratio + fixed   # 规则4：货币+固收
    alts        = sum(r["market_value_cny"] for r in raw if r["asset_class"] == "另类") / total
    derivatives = sum(r["market_value_cny"] for r in raw if r["asset_class"] == "衍生") / total
    etf         = sum(r["market_value_cny"] for r in raw if r["is_leverage_etf"]) / total

    drawdown_abs = abs(portfolio_drawdown_pct)
    circuit_triggered = drawdown_abs >= cfg_cb["drawdown_trigger_pct"]

    # 规则3：跨平台聚合后计算最大单仓
    aggregated = _aggregate_positions(raw)
    max_agg = max(aggregated, key=lambda a: a["market_value_cny"], default=None)
    max_weight = max_agg["market_value_cny"] / total if max_agg else 0.0
    max_name   = max_agg["name"] if max_agg else ""

    # ── 顶部四格指标（仅数值+状态图标，问题统一汇总在下方）──
    alerts: list[tuple[str, str]] = []   # (level, message)  level="error"/"warning"

    c1, c2, c3 = st.columns(3)

    with c1:
        etf_limit = RULES["leverage_limits"]["level_1_max_pct"]
        level0_items = []
        if has_margin:  level0_items.append("融资融券")
        if has_options: level0_items.append("期权")
        if has_credit:  level0_items.append("信用贷")
        level0_violated = bool(level0_items)
        level1_violated = etf > etf_limit

        if level0_violated:
            st.metric("🔴 杠杆工具（规则1）", "Level0 违规",
                      "、".join(level0_items))
            alerts.append(("error",
                f"[规则1·Level0] 持有绝对禁止工具：{'、'.join(level0_items)}。"
                "须立即清除，任何情况不得持有。"))
        elif level1_violated:
            st.metric("🟡 杠杆工具（规则1）", f"ETF {etf*100:.1f}%",
                      f"超过上限 {etf_limit*100:.0f}%")
            alerts.append(("warning",
                f"[规则1·Level1] 杠杆ETF持仓 {etf*100:.1f}%"
                f" 超过上限 {etf_limit*100:.0f}%，须减仓至上限以下。"))
        else:
            st.metric("🟢 杠杆工具（规则1）", f"ETF {etf*100:.1f}%",
                      f"上限 {etf_limit*100:.0f}%")

    with c2:
        icon = _status_icon(max_weight, cfg_pos["warning_position_pct"],
                            cfg_pos["max_position_pct"], higher_is_better=False)
        st.metric(f"{icon} 最大单仓（规则3）",
                  f"{max_weight*100:.1f}%",
                  f"{max_name}  |  上限 {cfg_pos['max_position_pct']*100:.0f}%")
        if max_weight > cfg_pos["max_position_pct"]:
            alerts.append(("error",
                f"[规则3·超限] {max_name} 跨平台合并仓位 {max_weight*100:.1f}%"
                f" 超过硬性上限 {cfg_pos['max_position_pct']*100:.0f}%，须立即减仓至上限以下。"))
        elif max_weight >= cfg_pos["warning_position_pct"]:
            alerts.append(("warning",
                f"[规则3·警戒区] {max_name} 跨平台合并仓位 {max_weight*100:.1f}%"
                f" 已进入警戒区（≥{cfg_pos['warning_position_pct']*100:.0f}%），禁止继续加仓该标的。"))

    with c3:
        liq_warn = cfg_liq["min_cash_pct"] * 1.25   # 25% 缓冲预警线
        icon = _status_icon(liquidity_ratio, liq_warn,
                            cfg_liq["min_cash_pct"], higher_is_better=True)
        st.metric(f"{icon} 流动性（货币+固收，规则4）",
                  f"{liquidity_ratio*100:.1f}%",
                  f"最低要求 {cfg_liq['min_cash_pct']*100:.0f}%")
        if liquidity_ratio < cfg_liq["min_cash_pct"]:
            alerts.append(("error",
                f"[规则4·流动性] 当前流动性（货币+固收）{liquidity_ratio*100:.1f}%"
                f" 低于最低要求 {cfg_liq['min_cash_pct']*100:.0f}%，禁止继续买入。"))
        elif liquidity_ratio < liq_warn:
            alerts.append(("warning",
                f"[规则4·流动性] 当前流动性（货币+固收）{liquidity_ratio*100:.1f}%"
                f" 接近下限，谨慎操作，避免进一步压缩子弹。"))

    # ── 汇总提示区 ─────────────────────────────────────────
    if alerts:
        for level, msg in alerts:
            if level == "error":
                st.error(msg)
            else:
                st.warning(msg)
    else:
        st.success("✅ 所有风控指标正常，账户当前无违规")

    st.divider()

    # ── 资产配置 & 持仓集中度表 ───────────────────────────────
    col_left, col_right = st.columns([2, 3])

    with col_left:
        st.subheader("资产配置（规则2）", divider=False)
        cash_amount = cash  # 货币绝对金额
        mn_min = cfg_alloc["monetary_min_amount"]
        mn_max = cfg_alloc["monetary_max_amount"]
        alloc_rows = [
            {"资产类别": "货币",
             "当前": f"{cash_amount/10000:.1f}万元",
             "目标区间": f"{mn_min//10000:.0f}万 ~ {mn_max//10000:.0f}万元",
             "状态": "🟢" if mn_min <= cash_amount <= mn_max else "⚠️"},
            {"资产类别": "固收",
             "当前": f"{fixed*100:.1f}%",
             "目标区间": f"{cfg_alloc['fixed_income_min']*100:.0f}% ~ {cfg_alloc['fixed_income_max']*100:.0f}%",
             "状态": "🟢" if cfg_alloc["fixed_income_min"] <= fixed <= cfg_alloc["fixed_income_max"] else "⚠️"},
            {"资产类别": "权益",
             "当前": f"{equity*100:.1f}%",
             "目标区间": f"{cfg_alloc['equity_min']*100:.0f}% ~ {cfg_alloc['equity_max']*100:.0f}%",
             "状态": "🟢" if cfg_alloc["equity_min"] <= equity <= cfg_alloc["equity_max"] else "⚠️"},
            {"资产类别": "另类",
             "当前": f"{alts*100:.1f}%",
             "目标区间": f"≤ {cfg_alloc['alternatives_max']*100:.0f}%",
             "状态": "🟢" if alts <= cfg_alloc["alternatives_max"] else "⚠️"},
            {"资产类别": "衍生",
             "当前": f"{derivatives*100:.1f}%",
             "目标区间": f"≤ {cfg_alloc['derivatives_max']*100:.0f}%",
             "状态": "🟢" if derivatives <= cfg_alloc["derivatives_max"] else "⚠️"},
        ]
        st.dataframe(pd.DataFrame(alloc_rows), use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("持仓集中度（规则3）", divider=False)
        rows = []
        for a in aggregated:
            w = a["market_value_cny"] / total
            if w > cfg_pos["max_position_pct"]:
                status = "🚨 超限"
            elif w >= cfg_pos["warning_position_pct"]:
                status = "⚠️ 警戒区"
            else:
                status = "🟢 安全"
            rows.append({
                "持仓名称": a["name"],
                "仓位": f"{w*100:.1f}%",
                "状态": status,
                "盈亏%": f"{a['pl_rate']:+.1f}%",
                "持仓平台": a["platform_display"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────
# Tab 2：操作评估器
# ─────────────────────────────────────────────────────────

def _render_evaluator(raw: list[dict], portfolio_drawdown_pct: float) -> None:
    total = sum(r["market_value_cny"] for r in raw) or 1.0
    position_names = [r["name"] for r in raw]

    col_input, col_result = st.columns([1, 1], gap="large")

    # ── 左列：输入 ────────────────────────────────────────
    with col_input:
        st.markdown("#### 操作参数")

        # 标的选择
        mode = st.radio("标的来源", ["从持仓中选择", "输入新标的"], horizontal=True,
                        key="disc_mode")
        if mode == "从持仓中选择" and position_names:
            selected_name = st.selectbox("选择持仓", position_names, key="disc_symbol_select")
            pos_data = next(r for r in raw if r["name"] == selected_name)
        else:
            custom_name = st.text_input("标的名称", placeholder="如：美团-W", key="disc_custom_name")
            custom_ticker = st.text_input("代码（可选）", placeholder="如：3690.HK", key="disc_custom_ticker")
            pos_data = {
                "name": custom_name or "新标的",
                "ticker": custom_ticker,
                "asset_class": "权益",
                "market_value_cny": 0.0,
                "profit_loss_rate": 0.0,
                "is_leverage_etf": False,
            }

        st.divider()

        # 操作参数
        action_type = st.selectbox("操作类型", ["BUY", "ADD", "SELL", "REDUCE"],
                                   format_func=lambda x: {
                                       "BUY": "BUY 买入（新建仓）",
                                       "ADD": "ADD 加仓",
                                       "SELL": "SELL 卖出（清仓/大幅减仓）",
                                       "REDUCE": "REDUCE 减仓",
                                   }[x], key="disc_action")

        amount_pct = st.slider(
            "本次操作金额（占总投资性资产 %）", 1, 30, 5, key="disc_amount"
        ) / 100.0

        st.caption(f"≈ 人民币 {total * amount_pct:,.0f} 元")

        # 工具类型
        with st.expander("工具类型（默认：普通股/ETF）"):
            is_margin = st.checkbox("涉及融资融券", key="disc_margin")
            is_options = st.checkbox("涉及期权", key="disc_options")
            is_credit = st.checkbox("使用信用贷/借贷资金", key="disc_credit")
            is_etf = st.checkbox("为杠杆ETF（如 TQQQ）", key="disc_etf",
                                 value=pos_data.get("is_leverage_etf", False))

        st.divider()
        st.markdown("#### 市场环境")

        trend = st.select_slider(
            "市场趋势", ["down", "sideways", "up"],
            format_func=lambda x: {"down": "下跌趋势", "sideways": "震荡", "up": "上涨趋势"}[x],
            key="disc_trend"
        )
        major_neg = st.checkbox("近期发生重大利空事件（如产品失败/财报暴雷）",
                                key="disc_major_neg")

        st.divider()
        st.markdown("#### 心理状态")

        emotion = st.selectbox(
            "当前情绪状态",
            list(_EMOTION_OPTIONS.keys()),
            format_func=lambda x: _EMOTION_OPTIONS[x],
            key="disc_emotion"
        )

        daily_drop = st.slider(
            "今日账户净值变动 %", -15.0, 5.0, 0.0, 0.1, key="disc_daily_drop"
        ) / 100.0

        cooldown_active = st.checkbox("已处于冷却期", key="disc_cooldown")
        cooldown_until_input: Optional[datetime] = None
        if cooldown_active:
            cd_dt = st.datetime_input("冷却期结束时间", value=datetime.now(),
                                      key="disc_cooldown_until") if hasattr(st, "datetime_input") else None
            cooldown_until_input = cd_dt

        st.divider()
        st.markdown("#### 标的详细参数（可选）")

        current_weight = (pos_data["market_value_cny"] / total) if total > 0 else 0.0
        is_core = st.checkbox(
            "核心底仓（持有1年以上）", key="disc_core",
            help="规则9：核心持仓永远保留10%~20%底仓"
        )
        logic_ok = st.checkbox(
            "长期逻辑完好（未发生逻辑破坏）", value=True, key="disc_logic",
            help="规则7/5：逻辑破坏 = 核心产品竞争力消失/商业模式根本变化/管理层诚信问题"
        )
        target_weight_pct = st.number_input(
            "目标仓位 %（0 = 未设定，跳过偏离度检查）",
            min_value=0.0, max_value=100.0, value=0.0, step=1.0, key="disc_target"
        ) / 100.0

        has_last_add = st.checkbox("记录上次加仓日期（规则6 间隔检查）", key="disc_has_add_date")
        last_add: Optional[date] = None
        if has_last_add:
            last_add = st.date_input("上次加仓日期", value=date.today(), key="disc_last_add")

        t_drawdown = st.slider(
            "做T参考回调幅度 %（卖出后已回调多少；0 = 未使用做T）",
            -30, 0, 0, key="disc_t_drawdown"
        ) / 100.0

    # ── 右列：评估结果 ────────────────────────────────────
    with col_result:
        st.markdown("#### 评估结果")

        run_eval = st.button("🔍 立即评估", type="primary", use_container_width=True,
                             key="disc_run")

        if run_eval or st.session_state.get("disc_last_result"):
            if run_eval:
                # 构建引擎输入
                pos_state = PositionState(
                    symbol=pos_data["ticker"] or pos_data["name"],
                    name=pos_data["name"],
                    weight=current_weight,
                    target_weight=target_weight_pct,
                    drawdown_pct=(pos_data["profit_loss_rate"] / 100.0)
                                 if pos_data["profit_loss_rate"] < 0 else 0.0,
                    asset_class="leverage_etf" if is_etf
                                else _ASSET_CLASS_MAP.get(pos_data["asset_class"], "equity"),
                    is_core_holding=is_core,
                    last_add_date=last_add,
                    logic_intact=logic_ok,
                )

                portfolio_state = _build_portfolio_state(raw, portfolio_drawdown_pct)

                # 用评估器输入的详细参数覆盖自动构建的对应持仓
                portfolio_state.positions = [
                    p if p.symbol != pos_state.symbol else pos_state
                    for p in portfolio_state.positions
                ]
                if pos_state.symbol not in {p.symbol for p in portfolio_state.positions}:
                    portfolio_state.positions.append(pos_state)

                market_ctx = MarketContext(
                    trend=trend,
                    major_negative_event=major_neg,
                )

                user_st = UserState(
                    emotional_state=emotion,
                    cooldown_active=cooldown_active,
                    cooldown_until=cooldown_until_input,
                    daily_nav_drop_pct=daily_drop,
                )

                action = TradeAction(
                    action_type=action_type,
                    symbol=pos_state.symbol,
                    amount_pct=amount_pct,
                    is_margin_trading=is_margin,
                    is_options=is_options,
                    is_credit_loan=is_credit,
                    is_leverage_etf=is_etf,
                )

                result = evaluate_action(
                    portfolio_state, pos_state, market_ctx, user_st, action,
                    t_strategy_drawdown=t_drawdown,
                )
                st.session_state["disc_last_result"] = result

            result = st.session_state["disc_last_result"]
            label, kind = _VERDICT_LABELS[result.final_verdict]

            # 最终裁定
            if kind == "error":
                st.error(f"**{label}**")
            elif kind == "warning":
                st.warning(f"**{label}**")
            else:
                st.success(f"**{label}**")

            # Risk Engine
            st.markdown("---")
            risk_icon = {"ALLOW": "🟢", "WARNING": "🟡", "BLOCK": "🔴"}[result.risk.status]
            st.markdown(f"**{risk_icon} Risk Engine：{result.risk.status}**")
            if result.risk.messages:
                _render_block_reasons(result.risk.messages)
            if result.risk.warnings:
                _render_warnings(result.risk.warnings)
            if not result.risk.messages and not result.risk.warnings:
                st.caption("全部硬性约束通过")

            # Psychology Engine
            st.markdown("---")
            psy_icon = "❄️" if result.psychology.status == "COOLDOWN" else "🟢"
            st.markdown(f"**{psy_icon} Psychology Engine：{result.psychology.status}**")
            if result.psychology.triggered_reasons:
                for r in result.psychology.triggered_reasons:
                    st.warning(r)
                if result.psychology.cooldown_until:
                    st.caption(f"冷却期至：{result.psychology.cooldown_until.strftime('%Y-%m-%d %H:%M')}")
            else:
                st.caption("情绪状态正常")

            # Decision Engine
            if result.allowed:
                st.markdown("---")
                rec = result.decision.recommendation
                rec_label, _ = _REC_LABELS.get(rec, (rec, "normal"))
                st.markdown(f"**Decision Engine：{rec_label}**")
                if result.decision.reasons:
                    _render_reasons(result.decision.reasons)
                if result.decision.warnings:
                    _render_warnings(result.decision.warnings)
        else:
            st.caption("填写左侧参数后点击「立即评估」")


# ─────────────────────────────────────────────────────────
# Tab 3：交易前检查清单（规则11）
# ─────────────────────────────────────────────────────────

def _render_checklist(raw: list[dict], portfolio_drawdown_pct: float) -> None:
    st.markdown(
        "#### 规则11 — 交易前强制检查清单\n"
        "> 每次下单前必须通过以下全部 9 项检查。**任意 1 项不通过 = 禁止交易。**"
    )

    total = sum(r["market_value_cny"] for r in raw) or 1.0
    liquidity_cl = sum(r["market_value_cny"] for r in raw
                       if r["asset_class"] in ("货币", "固收")) / total   # 规则4：货币+固收
    max_w = max((r["market_value_cny"] / total for r in raw), default=0)
    cfg_pos = RULES["single_asset_limits"]
    cfg_cb  = RULES["portfolio_circuit_breaker"]

    # 自动推断部分检查项
    auto_checks = {
        1: max_w <= cfg_pos["max_position_pct"],
        2: True,   # 用户手动确认
        3: True,   # 用户手动确认
        4: True,   # 用户手动确认
        5: liquidity_cl >= RULES["liquidity_limits"]["min_cash_pct"],
        6: True,   # 用户手动确认
        7: True,   # 用户手动确认
        8: True,   # 用户手动确认
        9: True,   # 用户手动确认
    }

    items = [
        (1, "买入后该标的仓位不超过40%",
         f"当前最大单仓 {max_w*100:.1f}%（上限40%）",
         auto_checks[1]),
        (2, "不涉及Level0杠杆（无融资、无期权、无借贷）",
         "需手动确认", None),
        (3, "杠杆ETF总持仓 ≤ 5%",
         "需手动确认", None),
        (4, "单次加仓 ≤ 总资产10%，且非一次性建仓",
         "需手动确认", None),
        (5, "操作后流动性资金（货币+固收）比例仍 ≥ 20%",
         f"当前流动性 {liquidity_cl*100:.1f}%", auto_checks[5]),
        (6, "未处于情绪冷却状态（无不甘心/贪婪/恐慌/侥幸）",
         "需手动确认", None),
        (7, "基于长期逻辑，非短期追涨杀跌",
         "需手动确认", None),
        (8, "若再跌10%，心理和仓位上都可以承受",
         "需手动确认", None),
        (9, "符合整体资产配置策略，不违反偏离度和跨资产比例约束",
         "需手动确认", None),
    ]

    # 账户熔断附加检查
    circuit_ok = abs(portfolio_drawdown_pct) < cfg_cb["drawdown_trigger_pct"]

    results = {}
    st.markdown("---")

    for num, desc, hint, auto_val in items:
        col_num, col_check, col_hint = st.columns([0.3, 3, 2])
        with col_num:
            st.markdown(f"**#{num}**")
        with col_check:
            if auto_val is not None:
                # 自动推断结果，不允许用户修改
                icon = "✅" if auto_val else "❌"
                st.markdown(f"{icon} {desc}")
                results[num] = auto_val
            else:
                checked = st.checkbox(desc, key=f"cl_{num}")
                results[num] = checked
        with col_hint:
            st.caption(hint)

    # 账户熔断额外检查（BUY/ADD 专用）
    st.markdown("---")
    st.markdown("**账户级熔断（加仓专用）**")
    cb_col1, cb_col2 = st.columns([3, 2])
    with cb_col1:
        icon = "✅" if circuit_ok else "❌"
        st.markdown(f"{icon} 账户总回撤 < 25%（当前未触发熔断）")
    with cb_col2:
        st.caption(f"当前回撤 {abs(portfolio_drawdown_pct)*100:.1f}%（阈值25%）")

    st.markdown("---")
    all_pass = all(results.values()) and circuit_ok

    if all_pass:
        st.success("✅ 全部 9 项通过，账户未熔断。纪律允许本次操作，请在操作评估器中进一步检验。")
    else:
        failed = [str(k) for k, v in results.items() if not v]
        if not circuit_ok:
            failed.append("账户熔断")
        st.error(f"❌ 以下检查项未通过：{', '.join(failed)}。禁止本次交易。")


# ─────────────────────────────────────────────────────────
# Tab 2（新）：交易前评估 — 自然语言解析 + 自动清单检查
# ─────────────────────────────────────────────────────────

_PRE_EVAL_FORM_KEYS = [
    "pre_eval_mode", "pre_eval_symbol", "pre_eval_custom_name",
    "pre_eval_action", "pre_eval_amount", "pre_eval_emotion",
    "pre_eval_logic", "pre_eval_margin", "pre_eval_options",
    "pre_eval_credit", "pre_eval_etf",
    "pre_eval_confirm_8", "pre_eval_confirm_9",
]


def _parse_trade_intent(text: str, raw: list[dict], total_assets: float) -> dict:
    """
    从自然语言描述中提取交易意图的结构化字段。
    MVP：关键词规则实现。返回结构固定，后续可直接替换为 LLM 调用。

    输入：(自然语言文本, 当前持仓列表, 总资产)
    输出：dict，字段见下方注释
    """
    result: dict = {
        "name":            None,     # str | None  标的名称
        "action_type":     None,     # BUY | ADD | SELL | REDUCE | None
        "amount_cny":      None,     # float | None  操作金额（元）
        "amount_pct":      None,     # float | None  占总资产比例
        "is_leverage_etf": False,
        "is_margin":       False,
        "is_options":      False,
        "is_credit":       False,
        "emotion":         "normal", # normal | regret | greed | panic | lucky
        "logic_based":     None,     # True | False | None（无法判断）
        "major_neg_event": False,
        "trend":           "sideways",  # up | sideways | down
        "raw_text":        text,
        "unresolved":      [],
    }

    # 操作类型
    if any(k in text for k in ["加仓", "补仓", "补一点", "分批补", "继续买"]):
        result["action_type"] = "ADD"
    elif any(k in text for k in ["清仓", "全部卖", "全卖", "清掉"]):
        result["action_type"] = "SELL"
    elif any(k in text for k in ["减仓", "减一点", "卖一点", "先减", "部分卖"]):
        result["action_type"] = "REDUCE"
    elif any(k in text for k in ["买入", "新建仓", "建仓", "首次买", "第一次买"]):
        result["action_type"] = "BUY"

    # 标的名称——从当前持仓中匹配（长名优先）
    position_names = [r["name"] for r in raw]
    for name in sorted(position_names, key=len, reverse=True):
        if name in text or name.lower() in text.lower():
            result["name"] = name
            break

    # 金额提取
    for pat, mult in [
        (r"(\d+(?:\.\d+)?)\s*万元?", 10_000),
        (r"(\d+(?:\.\d+)?)\s*千元?",  1_000),
        (r"(\d+(?:\.\d+)?)\s*百元?",    100),
        (r"(\d+(?:\.\d+)?)\s*元",          1),
    ]:
        m = re.search(pat, text)
        if m:
            result["amount_cny"] = float(m.group(1)) * mult
            if total_assets > 0:
                result["amount_pct"] = result["amount_cny"] / total_assets
            break
    if result["amount_pct"] is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if m:
            result["amount_pct"] = float(m.group(1)) / 100
            result["amount_cny"] = result["amount_pct"] * total_assets

    # 情绪
    if any(k in text for k in ["不甘心", "翻本", "回血", "亏了想", "亏损中"]):
        result["emotion"] = "regret"
    elif any(k in text for k in ["恐慌", "吓到了", "割肉冲动", "怕了", "慌了"]):
        result["emotion"] = "panic"
    elif any(k in text for k in ["连续涨", "涨不停", "没有上限", "飘了"]):
        result["emotion"] = "greed"
    elif any(k in text for k in ["赌一把", "这次不一样", "押注", "碰运气"]):
        result["emotion"] = "lucky"

    # 长期逻辑
    if any(k in text for k in ["长期", "基本面", "看好", "长逻辑", "赛道", "成长", "价值"]):
        result["logic_based"] = True
    elif any(k in text for k in ["短线", "短期", "博反弹", "追涨", "跟风", "消息面"]):
        result["logic_based"] = False

    # 市场环境
    if any(k in text for k in ["下跌", "跌了", "回调", "低位", "底部", "跌幅"]):
        result["trend"] = "down"
    elif any(k in text for k in ["上涨", "涨了", "高位", "新高", "大涨"]):
        result["trend"] = "up"

    # 重大利空
    if any(k in text for k in ["财报暴雷", "产品失败", "利空", "暴雷", "丑闻", "造假"]):
        result["major_neg_event"] = True

    # 杠杆工具
    if any(k.upper() in text.upper() for k in
           ["TQQQ", "SOXL", "UPRO", "TECL", "LABU", "FNGU", "杠杆ETF"]):
        result["is_leverage_etf"] = True
    if any(k in text for k in ["融资", "融券"]):
        result["is_margin"] = True
    if any(k in text for k in ["期权", "认购", "认沽"]):
        result["is_options"] = True
    if any(k in text for k in ["借贷", "信用贷", "消费贷", "贷款买"]):
        result["is_credit"] = True

    # 标注无法提取的字段
    if result["name"] is None:
        result["unresolved"].append("标的名称")
    if result["action_type"] is None:
        result["unresolved"].append("操作类型")
    if result["amount_pct"] is None:
        result["unresolved"].append("操作金额")

    return result


def _render_eval_result_section(result) -> None:
    """引擎评估结果展示（从 _render_evaluator 提取，供新 Tab 复用）"""
    label, kind = _VERDICT_LABELS[result.final_verdict]
    if kind == "error":
        st.error(f"**{label}**")
    elif kind == "warning":
        st.warning(f"**{label}**")
    else:
        st.success(f"**{label}**")

    risk_icon = {"ALLOW": "🟢", "WARNING": "🟡", "BLOCK": "🔴"}[result.risk.status]
    st.markdown(f"**{risk_icon} Risk Engine：{result.risk.status}**")
    if result.risk.messages:
        _render_block_reasons(result.risk.messages)
    if result.risk.warnings:
        _render_warnings(result.risk.warnings)
    if not result.risk.messages and not result.risk.warnings:
        st.caption("全部硬性约束通过")

    st.markdown("---")
    psy_icon = "❄️" if result.psychology.status == "COOLDOWN" else "🟢"
    st.markdown(f"**{psy_icon} Psychology Engine：{result.psychology.status}**")
    if result.psychology.triggered_reasons:
        for r in result.psychology.triggered_reasons:
            st.warning(r)
    else:
        st.caption("情绪状态正常")

    if result.allowed:
        st.markdown("---")
        rec = result.decision.recommendation
        rec_label, _ = _REC_LABELS.get(rec, (rec, "normal"))
        st.markdown(f"**Decision Engine：{rec_label}**")
        if result.decision.reasons:
            _render_reasons(result.decision.reasons)
        if result.decision.warnings:
            _render_warnings(result.decision.warnings)


def _render_checklist_auto(
    raw: list[dict], portfolio_drawdown_pct: float, ctx: dict
) -> None:
    """
    交易前检查清单（规则11）——自动判断版。
    能从账户数据 / 交易参数自动判断的直接给结论；
    无法自动判断的仅要求一次性确认，不再逐项手动勾选。
    """
    total = sum(r["market_value_cny"] for r in raw) or 1.0
    liquidity_cl = sum(
        r["market_value_cny"] for r in raw if r["asset_class"] in ("货币", "固收")
    ) / total

    action_type = ctx.get("action_type", "BUY")
    amount_pct  = ctx.get("amount_pct", 0.05)
    is_margin   = ctx.get("is_margin", False)
    is_opts     = ctx.get("is_options", False)
    is_credit   = ctx.get("is_credit", False)
    is_etf      = ctx.get("is_etf", False)
    emotion     = ctx.get("emotion", "normal")
    logic_based = ctx.get("logic_based", True)
    pos_data    = ctx.get("pos_data", {})

    cfg_pos   = RULES["single_asset_limits"]
    cfg_cb    = RULES["portfolio_circuit_breaker"]
    cfg_liq   = RULES["liquidity_limits"]
    etf_limit = RULES["leverage_limits"]["level_1_max_pct"]

    is_buy = action_type in ("BUY", "ADD")
    current_weight   = (pos_data.get("market_value_cny", 0) / total) if total > 0 else 0.0
    projected_weight = current_weight + amount_pct if is_buy else current_weight - amount_pct
    existing_etf     = sum(r["market_value_cny"] for r in raw if r["is_leverage_etf"]) / total
    projected_etf    = (existing_etf + amount_pct) if (is_etf and is_buy) else existing_etf
    projected_liq    = (liquidity_cl - amount_pct) if is_buy else liquidity_cl
    circuit_ok       = abs(portfolio_drawdown_pct) < cfg_cb["drawdown_trigger_pct"]

    no_lv0     = not (is_margin or is_opts or is_credit)
    lv0_detail = ("已自动判断" if no_lv0
                  else f"检测到：{'融资 ' if is_margin else ''}{'期权 ' if is_opts else ''}{'借贷' if is_credit else ''}")

    # (num, 描述, 提示, auto_result)  auto_result: True=通过 / False=失败 / None=需确认
    items = [
        (1, "买入后该标的仓位不超过 40%",
         f"操作后预计 {projected_weight*100:.1f}%（上限 40%）",
         projected_weight <= cfg_pos["max_position_pct"]),

        (2, "不涉及 Level0 杠杆（无融资、无期权、无借贷）",
         lv0_detail, no_lv0),

        (3, "杠杆 ETF 总持仓 ≤ 5%",
         f"操作后预计 {projected_etf*100:.1f}%（上限 5%）",
         projected_etf <= etf_limit),

        (4, "单次加仓 ≤ 总资产 10%",
         f"本次 {amount_pct*100:.1f}%（上限 10%）；分批执行请自行保证",
         (amount_pct <= RULES["position_sizing"]["max_single_add_pct"]) if is_buy else True),

        (5, "操作后流动性资金（货币+固收）比例仍 ≥ 20%",
         f"操作后预计 {projected_liq*100:.1f}%（要求 ≥ 20%）",
         (projected_liq >= cfg_liq["min_cash_pct"]) if is_buy else True),

        (6, "未处于情绪冷却状态",
         f"当前情绪：{_EMOTION_OPTIONS.get(emotion, emotion)}",
         emotion == "normal"),

        (7, "基于长期逻辑，非短期追涨杀跌",
         "已从描述中推断" if logic_based is not None else "无法从描述中判断，请自行确认",
         logic_based),

        (8, "若再跌 10%，心理和仓位上都可以承受",
         "需主观确认", None),

        (9, "符合整体资产配置策略，不违反偏离度约束",
         "请对照仪表盘「资产配置」表自行确认", None),
    ]

    st.markdown("#### ✅ 交易前检查清单（规则11）")
    st.caption("可自动判断的项已标注结果；⚠️ 项请在下方确认。")
    st.markdown("---")

    hard_fail    = False
    need_confirm = []

    for num, desc, hint, auto_val in items:
        c_num, c_check, c_hint = st.columns([0.3, 3, 2])
        with c_num:
            st.markdown(f"**#{num}**")
        with c_check:
            if auto_val is True:
                st.markdown(f"✅ {desc}")
            elif auto_val is False:
                st.markdown(f"❌ {desc}")
                hard_fail = True
            else:
                st.markdown(f"⚠️ {desc}")
                need_confirm.append(num)
        with c_hint:
            st.caption(hint)

    c_num, c_check, c_hint = st.columns([0.3, 3, 2])
    with c_num:
        st.markdown("**+**")
    with c_check:
        cb_icon = "✅" if circuit_ok else "❌"
        st.markdown(f"{cb_icon} 账户总回撤 < 25%（加仓专用）")
        if not circuit_ok:
            hard_fail = True
    with c_hint:
        st.caption(f"当前回撤 {abs(portfolio_drawdown_pct)*100:.1f}%（阈值 25%）")

    st.markdown("---")

    if hard_fail:
        st.error("❌ 存在检查项未通过，**禁止本次交易**。")
        return

    if not need_confirm:
        st.success("✅ 全部 9 项 + 熔断检查通过，纪律允许本次操作。")
        return

    nums_str = "、".join(f"#{n}" for n in need_confirm)
    st.warning(f"⚠️ 自动检查已通过。以下项需主观确认：{nums_str}")
    confirm_all = True
    if 8 in need_confirm:
        confirm_all = st.checkbox(
            "我确认：若该标的再跌 10%，我的心理和仓位都能承受",
            key="pre_eval_confirm_8",
        ) and confirm_all
    if 9 in need_confirm:
        confirm_all = st.checkbox(
            "我确认：本次操作符合整体资产配置策略（已对照仪表盘确认）",
            key="pre_eval_confirm_9",
        ) and confirm_all
    if confirm_all:
        st.success("✅ 全部确认通过，纪律允许本次操作。")


def _render_pre_trade_eval(raw: list[dict], portfolio_drawdown_pct: float) -> None:
    """
    Tab 2（新）：交易前评估
    流程：自然语言描述 → 关键词解析 → 参数确认 → 纪律评估 + 自动清单检查
    """
    total = sum(r["market_value_cny"] for r in raw) or 1.0
    position_names = [r["name"] for r in raw]

    # ── 自然语言输入 ──────────────────────────────────────
    st.markdown("#### 描述你想做的交易")
    st.caption("用自己的话描述，系统自动提取关键信息，并逐项对照投资纪律检查。")

    nl_text = st.text_area(
        "交易描述",
        placeholder="例如：我想加仓理想汽车，大概买10万元，最近跌了不少，长期逻辑没变，想分批补仓",
        height=90,
        label_visibility="collapsed",
        key="pre_eval_nl_text",
    )

    col_parse, col_clear = st.columns([4, 1])
    with col_parse:
        parse_btn = st.button(
            "🔍 解析交易意图", type="primary",
            use_container_width=True, key="pre_eval_parse",
            # 不用 disabled 控制：text_area 只在失去焦点后才更新 nl_text，
            # 导致用户输入中按钮仍呈灰色。空输入由下方 if 条件拦截。
        )
    with col_clear:
        if st.button("重置", use_container_width=True, key="pre_eval_clear"):
            # 将文本框置空（比 pop 更可靠：同一轮渲染中文本框已渲染完毕，
            # 设置 session_state 会在下一次用户交互时生效）
            st.session_state["pre_eval_nl_text"] = ""
            for k in _PRE_EVAL_FORM_KEYS + ["pre_eval_parsed", "pre_eval_result", "pre_eval_context"]:
                st.session_state.pop(k, None)
            # 不调用 st.rerun()，让后续渲染继续（parsed 已清空，下方会显示 info 提示）

    if parse_btn and not (nl_text or "").strip():
        st.warning("请先在上方输入交易描述，再点击解析。")

    if parse_btn and (nl_text or "").strip():
        parsed = _parse_trade_intent(nl_text, raw, total)
        st.session_state["pre_eval_parsed"] = parsed
        # 在渲染表单 widget 之前清除旧 key，使它们在本轮渲染中使用 value 参数作为默认值
        # 无需 st.rerun()，Streamlit 同一轮渲染中 key 缺失时会自动使用 value 默认值
        for k in _PRE_EVAL_FORM_KEYS + ["pre_eval_result", "pre_eval_context"]:
            st.session_state.pop(k, None)

    parsed: dict = st.session_state.get("pre_eval_parsed", {})

    if not parsed:
        st.info("💡 在上方输入你的交易计划，点击「解析」后系统自动填充参数，并对照投资纪律逐项检查。")
        return

    st.divider()

    # ── 解析警告 ──────────────────────────────────────────
    if parsed.get("unresolved"):
        st.warning(
            "⚠️ 以下信息未能从描述中提取，请在下方手动补充：**"
            + "、".join(parsed["unresolved"]) + "**"
        )

    # ── 参数确认 / 调整表单 ───────────────────────────────
    st.markdown("#### 📋 确认 / 调整参数")
    col_a, col_b = st.columns(2)

    with col_a:
        default_mode = (
            "从持仓中选择"
            if parsed.get("name") and parsed["name"] in position_names
            else "输入新标的"
        )
        mode_sel = st.radio(
            "标的来源", ["从持仓中选择", "输入新标的"],
            index=0 if default_mode == "从持仓中选择" else 1,
            horizontal=True, key="pre_eval_mode",
        )

        if mode_sel == "从持仓中选择" and position_names:
            default_idx = (
                position_names.index(parsed["name"])
                if parsed.get("name") in position_names else 0
            )
            selected_name = st.selectbox(
                "选择持仓", position_names,
                index=default_idx, key="pre_eval_symbol",
            )
            pos_data = next(r for r in raw if r["name"] == selected_name)
        else:
            custom_name = st.text_input(
                "标的名称", value=parsed.get("name") or "",
                placeholder="如：苹果 / Apple Inc.", key="pre_eval_custom_name",
            )
            pos_data = {
                "name": custom_name or "新标的",
                "ticker": "",
                "asset_class": "权益",
                "market_value_cny": 0.0,
                "profit_loss_rate": 0.0,
                "is_leverage_etf": parsed.get("is_leverage_etf", False),
            }

        _action_opts = ["BUY", "ADD", "SELL", "REDUCE"]
        _action_labels = {
            "BUY":    "BUY 买入（新建仓）",
            "ADD":    "ADD 加仓",
            "SELL":   "SELL 卖出（清仓）",
            "REDUCE": "REDUCE 减仓",
        }
        default_action_idx = (
            _action_opts.index(parsed["action_type"])
            if parsed.get("action_type") in _action_opts else 1
        )
        action_type = st.selectbox(
            "操作类型", _action_opts,
            format_func=lambda x: _action_labels[x],
            index=default_action_idx, key="pre_eval_action",
        )

    with col_b:
        default_pct_int = max(1, min(30, int(round((parsed.get("amount_pct") or 0.05) * 100))))
        amount_pct = st.slider(
            "操作金额（占总资产 %）", 1, 30, default_pct_int,
            key="pre_eval_amount",
        ) / 100.0
        st.caption(f"≈ 人民币 {total * amount_pct:,.0f} 元")

        _emotion_opts = list(_EMOTION_OPTIONS.keys())
        default_emotion_idx = (
            _emotion_opts.index(parsed.get("emotion", "normal"))
            if parsed.get("emotion") in _emotion_opts else 0
        )
        emotion = st.selectbox(
            "当前情绪状态", _emotion_opts,
            format_func=lambda x: _EMOTION_OPTIONS[x],
            index=default_emotion_idx, key="pre_eval_emotion",
        )

        logic_ok = st.checkbox(
            "长期逻辑完好（未发生逻辑破坏）",
            value=parsed.get("logic_based") if parsed.get("logic_based") is not None else True,
            key="pre_eval_logic",
            help="规则5/7：逻辑破坏 = 核心产品竞争力消失/商业模式根本变化/管理层诚信问题",
        )

    with st.expander("🔧 工具类型（默认：普通股/ETF）"):
        is_margin = st.checkbox("涉及融资融券",      value=parsed.get("is_margin", False),   key="pre_eval_margin")
        is_opts   = st.checkbox("涉及期权",           value=parsed.get("is_options", False),  key="pre_eval_options")
        is_credit = st.checkbox("使用信用贷/借贷资金", value=parsed.get("is_credit", False),   key="pre_eval_credit")
        is_etf    = st.checkbox(
            "为杠杆ETF（如 TQQQ）",
            value=parsed.get("is_leverage_etf", False) or pos_data.get("is_leverage_etf", False),
            key="pre_eval_etf",
        )

    run_btn = st.button(
        "⚡ 运行纪律评估", type="primary",
        use_container_width=True, key="pre_eval_run",
    )

    if run_btn:
        current_weight = (pos_data["market_value_cny"] / total) if total > 0 else 0.0
        pos_state = PositionState(
            symbol=pos_data.get("ticker") or pos_data["name"],
            name=pos_data["name"],
            weight=current_weight,
            drawdown_pct=(pos_data.get("profit_loss_rate", 0) / 100.0)
                         if pos_data.get("profit_loss_rate", 0) < 0 else 0.0,
            asset_class=(
                "leverage_etf" if is_etf
                else _ASSET_CLASS_MAP.get(pos_data.get("asset_class", "权益"), "equity")
            ),
            logic_intact=logic_ok,
        )
        portfolio_state = _build_portfolio_state(raw, portfolio_drawdown_pct)
        portfolio_state.positions = [
            p if p.symbol != pos_state.symbol else pos_state
            for p in portfolio_state.positions
        ]
        if pos_state.symbol not in {p.symbol for p in portfolio_state.positions}:
            portfolio_state.positions.append(pos_state)

        market_ctx = MarketContext(
            trend=parsed.get("trend", "sideways"),
            major_negative_event=bool(parsed.get("major_neg_event")),
        )
        user_st = UserState(emotional_state=emotion, daily_nav_drop_pct=0.0)
        action = TradeAction(
            action_type=action_type,
            symbol=pos_state.symbol,
            amount_pct=amount_pct,
            is_margin_trading=is_margin,
            is_options=is_opts,
            is_credit_loan=is_credit,
            is_leverage_etf=is_etf,
        )
        eval_result = evaluate_action(
            portfolio_state, pos_state, market_ctx, user_st, action,
        )
        st.session_state["pre_eval_result"] = eval_result
        st.session_state["pre_eval_context"] = {
            "pos_data":    pos_data,
            "action_type": action_type,
            "amount_pct":  amount_pct,
            "is_margin":   is_margin,
            "is_options":  is_opts,
            "is_credit":   is_credit,
            "is_etf":      is_etf,
            "emotion":     emotion,
            "logic_based": logic_ok,
        }

    eval_result = st.session_state.get("pre_eval_result")
    ctx         = st.session_state.get("pre_eval_context", {})

    if eval_result and ctx:
        st.divider()
        st.markdown("#### 🧪 引擎评估结果")
        _render_eval_result_section(eval_result)
        st.divider()
        _render_checklist_auto(raw, portfolio_drawdown_pct, ctx)


# ─────────────────────────────────────────────────────────
# Tab 4：纪律手册速查
# ─────────────────────────────────────────────────────────

_DEFAULT_HANDBOOK_MD = """\
# 投资纪律手册 v1.3

> 所有纪律均来源于真实亏损经历，每一条都是「避免再次犯错」的约束，而不是提升收益的技巧。**投资是一生的事业，而不是一次的赌局。**

**核心原则：**
- 投资的第一目标：**避免死亡（Avoid Ruin）**
- 收益来自长期复利，而非短期暴利
- 所有可能导致「不可逆损失」的行为必须被禁止
- 情绪不可作为决策依据，必须被纪律约束

**纪律分类：** 🔴 `HARD` = 硬性约束，强制执行 &emsp; 🔵 `SOFT` = 定性原则，辅助决策

### 规则1 — 杠杆工具分级管理  🔴 HARD

| 级别 | 工具类型 | 限制 |
|------|---------|------|
| **Level 0（禁止）** | 融资融券 | 完全禁止，杠杆率恒为 1.0 |
| **Level 0（禁止）** | 期权（任何形式） | 完全禁止，持仓为 0 |
| **Level 0（禁止）** | 借贷投资（信用贷等） | 完全禁止 |
| **Level 1（限制）** | 杠杆 ETF（如 TQQQ） | 允许，但持仓上限 ≤ 5% |
| **Level 2（正常）** | 无杠杆股票 / ETF / 基金 | 正常操作，受其他规则约束 |

- **Level 0 融资**：下跌时杠杆自动放大，触发强制平仓，在最低点锁死损失，剥夺持有回本的机会
- **Level 0 期权**：存在归零风险，与融资叠加实际杠杆率可达无限大
- **Level 1 杠杆 ETF**：无强制平仓/归零风险，但长期有衰减损耗，须严格控仓

> 真实案例：雪球 2 倍融资 + 期权叠加，本金 40 万 + 盈利 70 万 → 最终亏损至 ~10 万。

### 规则2 — 跨资产类别配置约束与偏离度控制  🔴 HARD

| 资产类别 | 建议区间 | 说明 |
|---------|---------|------|
| 货币 | 金额 1~10 万元 | 不宜太多或太少，日常生活备用，但不要大量闲置 |
| 固收 | 占比 20%~60% | 分散风险、流动性保障（详见规则4） |
| 权益 | 占比 40%~80% | 核心收益来源 |
| 另类 | 占比 0%~10% | 选配，非必须 |
| 衍生 | 占比 0%~10% | 选配，非必须 |

具体目标配置比例在 WealthPilot 中单独设定，本规则同时约束各类资产的**偏离程度**（见下）。

**偏离度控制与再平衡**

**偏离度 = 当前仓位占比 − 目标仓位占比**

| 偏离度 | 状态 | 操作 |
|--------|------|------|
| ≤ 10% | 🟢 正常 | 无需操作 |
| 10% ~ 20% | ⚠️ 预警 | 触发再平衡提醒，下次操作时优先向目标靠拢 |
| > 20% | 🚨 超限 | **强制再平衡** |

### 规则3 — 单一标的仓位上限  🔴 HARD

| 仓位区间 | 状态 | 操作限制 |
|---------|------|---------|
| ≤ 30% | 🟢 安全区 | 正常操作 |
| 30% ~ 40% | ⚠️ 警戒区 | 禁止继续加仓 |
| > 40% | 🚨 超限 | **强制减仓至 40% 以下** |

**硬性上限：单一标的仓位不得超过 40%，任何情况不得突破。**

仓位过度集中会导致：无法操作（既无法加仓也无法减仓）、回撤不可控、情绪被仓位绑架丧失判断力。

> 真实案例：理想汽车仓位升至 ~70%，四重打击下无法操作，损失惨重。

### 规则4 — 流动性管理（子弹纪律）  🔴 HARD

**流动性定义：货币 + 固收（不含权益、另类、衍生）**

| 场景 | 流动性资金（货币+固收）比例 |
|------|------------------------|
| 正常市场 | ≥ 20% |
| 下跌建仓过程中 | 最后 10%~15% 保留为「极端子弹」，只在极端行情触发 |
| 任何时候 | **禁止满仓操作** |

- 越跌越买，但永远不能把子弹打完
- 最后一发子弹只在系统性崩盘、个股极端利空时使用
- 保留流动性也是心理稳定的基础：有子弹可打，才有底气持仓

### 规则5 — 止损与逻辑判断  🔴 HARD

| 层级 | 条件 | 动作 |
|------|------|------|
| **硬止损（立即执行）** | 逻辑破坏：公司基本面/商业模式/赛道发生根本性改变 | 立即减仓或清仓 |
| **软止损（强制复核）** | 单标的从买入成本回撤达 30% | **强制重新评估逻辑，不自动卖出** |

> ⚠️ **软止损 = 强制思考，不是机械卖出**。成长股波动大，机械止损会误杀优质资产。

**逻辑破坏判断框架（任意一条成立则触发硬止损）：**
1. 核心产品竞争力消失，被替代品颠覆
2. 商业模式不可持续（盈利模型根本变化）
3. 管理层诚信或能力出现不可逆问题
4. 赛道萎缩或监管导致行业前景根本改变

> ⚠️ 短期利空（产品延误/季报不及预期/营销失误）≠ 逻辑破坏，不触发止损，应视为左侧加仓机会（规则8）。

### 规则6 — 加仓节奏纪律  🔴 HARD

**定量约束：**
- 单次加仓 ≤ 总投资性资产的 **10%**
- 必须分批执行（≥ 2 次以上），**禁止一次性建满仓位**
- 连续两次加仓之间，至少间隔 **1 个交易日**
- 不设固定跌幅触发间距（保留判断灵活性，避免机械化）

**原则：** 强制间隔制造冷静期，防止连续利空下情绪驱动仓位失控。

> 真实案例：理想汽车 i8/i6/MEGA/增程四重利空下连续加仓，仓位从 40% 升至 ~70%，完全失去操作能力。

### 规则7 — 动态仓位管理  🔵 SOFT

**原则：** 长期看好一家公司 ≠ 仓位永远不动。企业发展螺旋式上升，必然伴随阶段性利好与挫折，应顺应规律逆向管理仓位。

| 市场信号 | 操作方向 |
|---------|---------|
| 利好密集 + 股价快速上涨 | 分批减仓，落袋为安 |
| 利空冲击 + 股价快速下跌（长期逻辑未变） | 分批加仓，逆向布局 |

**注意：**
- 「长期看好」的前提下才可逆向加仓，若基本面逻辑已破坏则不适用此规则
- 逆向操作须配合规则8（左侧交易）和规则6（加仓节奏）执行

### 规则8 — 左侧交易原则  🔵 SOFT

**买入纪律：** 在下跌趋势中逐步分批建仓（左侧买入），越跌越买，不追最低点，不在反弹已确立后追涨。

**卖出纪律：** 在上涨趋势中逐步分批减仓（左侧卖出），越涨越卖，利好密集情绪过热时主动减仓，不等见顶后才卖。

**波动做T策略：**

| 卖出后回调幅度 | 操作 |
|--------------|------|
| 回调 ~10% | 按原仓位比例买回，完成「T」字操作 |
| 回调 ~20% | 在买回基础上适度加仓 |

### 规则9 — 长期持仓底仓机制  🔵 SOFT

**原则：** 对于长期看好的核心标的，永远保留 10%~20% 的底仓不卖出。

**适用条件：**
- 公司长期逻辑成立（未触发规则5的逻辑破坏判断）
- 已在投资组合中持有 1 年以上的核心仓位

**底仓的意义：**
- 防止「卖飞」后心理负担过重，影响后续决策
- 保持对标的的持续关注和参与感
- 长期成长红利不因短期操作而错失

### 规则10 — 情绪冷却与禁止交易纪律  🔴 HARD

| 情绪状态 | 典型特征 | 后果 |
|---------|---------|------|
| **不甘心** | 亏损后想翻本，加大仓位 | 加速亏损，杠杆风险 |
| **贪婪** | 连续盈利后认为无所不能 | 仓位过重，黑天鹅暴击 |
| **恐慌** | 跟随市场恐慌砍仓 | 在最低点锁定亏损 |
| **侥幸** | 「这次不一样」，绕过纪律 | 纪律体系失效 |

**强制冷却触发条件（任意一条 → 24 小时禁止操作）：**
- 单日个人资产净值下跌超过 5%
- 刚经历重大负面消息（公司利空、市场暴跌）
- 主观上已感到强烈的情绪驱动

### 规则11 — 交易前强制检查清单  🔴 HARD

**每次下单前必须通过以下全部 9 项检查，任意 1 项不通过 = 禁止交易。**

| # | 检查项 | 标准 |
|---|--------|------|
| 1 | 是否违反单一仓位上限？ | 买入后该标的仓位不超过 40% |
| 2 | 是否涉及 Level 0 杠杆工具？ | 无融资、无期权、无借贷 |
| 3 | 杠杆 ETF 是否超限？ | 杠杆 ETF 总持仓 ≤ 5% |
| 4 | 单次加仓是否超限？ | 本次加仓 ≤ 总资产 10%，且非一次性建仓 |
| 5 | 流动性资金是否足够？ | 操作后流动性资金（货币+固收）比例仍 ≥ 20% |
| 6 | 是否处于情绪冷却期？ | 未处于上述 4 种禁止情绪状态 |
| 7 | 是否基于长期逻辑？ | 非短期追涨杀跌，有明确长期判断依据 |
| 8 | 若再跌 10%，是否可以接受？ | 心理上和仓位上都能承受 |
| 9 | 是否符合整体资产配置策略？ | 不违反偏离度和跨资产比例约束 |
"""


def _parse_handbook_md(md: str) -> tuple[str, list[tuple[str, str]]]:
    """
    将手册 Markdown 解析为 (header_text, [(rule_title, rule_content), ...])。
    以 '\\n### ' 作为规则章节分隔符。
    """
    parts = md.split("\n### ")
    header = parts[0].strip()
    sections: list[tuple[str, str]] = []
    for part in parts[1:]:
        newline_idx = part.find("\n")
        if newline_idx == -1:
            sections.append((part.strip(), ""))
        else:
            title = part[:newline_idx].strip()
            content = part[newline_idx + 1:].strip()
            sections.append((title, content))
    return header, sections


def _render_handbook() -> None:
    # ── 确定内容来源（session_state 缓存上传内容）────────────
    if "handbook_uploaded_md" not in st.session_state:
        st.session_state["handbook_uploaded_md"] = None

    md_content = st.session_state["handbook_uploaded_md"] or _DEFAULT_HANDBOOK_MD

    # ── 解析并渲染 ────────────────────────────────────────
    header, sections = _parse_handbook_md(md_content)

    if header:
        st.markdown(header)

    if sections:
        for title, content in sections:
            with st.expander(f"**{title}**"):
                if content:
                    st.markdown(content)

    # ── 下载 / 上传控制栏（页面底部，低调展示）──────────────
    st.divider()
    col_up, col_dl = st.columns([3, 1])

    with col_up:
        uploaded = st.file_uploader(
            "上传自定义手册",
            type=["md"],
            help="将手册下载到本地，在 Obsidian / VS Code 等编辑器中修改后上传。格式要求：用 ### 标题 分隔各规则章节。",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            st.session_state["handbook_uploaded_md"] = uploaded.read().decode("utf-8")
            st.rerun()

    with col_dl:
        st.download_button(
            label="⬇️ 下载手册 .md",
            data=md_content,
            file_name="investment_discipline_handbook.md",
            mime="text/markdown",
            use_container_width=True,
            help="下载当前手册为 Markdown 文件，修改后可重新上传",
        )

    if st.session_state["handbook_uploaded_md"]:
        col_tip, col_clear = st.columns([4, 1])
        with col_tip:
            st.caption("📂 当前显示：已上传的自定义手册")
        with col_clear:
            if st.button("恢复默认", use_container_width=True):
                st.session_state["handbook_uploaded_md"] = None
                st.rerun()
    else:
        st.caption("💡 可下载手册在本地编辑后重新上传，支持添加个人案例备注")


# ─────────────────────────────────────────────────────────
# 主渲染函数
# ─────────────────────────────────────────────────────────

_NAV_ITEMS = ["📊  账户风险仪表盘", "🔍  交易前评估", "📖  纪律手册速查"]


def render() -> None:
    st.title("投资纪律执行引擎")

    # 导航栏样式：用 radio horizontal 替代 st.tabs()
    # 原因：Streamlit 1.32.x 中 st.tabs() 的选中状态在内部 button 点击后会丢失（已知行为），
    # 而 st.radio(key=...) 的选中值存储在 session_state 中，任何 rerun 都不会重置。
    st.markdown("""
    <style>
    /* 隐藏 radio 圆形按钮，保留可点击的 label 区域，模拟 tab 外观 */
    div[data-testid="stRadio"] > label { display: none; }
    div[data-testid="stRadio"] > div[role="radiogroup"] {
        gap: 0;
        border-bottom: 2px solid rgba(49,51,63,0.15);
        margin-bottom: 1rem;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label {
        flex: 1;
        justify-content: center;
        font-size: 1.05rem;
        font-weight: 500;
        padding: 0.55rem 0.5rem;
        border-radius: 6px 6px 0 0;
        cursor: pointer;
    }
    div[data-testid="stRadio"] > div[role="radiogroup"] > label[data-checked="true"] {
        font-weight: 700;
        border-bottom: 2px solid #ff4b4b;
        margin-bottom: -2px;
    }
    </style>
    """, unsafe_allow_html=True)

    # 加载数据
    raw = _load_positions(portfolio_id)

    if not raw:
        st.warning("暂无投资持仓数据，请先在「投资账户总览」中导入数据。")
        return

    # 根据持仓整体盈亏自动计算账户回撤（负数表示亏损）
    _total_cost = sum(r["market_value_cny"] - r.get("profit_loss_value", 0.0) for r in raw)
    _total_pl   = sum(r.get("profit_loss_value", 0.0) for r in raw)
    portfolio_drawdown = (_total_pl / _total_cost) if _total_cost > 0 else 0.0

    # 自动检测规则5 Level 0 违规（从 DB 读取，无需手动输入）
    has_credit, has_margin, has_options = _detect_level0_status(portfolio_id)

    # 导航 radio：state 存于 session_state，button 点击后不会丢失
    active_nav = st.radio(
        "页面导航",
        _NAV_ITEMS,
        horizontal=True,
        label_visibility="collapsed",
        key="discipline_nav",
    )

    if active_nav == _NAV_ITEMS[0]:
        _render_dashboard(raw, portfolio_drawdown, has_margin, has_options, has_credit)
    elif active_nav == _NAV_ITEMS[1]:
        _render_pre_trade_eval(raw, portfolio_drawdown)
    else:
        _render_handbook()
