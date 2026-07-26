"""
ReviewingAgent - PEER 4 Agent 之 Reviewing 角色。

职责（对标 agentUniverse ReviewingAgent）：
1. 跑 DecisionValidator 硬校验（v2.6 已有，覆盖 99% 场景）
2. 硬校验失败时调 LLM 评分（0-1）
3. 根据评分决定动作：pass / retry / fallback

设计哲学：
- 同步函数（非流式），输入 3 个 Output → 输出 ReviewOutput
- 两层校验设计平衡延迟和质量：硬校验优先（毫秒级），LLM 评分兜底
- 评分机制（agentUniverse PEER 启发）：
    score >= 0.8       → action="pass"
    0.5 <= score < 0.8 → action="retry", jump_step="expressing"
    score < 0.5        → action="retry", jump_step="executing"
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from backend.agents.contracts import (
    PlanningOutput,
    ExecutionOutput,
    ExpressionOutput,
    ReviewOutput,
    AgentTaskStatus,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# LLM 评分 Prompt（Layer 2 用）
# ════════════════════════════════════════════════════════════

_REVIEWING_PROMPT = """你是 WealthPilot 的输出审查官，负责评估 LLM 生成的投资建议是否合规、是否解决了用户问题。

请基于以下信息给出 0-1 之间的评分：

【用户问题】
{user_query}

【意图】
{intent_type}

【硬校验失败的规则】
{failed_rules}

【LLM 输出】
{chat_answer}

评分标准：
- 0.8-1.0：高质量，能完整解决用户问题，无明显问题
- 0.5-0.8：可接受但有瑕疵（格式不规范、引用缺失等表面问题），可重新生成表达
- 0.0-0.5：质量较差（数据引用错误、决策违反纪律、严重跑题），需要重新执行

