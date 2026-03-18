"""
Psychology Engine — 行为约束层

对应规则（HARD_RULE）：
    规则 11 情绪冷却与禁止交易纪律

功能：
    · 识别四种禁止情绪状态（不甘心/贪婪/恐慌/侥幸）
    · 检测单日净值跌幅触发冷却
    · 检查是否仍在有效冷却期内

输出：PsychologyCheckResult
    status = NORMAL   — 可以操作
    status = COOLDOWN — 强制禁止，24小时冷却期
"""

from datetime import datetime, timedelta
from typing import List

from .config import RULES
from .models import UserState, PortfolioState, PsychologyCheckResult

# 四种禁止情绪的描述（来自规则11）
_EMOTION_LABELS = {
    "regret": "不甘心（亏损后想翻本，加大仓位）",
    "greed":  "贪婪（连续盈利后认为无所不能）",
    "panic":  "恐慌（跟随市场恐慌砍仓）",
    "lucky":  "侥幸（'这次不一样'，绕过纪律）",
}
_FORBIDDEN_EMOTIONS = set(_EMOTION_LABELS.keys())


def run(user_state: UserState, portfolio: PortfolioState) -> PsychologyCheckResult:
    """
    Psychology Engine 主入口。

    检查顺序：
        1. 是否仍在有效冷却期内
        2. 单日净值跌幅是否达到触发阈值（≥5%）
        3. 当前情绪状态是否属于禁止类型

    任意条件触发 → COOLDOWN，禁止所有操作。
    """
    cfg = RULES["cooldown_rules"]
    triggered: List[str] = []
    cooldown_until = None

    # ── 1. 已在冷却期内 ──────────────────────────────────
    if user_state.cooldown_active and user_state.cooldown_until:
        if datetime.now() < user_state.cooldown_until:
            remaining = (user_state.cooldown_until - datetime.now()).total_seconds() / 3600
            triggered.append(
                f"[规则11·冷却期] 当前处于强制冷却期，"
                f"剩余约 {remaining:.1f} 小时，禁止一切操作。"
            )
            return PsychologyCheckResult(
                status="COOLDOWN",
                triggered_reasons=triggered,
                cooldown_until=user_state.cooldown_until,
            )

    # ── 2. 单日净值跌幅触发 ───────────────────────────────
    threshold = cfg["daily_nav_drop_trigger_pct"]
    if user_state.daily_nav_drop_pct < 0 and abs(user_state.daily_nav_drop_pct) >= threshold:
        triggered.append(
            f"[规则11·净值跌幅] 今日资产净值下跌"
            f" {abs(user_state.daily_nav_drop_pct)*100:.1f}%"
            f" ≥ 触发阈值 {threshold*100:.0f}%，"
            f"强制进入 {cfg['cooldown_hours']} 小时冷却期。"
        )
        cooldown_until = datetime.now() + timedelta(hours=cfg["cooldown_hours"])

    # ── 3. 情绪状态检查 ───────────────────────────────────
    if user_state.emotional_state in _FORBIDDEN_EMOTIONS:
        label = _EMOTION_LABELS[user_state.emotional_state]
        triggered.append(
            f"[规则11·情绪] 当前情绪状态：{label}。"
            f"情绪不可作为决策依据，强制进入 {cfg['cooldown_hours']} 小时冷却期。"
        )
        if cooldown_until is None:
            cooldown_until = datetime.now() + timedelta(hours=cfg["cooldown_hours"])

    if triggered:
        return PsychologyCheckResult(
            status="COOLDOWN",
            triggered_reasons=triggered,
            cooldown_until=cooldown_until,
        )

    return PsychologyCheckResult(status="NORMAL")
