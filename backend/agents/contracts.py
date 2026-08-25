"""
WealthPilot v3.0 Multi-Agent 数据契约。

设计原则：
1. 字段命名对齐 Google A2A Task 协议（task_id / status / artifacts），
   v4.0 切换 A2A 协议时零迁移成本。
2. 强类型 dataclass，配合 IDE 自动补全和类型检查。
3. 每个 Agent 的输出是下一个 Agent 的输入，形成清晰的契约链。
4. 包含 trace 字段方便调试和评测。

PEER 协作流程：
    User Query
        ↓
    PlanningAgent.run(query) → PlanningOutput
        ↓
    ExecutingAgent.run(plan) → ExecutionOutput
        ↓
    ExpressingAgent.run_streaming(plan, exec) → AsyncGen[chunk]
                                              → ExpressionOutput
        ↓
    ReviewingAgent.run(plan, exec, expr) → ReviewOutput
        ↓
    SSE stream output
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backend.services.technical_patterns.decision_integration import (
        DecisionPatternEvidenceSnapshot,
    )

# 复用现有类型定义，避免循环依赖
# 这些类型已经在 v2.6 阶段稳定，v3.0 不修改
from decision_engine.types import IntentResult


# ════════════════════════════════════════════════════════════
# A2A 协议对齐枚举
# ════════════════════════════════════════════════════════════

class AgentTaskStatus(str, Enum):
    """
    Agent 任务状态，对齐 Google A2A 协议 Task 状态枚举。

    A2A v1.0 标准状态：
    - pending：任务已创建，等待执行
    - in_progress：执行中
    - completed：成功完成
    - failed：执行失败
    - skipped：被跳过（如 jump_step 跳过某个 Agent）
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ════════════════════════════════════════════════════════════
# Stage 1: PlanningAgent 输出
# ════════════════════════════════════════════════════════════

@dataclass
class PlanningOutput:
    """
    PlanningAgent 输出契约。

    职责：意图识别 + Skill 组合选择 + 路由决策。

    A2A 对齐：task_id / status 字段直接映射到 A2A Task 对象。
    """
    # === A2A 协议对齐字段 ===
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    # === 意图识别结果 ===
    intent: Optional[IntentResult] = None

    # === 路由决策（兼容 v2.6 路由协议）===
    route: str = ""               # position_single / position_multi / portfolio / general / clarify / low_confidence
    sse_handler: str = ""         # 兼容 v2.6 字符串分派（v3.1 删除）
    rationale: str = ""           # LLM Planner 给出的路由理由

    # === Skill 选择（v3.0 新增）===
    selected_skills: list[str] = field(default_factory=list)
    """
    本次决策应该调用的 Skill 名称列表，由 LLM Skill Selector 输出。
    例：["wp-fetch-holdings", "wp-fetch-research", "wp-check-discipline",
         "wp-generate-signals", "wp-reasoning"]

    PlanningAgent 选定后，ExecutingAgent 按此列表顺序调用。
    """

    # === 路由参数（透传给 SSE 层）===
    portfolio_id: int = 1
    multi_assets: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    candidate_holdings: list[dict] = field(default_factory=list)
    clarify_question: str = ""

    # === 调试与评测 trace ===
    memory_context: list[dict] = field(default_factory=list)
    """从 DecisionHistory 读取的多轮对话上下文。"""

    def mark_completed(self) -> None:
        self.status = AgentTaskStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = AgentTaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    @property
    def duration_ms(self) -> Optional[int]:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at) * 1000)


# ════════════════════════════════════════════════════════════
# Stage 2: ExecutingAgent 输出
# ════════════════════════════════════════════════════════════

@dataclass
class ExecutionOutput:
    """
    ExecutingAgent 输出契约。

    职责：按 PlanningOutput.selected_skills 顺序调用数据获取类 + 计算分析类 Skills，
          得到结构化的执行上下文。
    """
    # === A2A 协议对齐字段 ===
    task_id: str = ""             # 继承自 PlanningOutput.task_id
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    # === Skill 调用记录（trace）===
    invoked_skills: list[str] = field(default_factory=list)
    """实际调用的 Skill 名称列表（按顺序）。"""

    skill_results: dict = field(default_factory=dict)
    """
    每个 Skill 的执行结果，key 是 Skill 名称。
    例：{
        "wp-fetch-holdings": {<LoadedData>},
        "wp-fetch-research": {<research items>},
        "wp-check-discipline": {<RuleResult>},
        "wp-generate-signals": {<SignalResult>},
    }
    """

    # === 核心结构化产出（供 ExpressingAgent 消费）===
    loaded_data: Optional[object] = None      # decision_engine.data_loader.LoadedData
    pre_check_result: Optional[object] = None # decision_engine.pre_check.PreCheckResult
    rule_result: Optional[object] = None      # decision_engine.rule_engine.RuleResult
    signal_result: Optional[object] = None    # decision_engine.signal_engine.SignalResult
    market_data: Optional[object] = None      # services.market_data.schema.MarketDataBundle
    pattern_evidence: Optional["DecisionPatternEvidenceSnapshot"] = None
    """Optional read-only Decision supporting evidence; never execution authority."""

    # === Stage 中断标记（数据加载失败 / 前置校验失败时）===
    aborted: bool = False
    abort_reason: str = ""
    abort_chat_answer: str = ""               # ABORT 时给用户的提示

    def mark_completed(self) -> None:
        self.status = AgentTaskStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = AgentTaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    def mark_aborted(self, reason: str, chat_answer: str = "") -> None:
        """ABORT 不是失败，是流程上的合理中止（如未持仓+非买入）。"""
        self.aborted = True
        self.abort_reason = reason
        self.abort_chat_answer = chat_answer
        self.status = AgentTaskStatus.SKIPPED
        self.completed_at = time.time()

    @property
    def duration_ms(self) -> Optional[int]:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at) * 1000)


