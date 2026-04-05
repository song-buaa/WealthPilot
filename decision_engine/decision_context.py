"""
DecisionContext 构建模块（Phase 2）

职责：在每次调用决策模块前，自动组装结构化上下文（DecisionContext），
注入给 AI 的 system prompt。

设计原则：
    - 所有字段允许降级：任何子步骤抛异常时 catch 后用占位文字继续
    - 不允许整个函数因单个字段失败而抛出异常
    - 复用已有 data_loader 加载结果，不重复查询持仓数据
"""

from __future__ import annotations

import json
import re
from typing import Optional

from app.database import get_session
from app.discipline.config import get_rules as _get_discipline_rules
from app.models import (
    DecisionLog,
    Portfolio,
    ResearchCard,
    ResearchDocument,
    UserProfile as UserProfileModel,
)

from .data_loader import LoadedData, PositionInfo


# ── 公司名 → 代码 关键词表 ──────────────────────────────────────────────────────

_ASSET_KEYWORDS: dict[str, str] = {
    "腾讯": "00700", "美团": "03690", "阿里": "09988", "京东": "09618",
    "拼多多": "PDD", "李宁": "02331", "比亚迪": "002594",
    "茅台": "600519", "宁德": "300750", "小米": "01810",
}


# ═════════════════════════════════════════════════════════════════════════════
# Step 2：decisionTask 推断（规则引擎，不调用 LLM）
# ═════════════════════════════════════════════════════════════════════════════

def _extract_target_asset(text: str) -> str:
    """
    从用户消息中提取 targetAsset。
    优先匹配股票代码，再匹配公司名关键词。
    """
    # 港股代码: 5位数字.HK
    m = re.search(r'\d{5}\.HK', text, re.IGNORECASE)
    if m:
        return m.group(0)
    # A股代码: 6位纯数字
    m = re.search(r'(?<!\d)\d{6}(?!\d)', text)
    if m:
        return m.group(0)
    # 美股代码: 1-5位大写字母（排除常见中文语境误匹配）
    m = re.search(r'\b[A-Z]{1,5}\b', text)
    if m and m.group(0) not in ("AI", "VS", "OK", "ETF", "IPO", "CEO", "APP"):
        return m.group(0)
    # 公司名关键词
    for name, code in _ASSET_KEYWORDS.items():
        if name in text:
            return name
    return ""


def _infer_decision_scenario(text: str) -> str:
    """按顺序匹配，第一个命中即返回。"""
    if any(kw in text for kw in ("研报", "分析师", "帮我看", "解读")):
        return "research_interpretation"
    if any(kw in text for kw in ("仓位", "调仓", "组合", "结构", "配置")):
        return "portfolio_rebalance"
    if any(kw in text for kw in ("跌", "暴跌", "风险", "该怎么办", "止损")):
        return "risk_check"
    if any(kw in text for kw in ("还要拿", "继续持", "还能持", "要不要卖", "要不要走")):
        return "position_followup"
    return "single_asset_trade"


_SCENARIO_TO_TASK = {
    "single_asset_trade":       "buy_sell_judgement",
    "position_followup":        "position_management",
    "portfolio_rebalance":      "rebalance",
    "risk_check":               "risk_assessment",
    "research_interpretation":  "buy_sell_judgement",
}


def _infer_question_type(text: str) -> str:
    if any(kw in text for kw in ("为什么", "原因", "解释", "怎么理解")):
        return "explanation"
    if any(kw in text for kw in ("比较", "哪个好")) or ("还是" in text and "好" in text) or "vs" in text.lower():
        return "comparison"
    if any(kw in text for kw in ("之前", "上次", "刚才", "刚说")):
        return "followup"
    return "judgement"


