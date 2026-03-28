"""
决策流程执行层（Decision Flow）— V3.2

职责：接收已解析的 IntentResult，执行完整 5 步投资决策管道。
意图识别由 intent_engine 统一负责，本模块不做意图解析。

执行链路：
    IntentResult（由 intent_engine 适配器传入）
    → 数据加载（data_loader）
    → 前置校验（pre_check）
    → 规则校验（rule_engine）
    → 信号生成（signal_engine）
    → LLM 推理（llm_engine）
    → 返回 DecisionResult（含 decision_id + llm.chat_answer）

对外接口：
    run_with_intent(intent, user_input, pid) → DecisionResult
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import data_loader, pre_check, rule_engine, signal_engine, llm_engine
from .types import IntentResult
from .data_loader import LoadedData
from .pre_check import PreCheckResult
from .rule_engine import RuleResult
from .signal_engine import SignalResult
from .llm_engine import LLMResult, GenericLLMResult
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

    V3.1 新增：
        decision_id   — investment_decision 流程唯一 ID，用于 Explain Panel 绑定
        chat_response — general_chat 路由时的普通对话回复文本
    """
    # 流程元数据
    stage: FlowStage = FlowStage.INTENT
    aborted_reason: Optional[str] = None   # 流程中断原因

    # V3.1 新增
    decision_id: Optional[str] = None      # 仅 investment_decision 完整流程生成
    chat_response: Optional[str] = None    # 仅 general_chat 时有值

    # 各阶段输出（按执行顺序）
    intent: Optional[IntentResult] = None
    data: Optional[LoadedData] = None
    pre_check: Optional[PreCheckResult] = None
    rules: Optional[RuleResult] = None
    signals: Optional[SignalResult] = None
    llm: Optional[LLMResult] = None
    generic_llm: Optional[GenericLLMResult] = None  # 组合级别意图（非 PositionDecision）的 LLM 结果

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

def run_with_intent(
    intent: IntentResult,
    user_input: str,
    pid: int = default_portfolio_id,
) -> DecisionResult:
    """
    跳过意图解析，直接用外部传入的 IntentResult 执行步骤 2-6。

    供 intent_engine 识别完意图后注入使用，避免重复调用 LLM 做意图识别。
    意图路由（hypothetical/general_chat 拦截）由调用方负责，此函数只处理
    investment_decision 完整链路。
    """
    result = DecisionResult()
    result.intent = intent
    result.stage = FlowStage.INTENT
    result.decision_id = f"decision_{uuid.uuid4().hex[:8]}"

    if intent.needs_clarification:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = intent.clarification or "请重新描述您的投资决策需求。"
        return result

    return _run_pipeline(result, intent, user_input, pid)


# ── 步骤 2-6 公共管道（run_with_intent 调用）─────────────────────────────────

def _run_pipeline(
    result: DecisionResult,
    intent: IntentResult,
    user_input: str,
    pid: int,
) -> DecisionResult:
    """执行数据加载 → 前置校验 → 规则校验 → 信号生成 → LLM推理。"""

    # ── Step 2: 数据加载 ─────────────────────────────────────────────────────
    try:
        loaded = data_loader.load(asset_name=intent.asset, pid=pid)
        result.data = loaded
        result.stage = FlowStage.LOADED
    except ValueError as e:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"⚠️ 数据异常：{e}"
        return result
    except Exception as e:
        result.stage = FlowStage.ABORTED
        result.aborted_reason = f"数据加载失败：{e}"
        return result

    if loaded.ambiguous_matches:
        names = "、".join(p.name for p in loaded.ambiguous_matches)
        result.stage = FlowStage.ABORTED
        result.aborted_reason = (
            f"🔍 找到多个名称相似的标的：**{names}**。\n\n"
            f"请输入更精确的名称（如完整股票名称或股票代码），以避免误判。"
        )
        return result

    if loaded.has_data_errors:
        error_msgs = "\n".join(
            f"- {w.message}" for w in loaded.data_warnings if w.level == "error"
        )
        result.stage = FlowStage.ABORTED
        result.aborted_reason = (
            f"⚠️ **数据质量问题，无法可靠给出投资建议**：\n\n{error_msgs}\n\n"
            f"请先在「投资账户总览」核实持仓数据后重试。"
        )
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

    # 未持有该标的 + 非买入操作 → LLM 生成智能引导回复
    # 买入/加仓不需要持仓，可继续正常分析；卖出/减仓/持有评估则应告知用户
    _buy_actions = ("买入判断", "加仓判断")
    if loaded.target_position is None and intent.action_type not in _buy_actions:
        asset_label = intent.asset or "该标的"
        result.stage = FlowStage.ABORTED
        result.aborted_reason = llm_engine.respond_not_in_portfolio(
            user_query=user_input,
            asset_name=asset_label,
        )
        return result

    # ── Step 5: 信号生成 ─────────────────────────────────────────────────────
    sig = signal_engine.generate(loaded, intent, rule_result)
    result.signals = sig
    result.stage = FlowStage.SIGNAL

    if loaded.target_position is not None:
        tp_weight = loaded.target_position.weight
        rule_weight = rule_result.current_weight
        if abs(tp_weight - rule_weight) > 1e-6:
            result.stage = FlowStage.ABORTED
            result.aborted_reason = (
                f"⚠️ **内部数据口径不一致**：\n\n"
                f"- 持仓详情中仓位：{tp_weight:.2%}\n"
                f"- 规则校验中仓位：{rule_weight:.2%}\n\n"
                f"数据管道存在异常，已中断结论输出，请联系开发者排查。"
            )
            return result

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