输出严格 JSON 格式（第一个字符必须是 {{）：
{{
  "score": 0.0到1.0之间的小数,
  "rationale": "简短理由（不超过 50 字）",
  "jump_step": "executing 或 expressing 或 null"
}}

jump_step 规则：
- 评分 < 0.5 → "executing"（数据问题，从执行重做）
- 0.5 <= 评分 < 0.8 → "expressing"（表达问题，只重新生成）
- 评分 >= 0.8 → null（无需重试）
"""


def _use_skill_output_validator() -> bool:
    """C1 双轨 flag：默认开（走 invoke_skill），显式设 =0 切回老直连。"""
    return os.environ.get("WP_USE_SKILL_OUTPUT_VALIDATOR", "1") != "0"


class ReviewingAgent:
    """
    PEER 4 Agent 之 Reviewing 角色。

    使用方式：
        agent = ReviewingAgent()
        review = agent.run(plan_out, exec_out, expr_out, user_query)
        if review.action == "pass": ...
        elif review.action == "retry": ...
        elif review.action == "fallback": ...
    """

    def __init__(self):
        pass

    def run(
        self,
        planning_output: PlanningOutput,
        execution_output: ExecutionOutput,
        expression_output: ExpressionOutput,
        user_query: str = "",
        retry_count: int = 0,
    ) -> ReviewOutput:
        """
        执行 Reviewing 阶段。

        Layer 1：DecisionValidator 硬校验（毫秒级）
        Layer 2：LLM 评分（仅硬校验失败时触发）

        Note (Step 5 决策, 2026-05-04):
        v3 当前在 action="retry" 时只发 warning（不真实重跑），
        与 v2.6 decision_service.py L618-626 行为完全等价。

        真实 retry 循环（jump_step="expressing"/"executing" + retry_count 上限）
        是 ReviewingAgent 预留的增量能力，记入 v3.1 路线图。
        实施时需在 v3 入口（decision_service_v3.py）加 retry 循环 + 超限 fallback HOLD。
        """
        out = ReviewOutput(task_id=planning_output.task_id)
        out.status = AgentTaskStatus.IN_PROGRESS
        out.retry_count = retry_count

        try:
            # ── Layer 1: 硬校验 ──
            self._run_hard_validation(out, planning_output, expression_output)

            if out.hard_validation_passed:
                out.score = 1.0
                out.action = "pass"
                out.score_rationale = "hard_validation_passed"
                out.mark_completed()
                logger.info(
                    f"[ReviewingAgent] task={out.task_id} action=pass "
                    f"(hard validation passed)"
                )
                return out

            # 重试次数已达上限 → fallback
            if retry_count >= 3:
                out.score = 0.0
                out.action = "fallback"
                out.score_rationale = f"retry_limit_reached (count={retry_count})"
                out.mark_completed()
                logger.warning(
                    f"[ReviewingAgent] task={out.task_id} action=fallback "
                    f"(retry limit, failed_rules={out.failed_rules})"
                )
                return out

            # ── Layer 2: LLM 评分 ──
            self._run_llm_scoring(
                out, planning_output, expression_output, user_query,
            )

            out.mark_completed()
            logger.info(
                f"[ReviewingAgent] task={out.task_id} action={out.action} "
                f"score={out.score:.2f} jump_step={out.jump_step} "
                f"failed_rules={out.failed_rules} duration={out.duration_ms}ms"
            )
            return out

        except Exception as e:
            logger.exception(f"[ReviewingAgent] task={out.task_id} 异常: {e}")
            # 异常时降级为 pass（避免阻塞用户体验）
            out.action = "pass"
            out.score = 0.5
            out.score_rationale = f"reviewing_exception: {e}"
            out.warnings.append(f"审查异常但放行: {e}")
            out.mark_failed(str(e))
            return out

    # ────────────────────────────────────────────────────────
    # Layer 1: 硬校验
    # ────────────────────────────────────────────────────────

    def _run_hard_validation(
        self,
        out: ReviewOutput,
        planning_output: PlanningOutput,
        expression_output: ExpressionOutput,
    ) -> None:
        """
        调用 v2.6 DecisionValidator 做硬校验。

        API: validate_decision_output(result, intent_type, discipline_violations)
        返回 ValidationResult(passed, failures, action, intent_type)
        """
        from backend.graph.decision_validator import validate_decision_output

        llm_result = expression_output.llm_result
        if llm_result is None:
            # 没有 LLM 结果（如 general_chat 纯文本），跳过硬校验
            out.hard_validation_passed = True
            return

        # 解析 intent_type
        intent = planning_output.intent
        if isinstance(intent, dict):
            intent_type = intent.get("primary_intent", "")
        else:
            intent_type = ""

        try:
            if _use_skill_output_validator():
                from backend.skills import invoke_skill
                vr = invoke_skill(
                    "wp-output-validator",
                    result=llm_result,
                    intent_type=intent_type,
                )
            else:
                vr = validate_decision_output(
                    result=llm_result,
                    intent_type=intent_type,
                )
        except Exception as e:
            logger.warning(f"[ReviewingAgent] 硬校验异常: {e}")
            out.hard_validation_passed = True  # 异常时放行
            return

        out.hard_validation_passed = vr.passed
        if not vr.passed:
            out.failed_rules = [f.rule for f in vr.failures]
            out.failure_messages = [f.message for f in vr.failures]

    # ────────────────────────────────────────────────────────
    # Layer 2: LLM 评分
    # ────────────────────────────────────────────────────────

    def _run_llm_scoring(
        self,
        out: ReviewOutput,
        planning_output: PlanningOutput,
        expression_output: ExpressionOutput,
        user_query: str,
    ) -> None:
        """
        调 LLM 给输出打分（0-1）+ 决定 jump_step。
        使用 gpt-4.1-mini 控制成本。
        """
        try:
            import os
            import openai

            client = openai.OpenAI(api_key=os.environ.get("WEALTHPILOT_OPENAI_API_KEY", ""))

            intent = planning_output.intent
            intent_type = intent.get("primary_intent", "Unknown") if isinstance(intent, dict) else "Unknown"

            prompt = _REVIEWING_PROMPT.format(
                user_query=user_query or "未知问题",
                intent_type=intent_type,
                failed_rules=", ".join(out.failed_rules) if out.failed_rules else "无",
                chat_answer=expression_output.chat_answer[:500],
            )

            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                timeout=15,
            )

            raw = response.choices[0].message.content or "{}"
            scoring = json.loads(raw)

        except Exception as e:
            logger.warning(f"[ReviewingAgent] LLM 评分异常: {e}，降级为 retry expressing")
            out.score = 0.6
            out.action = "retry"
            out.jump_step = "expressing"
            out.score_rationale = f"scoring_exception: {e}"
            return

        out.score = float(scoring.get("score", 0.5))
        out.score_rationale = scoring.get("rationale", "")

        if out.score >= 0.8:
            out.action = "pass"
            out.jump_step = None
        elif out.score >= 0.5:
            out.action = "retry"
            out.jump_step = scoring.get("jump_step") or "expressing"
        else:
            out.action = "retry"
            out.jump_step = scoring.get("jump_step") or "executing"


# ════════════════════════════════════════════════════════════
# 模块级别快捷函数
# ════════════════════════════════════════════════════════════

_default_agent: Optional[ReviewingAgent] = None


def get_reviewing_agent() -> ReviewingAgent:
    """获取全局 ReviewingAgent 单例。"""
    global _default_agent
    if _default_agent is None:
        _default_agent = ReviewingAgent()
    return _default_agent
