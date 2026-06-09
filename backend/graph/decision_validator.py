"""
DecisionValidator (v2.6 M1.4)

运行时门禁：对每次决策输出做确定性校验。
- 通用层：所有意图都跑（chat_answer 非空、非 fallback）
- 专项层：PositionDecision 额外深度校验

设计原则：
  纯函数，无 LLM 调用，确定性可复现。
  Eval Harness（离线分析）是事后发现问题，
  Validator（运行时门禁）是每次决策前的最后防线，两者互补不替代。
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from decision_engine.llm_engine import LLMResult, GenericLLMResult

# PositionDecision 合法决策档位
VALID_DECISIONS = {"BUY", "HOLD", "TAKE_PROFIT", "REDUCE", "SELL", "STOP_LOSS"}

# 纪律严重违规时不允许出现的激进决策
AGGRESSIVE_DECISIONS = {"BUY", "TAKE_PROFIT"}


@dataclass
class ValidationFailure:
    rule: str          # 规则名称，如 "decision_invalid"
    message: str       # 人类可读的失败原因
    severity: str      # "hard"（必须重试）/ "soft"（记录但不重试）


@dataclass
class ValidationResult:
    passed: bool
    failures: list[ValidationFailure]
    action: str        # "pass" / "retry" / "fallback"
    intent_type: str   # 哪类意图


# ──────────────────────────────────────────────────────────────────
# 通用层：所有意图都跑
# ──────────────────────────────────────────────────────────────────

def _validate_common(result, intent_type: str) -> list[ValidationFailure]:
    """通用校验：适用于 LLMResult 和 GenericLLMResult。"""
    failures = []

    # 1. 是否 fallback（LLM 调用本身出错）
    if getattr(result, "is_fallback", False):
        failures.append(ValidationFailure(
            rule="is_fallback",
            message="LLM 调用出错（error 字段非空）",
            severity="hard",
        ))
        return failures  # fallback 时其他字段不可信，直接返回

    # 2. chat_answer 非空
    chat_answer = getattr(result, "chat_answer", "") or ""
    if not chat_answer.strip():
        failures.append(ValidationFailure(
            rule="chat_answer_empty",
            message="chat_answer 为空",
            severity="hard",
        ))

    # 3. chat_answer 最低长度（20 字）
    elif len(chat_answer.strip()) < 20:
        failures.append(ValidationFailure(
            rule="chat_answer_too_short",
            message=f"chat_answer 过短（{len(chat_answer.strip())} 字，最低 20 字）",
            severity="hard",
        ))

    return failures


# ──────────────────────────────────────────────────────────────────
# 专项层：仅 PositionDecision
# ──────────────────────────────────────────────────────────────────

def _validate_position_decision(
    result,
    discipline_violations: list | None = None,
) -> list[ValidationFailure]:
    """PositionDecision 专项校验。"""
    failures = []

    # 4. decision 在合法枚举内
    decision = getattr(result, "decision", None)
    if decision not in VALID_DECISIONS:
        failures.append(ValidationFailure(
            rule="decision_invalid",
            message=f"decision 值 '{decision}' 不在合法枚举 {VALID_DECISIONS} 内",
            severity="hard",
        ))

    # 5. reasoning 非空
    reasoning = getattr(result, "reasoning", None) or []
    if not reasoning:
        failures.append(ValidationFailure(
            rule="reasoning_empty",
            message="reasoning 列表为空，缺少推理依据",
            severity="hard",
        ))

    # 6. risk 非空
    risk = getattr(result, "risk", None) or []
    if not risk:
        failures.append(ValidationFailure(
            rule="risk_empty",
            message="risk 列表为空，缺少风险提示",
            severity="hard",
        ))

    # 7. 纪律严重违规时不能给激进决策
    if discipline_violations:
        high_severity = [
            v for v in discipline_violations
            if getattr(v, "severity", "") == "high"
            or (isinstance(v, dict) and v.get("severity") == "high")
        ]
        if high_severity and decision in AGGRESSIVE_DECISIONS:
            failures.append(ValidationFailure(
                rule="discipline_conflict",
                message=(
                    f"存在 {len(high_severity)} 条 high-severity 纪律违规，"
                    f"但 decision='{decision}'（激进建议），两者矛盾"
                ),
                severity="hard",
            ))

    # 8. structured_result 有就校验 confidence 字段（soft，不强制）
    structured = getattr(result, "structured_result", None)
    if structured and isinstance(structured, dict):
        confidence = structured.get("confidence")
        info_needed = structured.get("infoNeeded")
        if confidence is not None and confidence < 0.5 and not info_needed:
            failures.append(ValidationFailure(
                rule="low_confidence_no_info_needed",
                message=f"confidence={confidence} < 0.5 但 infoNeeded 为空",
                severity="soft",
            ))

    return failures


# ──────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────

def validate_decision_output(
    result,
    intent_type: str,
    discipline_violations: list | None = None,
) -> ValidationResult:
    """
    对决策输出做分层校验。

    Args:
        result: LLMResult 或 GenericLLMResult 实例
        intent_type: "PositionDecision" / "PortfolioReview" / "AssetAllocation" /
                     "PerformanceAnalysis" / "Education" / "GeneralChat"
        discipline_violations: 纪律校验结果列表（仅 PositionDecision 需要）

    Returns:
        ValidationResult
    """
    # 通用层
    failures = _validate_common(result, intent_type)

    # 专项层（仅 PositionDecision）
    if intent_type == "PositionDecision" and not any(
        f.rule == "is_fallback" for f in failures
    ):
        failures += _validate_position_decision(result, discipline_violations)

    # 判定 action
    hard_failures = [f for f in failures if f.severity == "hard"]

    if not hard_failures:
        action = "pass"
    elif any(f.rule == "is_fallback" for f in hard_failures):
        # LLM 本身出错，直接降级，重试没有意义
        action = "fallback"
    else:
        # 其他 hard failure，先重试一次
        action = "retry"

    return ValidationResult(
        passed=len(hard_failures) == 0,
        failures=failures,
        action=action,
        intent_type=intent_type,
    )


def make_fallback_result(intent_type: str, original_error: str = ""):
    """
    生成降级输出——当 Validator 判定 action=fallback 时使用。
    返回一个最小可用的结果对象（dict，不是 dataclass，避免循环 import）。
    """
    if intent_type == "PositionDecision":
        return {
            "decision": "HOLD",
            "reasoning": ["决策过程中遇到问题，建议暂时观望"],
            "risk": ["系统暂时无法完成完整分析，请稍后重试"],
            "strategy": [],
            "chat_answer": (
                "抱歉，本次分析未能完成。建议您暂时观望，稍后再试。"
                + (f"（原因：{original_error}）" if original_error else "")
            ),
            "is_fallback_by_validator": True,
        }
    else:
        return {
            "chat_answer": (
                "抱歉，本次分析未能完成，请稍后重试。"
                + (f"（原因：{original_error}）" if original_error else "")
            ),
            "is_fallback_by_validator": True,
        }


# ══════════════════════════════════════════════════════════════════
# 执行计划 validator (v3.11 债4, PRD §9)
# 旁路调用,不走 validate_decision_output 的 intent_type 分发
#
# 两层防线:
#   1. plan_summary_block 结构化比对(hard) — 锁死下单安全
#   2. 文案数字白名单检查(soft) — 暴露 LLM 编数但不卡下单
# ══════════════════════════════════════════════════════════════════

import re

# 匹配文案中的数字：浮点数(≥3.0) 或 ≥100 的整数
# 排除：百分号后缀、中文量词后缀、小浮点/小整数
_TEXT_NUMBER_RE = re.compile(
    r"(?<![.\d])"
    r"(\d+\.\d+)"          # 浮点数
    r"(?![\d%])"
    r"|"
    r"(?<![.\d])"
    r"(\d{3,})"             # ≥100 的整数
    r"(?![\d%年月日天条批周])"
)

# 因子快照里允许在文案中引用的字段
_ALLOWED_FACTOR_FIELDS = {
    "volatility_annual", "price_percentile", "drawdown_from_high",
    "rsi14", "atr14", "current_price", "ma5", "ma20",
    "macd", "macd_signal", "macd_hist",
}


def validate_execution_plan(
    plan_dict: dict,
    llm_rationale: str,
    llm_risk_notes: str,
    factor_snapshot: dict,
    plan_dict_frozen: dict | None = None,
) -> ValidationResult:
    """执行计划 validator — 结构化比对(hard) + 文案白名单(soft)。

    Args:
        plan_dict: orchestrator 最终返回的 plan_summary_block
        llm_rationale: LLM 产出的解释文案
        llm_risk_notes: LLM 产出的风险提示
        factor_snapshot: 因子快照(用于文案数字白名单)
        plan_dict_frozen: 规则引擎原始产出的 plan dict(调 LLM 前锁定)。
                          若提供，与 plan_dict 逐字段比对；不一致 = hard。
    """
    failures: list[ValidationFailure] = []

    # ══ Layer 1: plan_summary_block 结构化比对 (hard) ══
    psb = plan_dict
    tranches = psb.get("tranches", [])
    if not tranches:
        failures.append(ValidationFailure(
            rule="plan_structure_invalid",
            message="plan_summary_block 不含 tranches",
            severity="hard",
        ))

    # 如果有冻结副本，逐字段比对
    if plan_dict_frozen is not None:
        mismatches = _compare_plan_dicts(plan_dict_frozen, plan_dict)
        for field, expected, actual in mismatches:
            failures.append(ValidationFailure(
                rule="plan_value_mismatch",
                message=f"plan_summary_block.{field}: 期望 {expected}, 实际 {actual}",
                severity="hard",
            ))

    # ══ Layer 2: 文案数字白名单检查 (soft) ══
    allowed = _collect_allowed_numbers(psb, factor_snapshot)
    for label, text in [("rationale", llm_rationale), ("risk_notes", llm_risk_notes)]:
        if not text:
            continue
        suspicious = _find_text_numbers(text, allowed)
        for num_str in suspicious:
            failures.append(ValidationFailure(
                rule="plan_text_number_untracked",
                message=f"{label} 含计划/因子之外的数字 '{num_str}'",
                severity="soft",
            ))

    # 判定: hard failure → 拦截; soft only → 通过但带警告
    hard = [f for f in failures if f.severity == "hard"]
    return ValidationResult(
        passed=len(hard) == 0,
        failures=failures,
        action="pass" if not hard else "retry",
        intent_type="ExecutionPlan",
    )


def _compare_plan_dicts(frozen: dict, actual: dict) -> list[tuple[str, object, object]]:
    """逐字段比对 frozen plan dict 与 actual plan dict。

    返回 [(field_path, expected, actual), ...]
    """
    mismatches = []
    # 顶层数值字段
    for key in ("total_quantity", "num_tranches", "target_position_pct",
                "current_position_pct", "current_price"):
        fv = frozen.get(key)
        av = actual.get(key)
        if fv != av:
            mismatches.append((key, fv, av))

    # tranches 逐批比对
    ft = frozen.get("tranches", [])
    at = actual.get("tranches", [])
    if len(ft) != len(at):
        mismatches.append(("tranches.length", len(ft), len(at)))
    else:
        for i, (f, a) in enumerate(zip(ft, at)):
            for field in ("sequence", "quantity", "trigger_price", "limit_price", "trigger_type"):
                fv = f.get(field)
                av = a.get(field)
                if fv != av:
                    mismatches.append((f"tranches[{i}].{field}", fv, av))
    return mismatches


def _collect_allowed_numbers(plan_summary: dict, factor_snapshot: dict) -> set[str]:
    """收集 plan dict + factor_snapshot 里所有合法数字的字符串表示。"""
    allowed: set[str] = set()

    def _extract(obj):
        if isinstance(obj, (int, float)):
            allowed.add(str(obj))
            if isinstance(obj, float):
                for d in range(5):
                    allowed.add(f"{obj:.{d}f}")
            if isinstance(obj, int):
                allowed.add(str(float(obj)))
        elif isinstance(obj, dict):
            for v in obj.values():
                _extract(v)
        elif isinstance(obj, list):
            for v in obj:
                _extract(v)

    _extract(plan_summary)

    for field in _ALLOWED_FACTOR_FIELDS:
        val = factor_snapshot.get(field)
        if val is not None:
            allowed.add(str(val))
            if isinstance(val, float):
                for d in range(5):
                    allowed.add(f"{val:.{d}f}")
                pct = val * 100
                allowed.add(f"{pct:.1f}")
                allowed.add(f"{pct:.2f}")
                allowed.add(str(round(pct)))

    return allowed


def _find_text_numbers(text: str, allowed: set[str]) -> list[str]:
    """在文案中找不在白名单里的数字(浮点≥3 或整数≥100)。"""
    suspicious = []
    for match in _TEXT_NUMBER_RE.finditer(text):
        num_str = match.group(1) or match.group(2)
        if not num_str:
            continue
        try:
            val = float(num_str)
        except ValueError:
            continue
        if "." in num_str and val < 3.0:
            continue
        if num_str in allowed:
            continue
        matched = any(
            abs(float(a) - val) < 0.01
            for a in allowed
            if a.replace(".", "", 1).replace("-", "", 1).isdigit()
        )
        if not matched:
            suspicious.append(num_str)
    return suspicious