# ════════════════════════════════════════════════════════════
# Stage 3: ExpressingAgent 输出
# ════════════════════════════════════════════════════════════

@dataclass
class ExpressionOutput:
    """
    ExpressingAgent 输出契约。

    职责：
    1. 加载意图对应的 prompt 模板
    2. 注入 wp-citation-rules Skill 内容
    3. 调用 LLM（GPT-4.1）
    4. 流式输出 chat_answer 给用户
    5. 返回完整 LLMResult / GenericLLMResult

    特殊点：ExpressingAgent 是唯一的 AsyncGenerator Agent（流式输出）。
            run_streaming 方法在流式过程中 yield text chunk，最终通过
            this contract 返回完整结果。
    """
    # === A2A 协议对齐字段 ===
    task_id: str = ""
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    # === Prompt trace（v3.0 关键能力）===
    prompt_template_id: str = ""
    """加载的 prompt 模板 ID（如 'position_decision' / 'portfolio_review'）。"""

    citation_rules_applied: list[str] = field(default_factory=list)
    """注入的输出规范 Skills 列表（如 ['wp-citation-rules']）。"""

    # === LLM 推理结果（核心产出）===
    llm_result: Optional[object] = None
    """
    LLMResult 或 GenericLLMResult 对象。
    PositionDecision → LLMResult
    PortfolioReview / AssetAllocation / PerformanceAnalysis → GenericLLMResult
    """

    chat_answer: str = ""
    """完整的自然语言回答（LLM 输出的 chat_answer 字段）。"""

    structured_payload: dict = field(default_factory=dict)
    """完整结构化输出（除 chat_answer 外的所有 JSON 字段）。"""

    # === Mode（与 v2.6 SSE 协议对齐）===
    mode: str = "structured"
    """'structured'（正常）| 'fallback'（LLM 出错降级）"""

    raw_text: str = ""
    """LLM 原始输出文本，用于调试。"""

    # === v3.2 投资行动模块（actionable 信号）===
    actionable: bool = False
    """本次回复是否包含可执行的投资决策（买入/卖出/调仓建议）。"""

    actionable_hint: Optional[str] = None
    """可读提示，如"识别到加仓建议"。前端用于高亮"生成行动清单"按钮。"""

    def mark_completed(self) -> None:
        self.status = AgentTaskStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = AgentTaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error
        self.mode = "fallback"

    @property
    def duration_ms(self) -> Optional[int]:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at) * 1000)


# ════════════════════════════════════════════════════════════
# Stage 4: ReviewingAgent 输出
# ════════════════════════════════════════════════════════════

@dataclass
class ReviewOutput:
    """
    ReviewingAgent 输出契约。

    职责：
    1. 调用 wp-output-validator Skill 做硬校验
    2. LLM 评分（0-1）输出质量
    3. 决策：通过 / 重试 / 降级

    评分机制（来自 agentUniverse PEER 启发）：
        score >= 0.8       → action="pass"，通过
        0.5 <= score < 0.8 → action="retry"，jump_step="expressing"
        score < 0.5        → action="retry"，jump_step="executing"
        重试 3 次仍失败    → action="fallback"
    """
    # === A2A 协议对齐字段 ===
    task_id: str = ""
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    # === 硬校验结果（v2.6 已有）===
    hard_validation_passed: bool = True
    failed_rules: list[str] = field(default_factory=list)
    failure_messages: list[str] = field(default_factory=list)

    # === 评分机制（v3.0 新增）===
    score: float = 1.0
    """0-1 之间的输出质量评分。"""

    score_rationale: str = ""
    """评分理由（LLM 给出的解释）。"""

    # === 决策动作 ===
    action: str = "pass"
    """'pass'（通过）| 'retry'（重试）| 'fallback'（降级）"""

    jump_step: Optional[str] = None
    """重试时跳到哪一步：'executing' | 'expressing' | None。"""

    retry_count: int = 0
    """已重试次数。"""

    # === 警告（不阻断，只通知前端）===
    warnings: list[str] = field(default_factory=list)

    def mark_completed(self) -> None:
        self.status = AgentTaskStatus.COMPLETED
        self.completed_at = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = AgentTaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    @property
    def duration_ms(self) -> Optional[int]:
        if self.completed_at is None:
            return None
        return int((self.completed_at - self.started_at) * 1000)

    @property
    def passed(self) -> bool:
        """是否通过审查（兼容 v2.6 ValidationResult.passed 接口）。"""
        return self.action == "pass"