def _infer_decision_task(user_message: str) -> dict:
    """从 user_message 推断 decisionTask 全部字段。"""
    target_asset = _extract_target_asset(user_message)
    scenario = _infer_decision_scenario(user_message)
    return {
        "targetAsset":      target_asset,
        "decisionScenario": scenario,
        "taskType":         _SCENARIO_TO_TASK.get(scenario, "buy_sell_judgement"),
        "questionType":     _infer_question_type(user_message),
        "userIntent":       user_message[:50],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Step 3：各字段数据获取与映射
# ═════════════════════════════════════════════════════════════════════════════

# ── userProfileSummary ──────────────────────────────────────────────────────

def _build_user_profile_summary(session, portfolio_id: int) -> dict:
    """从 user_profiles 表 + portfolios 表读取，映射为 PRD 格式。"""
    result = {
        "riskTolerance":   "moderate",
        "investmentGoal":  "投资目标暂未配置",
        "hardConstraints": [],
        "softPreferences": [],
        "investmentHorizon": "medium",
    }

    try:
        # user_profiles 是全局单条记录
        profile = session.query(UserProfileModel).first()
        if profile:
            # riskTolerance
            rnl = profile.risk_normalized_level
            if rnl is not None:
                if rnl <= 2:
                    result["riskTolerance"] = "conservative"
                elif rnl == 3:
                    result["riskTolerance"] = "moderate"
                else:
                    result["riskTolerance"] = "aggressive"

            # investmentGoal
            if profile.target_return:
                result["investmentGoal"] = f"目标年化收益 {profile.target_return}"
            elif profile.goal_type:
                try:
                    goals = json.loads(profile.goal_type)
                    if isinstance(goals, list) and goals:
                        result["investmentGoal"] = str(goals[0])
                except (json.JSONDecodeError, TypeError):
                    result["investmentGoal"] = str(profile.goal_type)

            # investmentHorizon
            horizon = profile.investment_horizon or ""
            if "1年" in horizon and ("以下" in horizon or "<" in horizon):
                result["investmentHorizon"] = "short"
            elif "1-3" in horizon:
                result["investmentHorizon"] = "short"
            elif "3-5" in horizon:
                result["investmentHorizon"] = "medium"
            elif "5年" in horizon and ("以上" in horizon or ">" in horizon):
                result["investmentHorizon"] = "long"
    except Exception as e:
        print(f"[decision_context] userProfile 读取失败: {e}", flush=True)

    try:
        # hardConstraints: 从 portfolios 表
        portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
        if portfolio:
            constraints = []
            if portfolio.max_single_stock_pct is not None:
                pct = portfolio.max_single_stock_pct
                # 兼容 0~1 和 0~100 两种存储格式
                val = pct if pct > 1 else pct * 100
                constraints.append(f"单标的仓位上限 {val:.0f}%")
            if portfolio.min_cash_pct is not None:
                pct = portfolio.min_cash_pct
                val = pct if pct > 1 else pct * 100
                constraints.append(f"最低现金比例 {val:.0f}%")
            result["hardConstraints"] = constraints
    except Exception as e:
        print(f"[decision_context] hardConstraints 读取失败: {e}", flush=True)

    return result


# ── positionSnapshot ────────────────────────────────────────────────────────

def _build_position_snapshot(loaded_data: LoadedData, target_asset: str) -> dict:
    """从已加载的 LoadedData 构建持仓快照。"""
    positions = []
    for p in loaded_data.positions:
        positions.append({
            "asset":            f"{p.name} {p.ticker}".strip() if p.ticker else p.name,
            "costPrice":        p.cost_price,
            "currentPrice":     p.current_price,
            "weight":           p.weight,
            "unrealizedPnlPct": p.profit_loss_rate,
        })

    # currentHoldingStatus
    holding_status = "none"
    if target_asset:
        for p in loaded_data.positions:
            if target_asset in p.name or target_asset in p.ticker:
                w = p.weight
                if w <= 0:
                    holding_status = "none"
                elif w <= 0.08:
                    holding_status = "light"
                elif w <= 0.18:
                    holding_status = "medium"
                else:
                    holding_status = "heavy"
                break

    result = {
        "positions":            positions,
        "currentHoldingStatus": holding_status,
        "totalAssets":          loaded_data.total_assets,
    }
    return result


# ── disciplineRules ─────────────────────────────────────────────────────────

# 规则 key → 可读标题
_RULE_TITLES: dict[str, str] = {
    "single_asset_limits":      "单标的持仓限制",
    "position_sizing":          "加仓规模控制",
    "leverage_limits":          "杠杆使用限制",
    "liquidity_limits":         "流动性要求",
    "rebalancing_rules":        "再平衡规则",
    "stop_loss_rules":          "止损规则",
    "asset_allocation_ranges":  "资产配置区间",
    "cooldown_rules":           "冷静期规则",
    "portfolio_circuit_breaker": "组合熔断机制",
}

# 规则 key → content 生成函数
def _rule_content(key: str, val: dict) -> str:
    """把 config 中的规则数值拼成一句话描述（≤100字）。"""
    if key == "single_asset_limits":
        return f"单标的仓位硬性上限 {val.get('max_position_pct', 0.4):.0%}，警戒线 {val.get('warning_position_pct', 0.3):.0%}"
    if key == "position_sizing":
        return f"单次加仓不超过总资产 {val.get('max_single_add_pct', 0.1):.0%}，至少分 {val.get('min_batches_required', 2)} 批"
    if key == "leverage_limits":
        return f"杠杆ETF持仓上限 {val.get('level_1_max_pct', 0.05):.0%}，总杠杆率警戒线 {val.get('leverage_ratio_warning_max', 1.35)}"
    if key == "liquidity_limits":
        return f"最低现金比例 {val.get('min_cash_pct', 0.2):.0%}，极端市况预留 {val.get('extreme_reserve_pct', 0.1):.0%}"
    if key == "stop_loss_rules":
        return f"逻辑破坏硬止损，软止损触发阈值 {val.get('soft_stop_review_trigger_pct', 0.3):.0%}"
    if key == "cooldown_rules":
        return f"单日净值跌幅超 {val.get('daily_nav_drop_trigger_pct', 0.05):.0%} 触发 {val.get('cooldown_hours', 24)} 小时冷静期"
    if key == "portfolio_circuit_breaker":
        return f"组合回撤超 {val.get('drawdown_trigger_pct', 0.25):.0%} 暂停所有买入"
    if key == "rebalancing_rules":
        return f"偏离 {val.get('deviation_warning_pct', 0.1):.0%} 预警，偏离 {val.get('deviation_force_rebalance_pct', 0.2):.0%} 强制再平衡"
    if key == "asset_allocation_ranges":
        return f"权益 {val.get('equity_min', 0.4):.0%}-{val.get('equity_max', 0.8):.0%}，固收 {val.get('fixed_income_min', 0.2):.0%}-{val.get('fixed_income_max', 0.6):.0%}"
    return json.dumps(val, ensure_ascii=False)[:100]


def _rule_trigger(key: str, val: dict) -> str:
    """从 config 阈值字段生成 triggerCondition。"""
    if key == "single_asset_limits":
        return f"单标的仓位 ≥ {val.get('warning_position_pct', 0.3):.0%} 时触发预警"
    if key == "leverage_limits":
        return f"总杠杆率 > {val.get('leverage_ratio_acceptable_max', 1.2)} 时触发预警"
    if key == "liquidity_limits":
        return f"现金比例 < {val.get('min_cash_pct', 0.2):.0%} 时触发"
    if key == "stop_loss_rules":
        return f"标的亏损 ≥ {val.get('soft_stop_review_trigger_pct', 0.3):.0%} 或逻辑破坏时触发"
    if key == "cooldown_rules":
        return f"单日跌幅 ≥ {val.get('daily_nav_drop_trigger_pct', 0.05):.0%} 时触发"
    if key == "portfolio_circuit_breaker":
        return f"组合回撤 ≥ {val.get('drawdown_trigger_pct', 0.25):.0%} 时触发"
    return "按条件自动触发"


def _build_discipline_rules(target_asset: str) -> list[dict]:
    """从 config.py 读取硬编码规则并转换为 PRD 格式。"""
    try:
        rules = _get_discipline_rules()
    except Exception:
        return []

    all_rules = []
    for key, val in rules.items():
        if not isinstance(val, dict):
            continue
        all_rules.append({
            "ruleId":           key,
            "title":            _RULE_TITLES.get(key, key.replace("_", " ").title()),
            "content":          _rule_content(key, val),
            "triggerCondition": _rule_trigger(key, val),
        })

    # 过滤：targetAsset 不为空时优先返回相关规则
    if target_asset:
        # 对单标的操作最相关的规则
        priority_keys = ["single_asset_limits", "position_sizing", "stop_loss_rules"]
        prioritized = [r for r in all_rules if r["ruleId"] in priority_keys]
        if prioritized:
            return prioritized[:3]

    return all_rules[:3]


# ── researchViews ───────────────────────────────────────────────────────────

def _build_research_views(session, target_asset: str) -> list[dict]:
    """从 research_cards 表读取投研观点。"""
    if not target_asset:
        return []

    try:
        cards = (
            session.query(ResearchCard)
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .filter(ResearchDocument.object_name.ilike(f"%{target_asset}%"))
            .filter(ResearchDocument.parse_status.in_(["parsed", "saved_only"]))
            .order_by(ResearchCard.created_at.desc())
            .limit(2)
            .all()
        )

        result = []
        for card in cards:
            # key_metrics: JSON list → 最多取 3 条
            metrics = []
            if card.key_metrics:
                try:
                    raw = json.loads(card.key_metrics)
                    if isinstance(raw, list):
                        metrics = [str(m)[:60] for m in raw[:3]]
                except (json.JSONDecodeError, TypeError):
                    pass

            result.append({
                "asset":      card.document.object_name or target_asset if card.document else target_asset,
                "viewDate":   card.created_at.strftime("%Y-%m-%d") if card.created_at else "未知",
                "bullCase":   (card.bull_case or "")[:80],
                "bearCase":   (card.bear_case or "")[:80],
                "keyMetrics": metrics,
            })

        return result

    except Exception as e:
        print(f"[decision_context] researchViews 读取失败: {e}", flush=True)
        return []


# ── recentRecords ───────────────────────────────────────────────────────────

def _build_recent_records(session, target_asset: str, portfolio_id: int) -> list[dict]:
    """从 decision_logs 表读取近期决策记录。"""
    try:
        query = session.query(DecisionLog).filter_by(portfolio_id=portfolio_id)

        if target_asset:
            query = query.filter(
                DecisionLog.title.ilike(f"%{target_asset}%")
                | DecisionLog.context.ilike(f"%{target_asset}%")
            )
            query = query.order_by(DecisionLog.created_at.desc()).limit(5)
        else:
            query = query.order_by(DecisionLog.created_at.desc()).limit(3)

        logs = query.all()

        result = []
        for log in logs:
            # 从 title 提取 asset
            asset = target_asset or "未知标的"
            if log.title:
                # title 通常含标的名称
                for name in _ASSET_KEYWORDS:
                    if name in log.title:
                        asset = name
                        break

            result.append({
                "date":      log.created_at.strftime("%Y-%m-%d") if log.created_at else "未知",
                "asset":     asset,
                "action":    (log.conclusion or "")[:30],
                "rationale": (log.reasoning or "")[:60],
                "outcome":   log.status or "待执行",
            })

        return result

    except Exception as e:
        print(f"[decision_context] recentRecords 读取失败: {e}", flush=True)
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 主函数：组装 DecisionContext
# ═════════════════════════════════════════════════════════════════════════════

def build_decision_context(
    user_message: str,
    loaded_data: LoadedData,
    portfolio_id: int = 1,
) -> dict:
    """
    组装完整的 DecisionContext。

    所有字段允许降级，任何子步骤异常时 catch 后用占位值继续，
    不允许整个函数因单个字段失败而抛出异常。

    Args:
        user_message: 用户原始输入
        loaded_data:  已加载的 LoadedData（复用 data_loader 结果）
        portfolio_id: 投资组合 ID

    Returns:
        DecisionContext dict
    """
    # Step 2: decisionTask 推断
    task = _infer_decision_task(user_message)

    target_asset = task["targetAsset"]

    session = get_session()
    try:
        # Step 3: 各字段数据获取
        profile_summary = _build_user_profile_summary(session, portfolio_id)
        position_snapshot = _build_position_snapshot(loaded_data, target_asset)
        discipline_rules = _build_discipline_rules(target_asset)
        research_views = _build_research_views(session, target_asset)
        recent_records = _build_recent_records(session, target_asset, portfolio_id)
    finally:
        session.close()

    return {
        "decisionTask":        task,
        "userProfileSummary":  profile_summary,
        "positionSnapshot":    position_snapshot,
        "disciplineRules":     discipline_rules,
        "researchViews":       research_views,
        "recentRecords":       recent_records,
        "newsItems":           [],  # Phase 2 占位，后续模块接入
    }


# ═════════════════════════════════════════════════════════════════════════════
# Step 4：格式化为 Prompt 文本
# ═════════════════════════════════════════════════════════════════════════════

def format_context_prompt(ctx: dict) -> str:
    """
    将 DecisionContext dict 格式化为可注入 system prompt 的文本。
    """
    lines: list[str] = []
    lines.append("## 本次决策上下文")

    # ── 任务定义 ──
    task = ctx.get("decisionTask", {})
    lines.append("")
    lines.append("### 任务定义")
    lines.append(f"决策场景：{task.get('decisionScenario', '未知')}")
    lines.append(f"任务类型：{task.get('taskType', '未知')}")

    ta = task.get("targetAsset", "")
    lines.append(f"目标标的：{ta if ta else '标的未明确，请结合用户问题判断'}")

    snap = ctx.get("positionSnapshot", {})
    lines.append(f"当前持仓状态：{snap.get('currentHoldingStatus', 'none')}")
    lines.append(f"用户意图：{task.get('userIntent', '')}")

    # ── 用户画像 ──
    profile = ctx.get("userProfileSummary", {})
    lines.append("")
    lines.append("### 用户画像")
    lines.append(f"风险偏好：{profile.get('riskTolerance', 'moderate')}")
    lines.append(f"投资目标：{profile.get('investmentGoal', '暂未配置')}")

    hc = profile.get("hardConstraints", [])
    lines.append("硬性约束（判断边界，不得违反）：")
    if hc:
        for c in hc:
            lines.append(f"  - {c}")
    else:
        lines.append("  暂无硬性约束")

    sp = profile.get("softPreferences", [])
    lines.append(f"偏好：{'、'.join(sp) if sp else '暂未配置'}")

    ih = profile.get("investmentHorizon", "medium")
    lines.append(f"投资期限：{ih}")

    # ── 当前持仓 ──
    lines.append("")
    lines.append("### 当前持仓")
    positions = snap.get("positions", [])
    if positions:
        total = snap.get("totalAssets", 0)
        if total > 0:
            lines.append(f"总资产：¥{total:,.0f}")
        for p in positions:
            w = p.get("weight", 0)
            pnl = p.get("unrealizedPnlPct", 0)
            lines.append(
                f"- {p.get('asset', '?')}：成本 ¥{p.get('costPrice', 0):,.0f}，"
                f"现价 ¥{p.get('currentPrice', 0):,.0f}，"
                f"占比 {w * 100:.1f}%，浮动盈亏 {pnl * 100:.1f}%"
            )
    else:
        lines.append("当前无持仓数据")

    # ── 命中纪律规则 ──
    lines.append("")
    lines.append("### 命中纪律规则")
    rules = ctx.get("disciplineRules", [])
    if rules:
        for r in rules:
            lines.append(f"[{r.get('ruleId', '?')}] {r.get('title', '?')}: {r.get('content', '')}")
    else:
        lines.append("无命中纪律条目")

    # ── 相关投研观点 ──
    lines.append("")
    lines.append("### 相关投研观点")
    views = ctx.get("researchViews", [])
    if views:
        for v in views:
            lines.append(f"标的：{v.get('asset', '?')}")
            lines.append(f"多方：{v.get('bullCase', '无')}")
            lines.append(f"空方：{v.get('bearCase', '无')}")
            if v.get("keyMetrics"):
                lines.append(f"关注指标：{'、'.join(v['keyMetrics'])}")
            lines.append("")
    else:
        lines.append("暂无相关研究观点")

    # ── 近期决策记录 ──
    lines.append("")
    lines.append("### 近期决策记录")
    records = ctx.get("recentRecords", [])
    if records:
        for r in records:
            lines.append(
                f"{r.get('date', '?')} {r.get('asset', '?')} "
                f"{r.get('action', '?')} - {r.get('rationale', '')}（{r.get('outcome', '?')}）"
            )
    else:
        lines.append("暂无近期决策记录")

    return "\n".join(lines)
