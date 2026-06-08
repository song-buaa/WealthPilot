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
# 执行计划 validator (v3.11, PRD §9)
# 旁路调用,不走 validate_decision_output 的 intent_type 分发
# ══════════════════════════════════════════════════════════════════

import re

# 匹配文案中的"疑似计划数字"：带小数点的浮点数 或 ≥100 的整数
# 不匹配：百分比(如"45%")、小整数(<100)、中文量词后缀
_SUSPICIOUS_NUMBER_RE = re.compile(
    r"(?<![.\d])"           # 前面不是数字或小数点
    r"(\d+\.\d+)"          # 浮点数(如 13.91, 0.45)
    r"(?![\d%])"            # 后面不是数字/百分号
    r"|"
    r"(?<![.\d])"
    r"(\d{3,})"             # ≥100 的整数(如 1877, 5633)
    r"(?![\d%年月日天条批周])" # 后面不是数字/百分号/中文量词
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
) -> ValidationResult:
    """执行计划 validator — 校验 LLM 输出与规则引擎数字一致。

    PRD §9:
    1. plan_summary_block 里的数字必须与 plan_dict 逐一一致
       (本函数不需要比对两份数据——因为 plan_summary_block 就是 plan_dict
        直接产出的。真正要防的是 orchestrator 组装时错传/LLM 篡改。)
    2. rationale/risk_notes 禁止新增 plan_dict 之外的价格/数量/批次数字
    3. 引用波动率/回撤/分位必须能追溯到 factor_snapshot

    Returns:
        ValidationResult (旁路调用版,intent_type="ExecutionPlan")
    """
    failures: list[ValidationFailure] = []

    # ── Check 1: plan_summary_block 数值完整性 ──
    psb = plan_dict.get("plan_summary_block", plan_dict)
    tranches = psb.get("tranches", [])
    if not tranches:
        failures.append(ValidationFailure(
            rule="plan_value_mismatch",
            message="plan_summary_block 不含 tranches",
            severity="hard",
        ))

    # ── Check 2: rationale/risk_notes 不含计划外数字 ──
    # 收集 plan dict 里所有合法数字(含 factor_snapshot 可引用值)
    allowed_numbers = _collect_allowed_numbers(psb, factor_snapshot)

    for label, text in [("rationale", llm_rationale), ("risk_notes", llm_risk_notes)]:
        if not text:
            continue
        suspicious = _find_suspicious_numbers(text, allowed_numbers)
        for num_str in suspicious:
            failures.append(ValidationFailure(
                rule="plan_value_mismatch",
                message=(
                    f"{label} 中出现计划/因子之外的数字 '{num_str}'，"
                    f"LLM 不应新增 plan_dict 之外的数值"
                ),
                severity="hard",
            ))

    # 判定
    hard = [f for f in failures if f.severity == "hard"]
    return ValidationResult(
        passed=len(hard) == 0,
        failures=failures,
        action="pass" if not hard else "retry",
        intent_type="ExecutionPlan",
    )


def _collect_allowed_numbers(plan_summary: dict, factor_snapshot: dict) -> set[str]:
    """收集所有"允许出现在文案中"的数字字符串。"""
    allowed = set()

    # plan_summary 里的所有数值
    def _extract_nums(obj):
        if isinstance(obj, (int, float)):
            # 多种字符串表示都允许
            allowed.add(str(obj))
            if isinstance(obj, float):
                # 允许不同精度
                for d in range(5):
                    allowed.add(f"{obj:.{d}f}")
            if isinstance(obj, int):
                allowed.add(str(float(obj)))
        elif isinstance(obj, dict):
            for v in obj.values():
                _extract_nums(v)
        elif isinstance(obj, list):
            for v in obj:
                _extract_nums(v)

    _extract_nums(plan_summary)

    # factor_snapshot 里允许引用的字段值
    for field in _ALLOWED_FACTOR_FIELDS:
        val = factor_snapshot.get(field)
        if val is not None:
            allowed.add(str(val))
            if isinstance(val, float):
                for d in range(5):
                    allowed.add(f"{val:.{d}f}")
                # 百分比形式也允许(0.4544 → 45.44)
                pct = val * 100
                allowed.add(f"{pct:.1f}")
                allowed.add(f"{pct:.2f}")
                allowed.add(str(round(pct)))

    return allowed


def _find_suspicious_numbers(text: str, allowed: set[str]) -> list[str]:
    """在文案中找出不在允许集合里的可疑数字。

    只关注"看起来像价格/数量"的数字：
    - 浮点数(如 13.91, 15.50) — 几乎都是价格
    - ≥100 的整数(如 1877, 5633) — 几乎都是股数
    不关注：小整数、百分比、中文量词后缀的数字。
    """
    suspicious = []
    for match in _SUSPICIOUS_NUMBER_RE.finditer(text):
        # 两个捕获组：group(1)=浮点数, group(2)=大整数
        num_str = match.group(1) or match.group(2)
        if not num_str:
            continue

        try:
            val = float(num_str)
        except ValueError:
            continue

        # 浮点数: 跳过很小的值(如 0.45, 0.78 — 通常是比例)
        if "." in num_str and val < 3.0:
            continue

        if num_str in allowed:
            continue

        # 尝试各种精度匹配
        matched = False
        for a in allowed:
            try:
                if abs(float(a) - val) < 0.01:
                    matched = True
                    break
            except ValueError:
                continue
        if not matched:
            suspicious.append(num_str)
    return suspicious
