"""
Discipline Service — 投资纪律业务逻辑

从 app_pages/discipline.py 提取的纯业务逻辑，去除所有 Streamlit 依赖。
直接复用：app.discipline.{config, models, engine_runner}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.models import Position, Liability, get_session
from app.discipline.config import get_rules, save_rules_config, reset_rules_to_default
from app.discipline.models import (
    PortfolioState, PositionState, MarketContext,
    UserState, TradeAction,
)
from app.discipline.engine_runner import evaluate_action

_OFFICIAL_HANDBOOK_FILE = Path("data/handbook_official.md")
_CUSTOM_HANDBOOK_FILE   = Path("data/handbook_custom.md")

_LEVERAGE_ETF_KEYWORDS = ["杠杆", "TQQQ", "SOXL", "UPRO", "TECL", "LABU", "FNGU"]
_OPTIONS_KEYWORDS = ["期权", "认购", "认沽", "call", "put"]
_MARGIN_KEYWORDS  = ["融资", "融券"]

_ASSET_CLASS_MAP = {
    "权益": "equity",
    "固收": "fixed_income",
    "货币": "cash",
    "另类": "alternatives",
    "衍生": "leverage_etf",
}


# ── 交易评估 ──────────────────────────────────────────────────────────────────

def evaluate_trade(text: str, portfolio_id: int) -> dict:
    """
    自然语言交易意图 → 投资纪律评估结果。

    流程：
      1. 从 DB 加载持仓 + 负债
      2. 解析自然语言意图
      3. 构建引擎入参
      4. 调用 evaluate_action()
      5. 序列化结果返回
    """
    raw = _load_positions(portfolio_id)
    total_assets = sum(r["market_value_cny"] for r in raw) or 1.0

    parsed = _parse_trade_intent(text, raw, total_assets)

    # 构建 PortfolioState
    portfolio_state = _build_portfolio_state(raw, portfolio_drawdown_pct=0.0)

    # 构建 PositionState（目标标的）
    target_name = parsed.get("name")
    target_pos = _find_position_state(portfolio_state, target_name)
    if target_pos is None:
        target_pos = PositionState(
            symbol=target_name or "UNKNOWN",
            name=target_name or "",
            weight=0.0,
        )

    # 构建 TradeAction
    action_type = parsed.get("action_type") or "BUY"
    amount_pct  = parsed.get("amount_pct") or 0.05  # 无法识别金额时保守估计 5%
    action = TradeAction(
        action_type=action_type,
        symbol=target_pos.symbol,
        amount_pct=amount_pct,
        is_margin_trading=parsed.get("is_margin", False),
        is_options=parsed.get("is_options", False),
        is_credit_loan=parsed.get("is_credit", False),
        is_leverage_etf=parsed.get("is_leverage_etf", False),
    )

    # 构建 MarketContext
    market = MarketContext(
        trend=parsed.get("trend", "sideways"),
        major_negative_event=parsed.get("major_neg_event", False),
    )

    # 构建 UserState
    user_state = UserState(
        emotional_state=parsed.get("emotion", "normal"),
    )

    result = evaluate_action(portfolio_state, target_pos, market, user_state, action)

    return {
        "parsed_intent": {
            "asset":       parsed.get("name"),
            "action":      parsed.get("action_type"),
            "amount_cny":  parsed.get("amount_cny"),
            "amount_pct":  parsed.get("amount_pct"),
            "confidence":  1.0 if not parsed["unresolved"] else 0.5,
            "unresolved":  parsed["unresolved"],
        },
        "evaluation": {
            "blocked":             not result.allowed,
            "block_reason":        result.block_reasons[0] if result.block_reasons else None,
            "block_reasons":       result.block_reasons,
            "final_verdict":       result.final_verdict,
            "risk_status":         result.risk.status,
            "risk_warnings":       result.risk.warnings,
            "risk_messages":       result.risk.messages,
            "psychology_status":   result.psychology.status,
            "psychology_warnings": result.psychology.triggered_reasons,
            "decision_recommendation": result.decision.recommendation if result.allowed else None,
            "decision_reasons":    result.decision.reasons if result.allowed else [],
            "decision_warnings":   result.decision.warnings if result.allowed else [],
        },
    }


# ── 规则配置 ──────────────────────────────────────────────────────────────────

def get_rules_config() -> dict:
    return get_rules()


def update_rules_config(new_rules: dict) -> dict:
    save_rules_config(new_rules)
    return get_rules()


def reset_rules() -> dict:
    reset_rules_to_default()
    return get_rules()


# ── 手册管理 ──────────────────────────────────────────────────────────────────

def get_handbook() -> dict:
    """返回当前手册内容及来源"""
    custom = _load_custom_handbook()
    if custom is not None:
        return {"source": "custom", "content": custom}
    return {"source": "official", "content": _load_official_handbook()}


def save_handbook(content: str) -> None:
    """保存用户定制手册"""
    _CUSTOM_HANDBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CUSTOM_HANDBOOK_FILE.write_text(content, encoding="utf-8")


def reset_handbook() -> dict:
    """删除定制手册，恢复官方版"""
    if _CUSTOM_HANDBOOK_FILE.exists():
        _CUSTOM_HANDBOOK_FILE.unlink()
    reset_rules_to_default()
    return {"source": "official", "content": _load_official_handbook()}


# ── 内部：数据加载 ────────────────────────────────────────────────────────────

def _load_positions(portfolio_id: int) -> list[dict]:
    session = get_session()
    try:
        rows = session.query(Position).filter_by(
            portfolio_id=portfolio_id, segment="投资"
        ).all()
        return [
            {
                "name":            p.name,
                "ticker":          p.ticker or "",
                "platform":        p.platform,
                "asset_class":     p.asset_class,
                "market_value_cny": p.market_value_cny or 0.0,
                "profit_loss_rate": (
                    (p.profit_loss_value / ((p.market_value_cny or 0.0) - p.profit_loss_value) * 100)
                    if p.profit_loss_value and (p.market_value_cny or 0.0) - p.profit_loss_value != 0
                    else 0.0
                ),
                "profit_loss_value": p.profit_loss_value or 0.0,
                "is_leverage_etf": _is_leverage_etf(p),
            }
            for p in rows
        ]
    finally:
        session.close()


def _is_leverage_etf(p: Position) -> bool:
    text = f"{p.name or ''} {p.ticker or ''}".upper()
    return any(k.upper() in text for k in _LEVERAGE_ETF_KEYWORDS)


def _build_portfolio_state(raw: list[dict], portfolio_drawdown_pct: float) -> PortfolioState:
    total = sum(r["market_value_cny"] for r in raw) or 1.0
    liquidity = sum(
        r["market_value_cny"] for r in raw if r["asset_class"] in ("货币", "固收")
    )
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


def _find_position_state(
    portfolio: PortfolioState, name: Optional[str]
) -> Optional[PositionState]:
    if not name:
        return None
    for p in portfolio.positions:
        if p.name == name or p.symbol == name:
            return p
    return None


# ── 内部：意图解析（从 discipline.py 提取，原样复用）─────────────────────────

def _parse_trade_intent(text: str, raw: list[dict], total_assets: float) -> dict:
    """
    从自然语言描述中提取交易意图的结构化字段。
    关键词规则实现，后续可替换为 LLM 调用。
    """
    result: dict = {
        "name":            None,
        "action_type":     None,
        "amount_cny":      None,
        "amount_pct":      None,
        "is_leverage_etf": False,
        "is_margin":       False,
        "is_options":      False,
        "is_credit":       False,
        "emotion":         "normal",
        "logic_based":     None,
        "major_neg_event": False,
        "trend":           "sideways",
        "raw_text":        text,
        "unresolved":      [],
    }

    if any(k in text for k in ["加仓", "补仓", "补一点", "分批补", "继续买"]):
        result["action_type"] = "ADD"
    elif any(k in text for k in ["清仓", "全部卖", "全卖", "清掉"]):
        result["action_type"] = "SELL"
    elif any(k in text for k in ["减仓", "减一点", "卖一点", "先减", "部分卖"]):
        result["action_type"] = "REDUCE"
    elif any(k in text for k in ["买入", "新建仓", "建仓", "首次买", "第一次买"]):
        result["action_type"] = "BUY"

    position_names = [r["name"] for r in raw]
    for name in sorted(position_names, key=len, reverse=True):
        if name in text or name.lower() in text.lower():
            result["name"] = name
            break

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

    if any(k in text for k in ["不甘心", "翻本", "回血", "亏了想", "亏损中"]):
        result["emotion"] = "regret"
    elif any(k in text for k in ["恐慌", "吓到了", "割肉冲动", "怕了", "慌了"]):
        result["emotion"] = "panic"
    elif any(k in text for k in ["连续涨", "涨不停", "没有上限", "飘了"]):
        result["emotion"] = "greed"
    elif any(k in text for k in ["赌一把", "这次不一样", "押注", "碰运气"]):
        result["emotion"] = "lucky"

    if any(k in text for k in ["长期", "基本面", "看好", "长逻辑", "赛道", "成长", "价值"]):
        result["logic_based"] = True
    elif any(k in text for k in ["短线", "短期", "博反弹", "追涨", "跟风", "消息面"]):
        result["logic_based"] = False

    if any(k in text for k in ["下跌", "跌了", "回调", "低位", "底部", "跌幅"]):
        result["trend"] = "down"
    elif any(k in text for k in ["上涨", "涨了", "高位", "新高", "大涨"]):
        result["trend"] = "up"

    if any(k in text for k in ["财报暴雷", "产品失败", "利空", "暴雷", "丑闻", "造假"]):
        result["major_neg_event"] = True

    if any(k.upper() in text.upper() for k in
           ["TQQQ", "SOXL", "UPRO", "TECL", "LABU", "FNGU", "杠杆ETF"]):
        result["is_leverage_etf"] = True
    if any(k in text for k in ["融资", "融券"]):
        result["is_margin"] = True
    if any(k in text for k in ["期权", "认购", "认沽"]):
        result["is_options"] = True
    if any(k in text for k in ["借贷", "信用贷", "消费贷", "贷款买"]):
        result["is_credit"] = True

    if result["name"] is None:
        result["unresolved"].append("标的名称")
    if result["action_type"] is None:
        result["unresolved"].append("操作类型")
    if result["amount_pct"] is None:
        result["unresolved"].append("操作金额")

    return result


# ── 内部：手册文件读写 ────────────────────────────────────────────────────────

def _load_official_handbook() -> str:
    if _OFFICIAL_HANDBOOK_FILE.exists():
        return _OFFICIAL_HANDBOOK_FILE.read_text(encoding="utf-8")
    return "# 投资纪律手册\n\n官方手册文件未找到（data/handbook_official.md）。"


def _load_custom_handbook() -> Optional[str]:
    if _CUSTOM_HANDBOOK_FILE.exists():
        return _CUSTOM_HANDBOOK_FILE.read_text(encoding="utf-8")
    return None
