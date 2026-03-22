"""
决策流程编排（Decision Flow）

职责：按 PRD 规定的固定链路编排完整决策流程。

执行链路：
    用户输入
    → 意图解析（intent_parser）
    → 数据加载（data_loader）
    → 前置校验（pre_check）
    → 规则校验（rule_engine）
    → 信号生成（signal_engine）
    → LLM推理（llm_engine）
    → 返回 DecisionResult

对外接口：
    run(user_input, portfolio_id) → DecisionResult
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import intent_parser, data_loader, pre_check, rule_engine, signal_engine, llm_engine
from .intent_parser import IntentResult
from .data_loader import LoadedData
from .pre_check import PreCheckResult
from .rule_engine import RuleResult
from .signal_engine import SignalResult
from .llm_engine import LLMResult
from app.state import portfolio_id as default_portfolio_id


# ── 流程阶段枚举 ──────────────────────────────────────────────────────────────

class FlowStage(str, Enum):
    INTENT      = "intent"       # 意图解析完成
    LOADED      = "loaded"       # 数据加载完成
    PRE_CHECK   = "pre_check"    # 前置校验完成
    RULE_CHECK  = "rule_check"   # 规则校验完成
    SIGNAL      = "signal"       # 信号生成完成
    LLM         = "llm"          # LLM 推理完成
    DONE        = "done"         # 全流程完成
    ABORTED     = "aborted"      # 流程中断


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class DecisionResult:
    """
    决策流程完整输出，包含各阶段中间结果。
    Streamlit UI 直接读取此结构进行渲染。
    """
    # 流程元数据
    stage: FlowStage = FlowStage.INTENT
    aborted_reason: Optional[str] = None   # 流程中断原因

    # 各阶段输出（按执行顺序）
    intent: Optional[IntentResult] = None
    data: Optional[LoadedData] = None
    pre_check: Optional[PreCheckResult] = None
    rules: Optional[RuleResult] = None
    signals: Optional[SignalResult] = None
    llm: Optional[LLMResult] = None

    @property
    def is_complete(self) -> bool:
        """全流程是否跑完（包括 LLM 输出）。"""
        return self.stage == FlowStage.DONE

    @property
    def was_aborted(self) -> bool:
        return self.stage == FlowStage.ABORTED

    @property
    def final_decision(self) -> Optional[str]:
        """最终决策结论（BUY/HOLD/SELL），仅 complete 时有值。"""
        return self.llm.decision if self.llm else None


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def run(user_input: str, pid: int = default_portfolio_id) -> DecisionResult:
    """
    执行完整决策流程。

    Args:
        user_input: 用户自然语言输入
        pid: portfolio_id（默认从 app.state 读取）

    Returns:
        DecisionResult，包含各阶段输出
    """
    result = DecisionResult()

    # ── Step 1: 意图解析 ─────────────────────────────────────────────────────
    try:
        intent = intent_parser.parse(user_input)
        result.intent = intent
        result.stage = FlowStage.INTENT
    except EnvironmentError as e:
        # 无 API Key → 中断并提示
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"⚙️ 配置问题：{e}"
        return result
    except Exception as e:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"意图解析失败：{e}"
        return result

    # 置信度不足 → 中断，返回澄清问题
    if intent.needs_clarification:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = intent.clarification or "请重新描述您的投资决策需求。"
        return result

    # ── Step 2: 数据加载 ─────────────────────────────────────────────────────
    try:
        loaded = data_loader.load(asset_name=intent.asset, pid=pid)
        result.data = loaded
        result.stage = FlowStage.LOADED
    except Exception as e:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"数据加载失败：{e}"
        return result

    # ── Step 3: 前置校验 ─────────────────────────────────────────────────────
    pre = pre_check.check(loaded)
    result.pre_check = pre
    result.stage = FlowStage.PRE_CHECK

    if not pre.passed:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = pre.message
        return result

    # ── Step 4: 规则校验 ─────────────────────────────────────────────────────
    rule_result = rule_engine.check(loaded, intent)
    result.rules = rule_result
    result.stage = FlowStage.RULE_CHECK

    # 规则违规 + 操作是加仓/买入 → 可以继续流程但在 LLM 中体现（不直接中断）
    # PRD 未规定硬性中断，信号层会体现 violation，LLM 会据此给出 HOLD/SELL

    # ── Step 5: 信号生成 ─────────────────────────────────────────────────────
    sig = signal_engine.generate(loaded, intent, rule_result)
    result.signals = sig
    result.stage = FlowStage.SIGNAL

    # ── Step 6: LLM 推理 ─────────────────────────────────────────────────────
    llm_result = llm_engine.reason(
        user_query=user_input,
        data=loaded,
        intent=intent,
        rule_result=rule_result,
        signals=sig,
    )
    result.llm = llm_result
    result.stage = FlowStage.DONE

    return result
