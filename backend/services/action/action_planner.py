"""
WealthPilot v3.2 ActionPlanner Skill。

职责：把投资决策对话上下文翻译为结构化的 ActionListDraft。
触发方式：仅用户点击"生成行动清单"按钮时调用，不走 LLM Skill Selector。

v0.6 改造：
- "积极推算"模式：优先从对话推算 quantity/limit_price，仅真的推算不出才放 missing_fields
- missing_fields 从 List[str] 改为 List[MissingField]（结构化，前端可精确定位）
- value_sources 字段：每个被推算填充的字段说明推算依据
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 数据契约
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MissingField:
    """结构化缺失字段（v0.6）。"""
    target_type: str = ""    # "symbol_strategy" / "allocation_intent"
    target_index: int = 0    # 在数组中的索引
    field: str = ""          # 字段名
    description: str = ""    # 给用户看的文案


@dataclass
class AllocationIntentDraft:
    """资产配置调整意图草稿。"""
    title: str = ""
    target_allocation: dict = field(default_factory=dict)


@dataclass
class SymbolStrategyDraft:
    """标的策略草稿。"""
    symbol: str = ""
    side: str = "BUY"
    quantity: Optional[int] = None
    quantity_pct: Optional[float] = None
    order_type: str = "LIMIT"
    trigger_price: Optional[float] = None
    limit_price: Optional[float] = None
    parent_intent_index: Optional[int] = None
    value_sources: Optional[dict] = None  # v0.6: 每个被推算字段的依据


@dataclass
class ActionListDraft:
    """行动清单草稿（ActionPlanner 的输出）。"""
    conversation_id: str = ""
    decision_summary: str = ""
    allocation_intents: list[AllocationIntentDraft] = field(default_factory=list)
    symbol_strategies: list[SymbolStrategyDraft] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    missing_fields: list[MissingField] = field(default_factory=list)

    def to_payload_dict(self) -> dict:
        """转为 action_drafts.payload 的 JSON 结构。"""
        return {
            "symbol_strategies": [asdict(s) for s in self.symbol_strategies],
            "allocation_intents": [asdict(a) for a in self.allocation_intents],
            "risk_notes": self.risk_notes,
            "missing_fields": [asdict(m) for m in self.missing_fields],
        }


@dataclass
class ActionPlannerInput:
    """ActionPlanner 的输入。"""
    conversation_id: str = ""
    conversation_context: list[dict] = field(default_factory=list)
    expressing_output: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Prompt（v0.6: 积极推算模式）
# ═══════════════════════════════════════════════════════════════════

_ACTION_PLANNER_PROMPT = """\
你是 WealthPilot 投资行动规划助手。你的任务是从投资决策对话中提取用户已认可的交易动作，
翻译为结构化的行动清单草稿。

**核心原则——积极推算模式**：

你的目标是让行动清单尽可能完整，减少用户需要手动填写的字段。按以下优先级填充每个字段：

1. **优先级 1：对话明说** — 用户在对话中给出了具体值（如"减500股""限价32美元"）→ 直接使用
2. **优先级 2：可推算** — 对话提供了间接信息，你可以推算出合理值：
   - 目标仓位百分比 + 当前持仓数据 → 推算具体股数。
     使用 estimated_shares（系统已反算的当前持仓股数）：
     减仓股数 = estimated_shares × (当前仓位% - 目标仓位%) / 当前仓位%
     例："仓位由 33.1% 降至 15%"，estimated_shares=2800 → 减仓 = 2800 × (33.1-15)/33.1 ≈ 1530 股
   - "反弹到20-21美元" → 取中位数 20.5 作为 limit_price
   - limit_price 推算规则（重要！）：
     * 对话中明确提到限价 → 使用对话中的值
     * 对话提到价格区间（如"20-21美元"）→ 取中位数
     * 对话没提限价但当前股价已知（current_price）→ 用当前价：
       SELL 时 limit_price = current_price（当前价挂单，用户可调）
       BUY 时 limit_price = current_price（当前价挂单，用户可调）
     * value_sources 中说明："基于当前价 $X 推算（默认值，可编辑）"
     * 只有 current_price 完全未知（None/0）时，才将 limit_price 放入 missing_fields
   - "减到15%仓位" → quantity_pct = 0.15
   - AI分析建议"分批减仓，第一批X股" → 使用该建议值
   - 当前持仓数据可从 expressing_output 或对话上下文中获取（如"当前持仓 2800 股""仓位占比 33.1%"）
3. **优先级 3：真的推算不出** — 只有上述两类都无法获得时，才放进 missing_fields

**诚实表达**：每个被推算（优先级2）填充的字段，必须在 value_sources 中说明推算依据。
不允许"不解释就给数字"。优先级1的字段也要在 value_sources 标注"对话中明确提到"。

**输出要求**：严格 JSON 格式：

```json
{
  "decision_summary": "200字内的决策依据摘要",
  "symbol_strategies": [
    {
      "symbol": "标的代码，格式为 TICKER:MARKET（如 LI:US、0700:HK、600519:SH）。不要输出中文名。",
      "side": "BUY 或 SELL",
      "quantity": null 或整数,
      "quantity_pct": null 或小数,
      "order_type": "LIMIT",
      "trigger_price": null,
      "limit_price": null 或数字,
      "parent_intent_index": null 或整数,
      "value_sources": {
        "quantity": "推算依据说明",
        "limit_price": "推算依据说明"
      }
    }
  ],
  "allocation_intents": [
    {
      "title": "配置意图标题",
      "target_allocation": {"equity": 0.40}
    }
  ],
  "risk_notes": ["风险提示"],
  "missing_fields": [
    {
      "target_type": "symbol_strategy",
      "target_index": 0,
      "field": "limit_price",
      "description": "限价未明确，请补充"
    }
  ]
}
```

**字段规则**：
- **order_type 必须固定为 "LIMIT"**（v3.2 MVP 不支持 CONDITIONAL_LIMIT）
- **trigger_price 必须固定为 null**（不允许输出非 null 值）
- 即使对话中提到"等价格跌到 X 再买"或"反弹到 Y 再卖"，也输出 LIMIT，
  把目标价直接作为 limit_price，不要拆成 trigger_price + limit_price
- quantity 和 quantity_pct 至少有一个非 null。如果都推算不出，加入 missing_fields
- **missing_fields 中不允许出现 field="trigger_price" 的条目**
- missing_fields 必须是结构化对象数组（含 target_type / target_index / field / description）
- value_sources 只对有值的字段标注，null 字段不标注

只输出 JSON，不要有其他文字。
"""


# ═══════════════════════════════════════════════════════════════════
# ActionPlanner 核心函数
# ═══════════════════════════════════════════════════════════════════

def plan_actions(
    input_data: ActionPlannerInput,
    llm_client=None,
) -> ActionListDraft:
    """
    把对话上下文翻译为 ActionListDraft。
    """
    conversation_id = input_data.conversation_id

    messages = [{"role": "system", "content": _ACTION_PLANNER_PROMPT}]

    for turn in input_data.conversation_context:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            if role == "assistant" and len(content) > 1000:
                content = content[:600] + "\n…（中间省略）…\n" + content[-400:]
            messages.append({"role": role, "content": content})

    expr = input_data.expressing_output
    if expr:
        parts = [
            "AI 最近一次分析结论：",
            f"- 决策类型：{expr.get('decisionType', '未知')}",
            f"- 置信度：{expr.get('confidence', '未知')}",
            f"- 标的：{expr.get('asset', '未知')}",
            f"- 操作建议：{expr.get('recommendedAction', {}).get('detail', '无') if isinstance(expr.get('recommendedAction'), dict) else '无'}",
        ]
        # P1: 注入持仓数据供推算 quantity
        tp = expr.get("target_position")
        if tp and isinstance(tp, dict):
            parts.append(f"\n当前持仓数据（用于推算股数）：")
            parts.append(f"- 标的名称：{tp.get('name', '未知')}")
            parts.append(f"- 当前仓位占比：{tp.get('weight', '未知')}")
            parts.append(f"- 市值（CNY）：{tp.get('market_value_cny', '未知')}")
            if tp.get('current_price'):
                parts.append(f"- 当前股价：{tp['current_price']} {tp.get('currency', 'USD')}")
            if tp.get('estimated_shares'):
                parts.append(f"- 估算当前持仓股数：{tp['estimated_shares']} 股（基于市值/股价反算）")
            parts.append(f"- 盈亏率：{tp.get('profit_loss_rate', '未知')}")
            parts.append(f"- 平台：{tp.get('platforms', [])}")
        total = expr.get("total_assets")
        if total:
            parts.append(f"- 组合总市值：{total}")

        messages.append({"role": "user", "content": "\n".join(parts)})

    messages.append({
        "role": "user",
        "content": "请基于以上对话内容和持仓数据，积极推算所有可推算的字段，生成行动清单草稿（JSON）。",
    })

    try:
        client = llm_client or _get_default_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            max_tokens=2000,
            temperature=0,
            timeout=15,
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ActionPlanner] LLM 调用失败: {e}")
        return ActionListDraft(
            conversation_id=conversation_id,
            decision_summary="行动清单生成失败",
            missing_fields=[MissingField(
                target_type="system", target_index=0,
                field="all", description=f"AI 生成失败，请手动填写: {e}",
            )],
        )

    try:
        data = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[ActionPlanner] JSON 解析失败: {e}, raw={raw[:200]}")
        return ActionListDraft(
            conversation_id=conversation_id,
            decision_summary="行动清单解析失败",
            missing_fields=[MissingField(
                target_type="system", target_index=0,
                field="all", description="AI 返回格式异常，请手动填写或重试",
            )],
        )

    # 构建 ActionListDraft
    draft = ActionListDraft(
        conversation_id=conversation_id,
        decision_summary=data.get("decision_summary", ""),
    )

    for s in data.get("symbol_strategies", []):
        raw_symbol = s.get("symbol", "")
        # symbol 格式校验/修正: 期望 TICKER:MARKET, 拒绝中文名
        if raw_symbol and ":" not in raw_symbol:
            import re as _re
            if _re.search(r"[\u4e00-\u9fff]", raw_symbol):
                logger.warning(
                    f"[ActionPlanner] LLM 输出中文 symbol '{raw_symbol}'，跳过该策略"
                )
                continue
            logger.warning(
                f"[ActionPlanner] symbol '{raw_symbol}' 缺少 :MARKET 后缀，"
                f"请在下次 prompt 中明确格式要求"
            )
        draft.symbol_strategies.append(SymbolStrategyDraft(
            symbol=raw_symbol,
            side=s.get("side", "BUY"),
            quantity=s.get("quantity"),
            quantity_pct=s.get("quantity_pct"),
            order_type=s.get("order_type", "LIMIT"),
            trigger_price=s.get("trigger_price"),
            limit_price=s.get("limit_price"),
            parent_intent_index=s.get("parent_intent_index"),
            value_sources=s.get("value_sources"),
        ))

    for a in data.get("allocation_intents", []):
        draft.allocation_intents.append(AllocationIntentDraft(
            title=a.get("title", ""),
            target_allocation=a.get("target_allocation", {}),
        ))

    draft.risk_notes = data.get("risk_notes", [])

    # 解析结构化 missing_fields
    for mf in data.get("missing_fields", []):
        if isinstance(mf, dict):
            draft.missing_fields.append(MissingField(
                target_type=mf.get("target_type", ""),
                target_index=mf.get("target_index", 0),
                field=mf.get("field", ""),
                description=mf.get("description", ""),
            ))
        elif isinstance(mf, str):
            # 兼容旧格式
            draft.missing_fields.append(MissingField(
                target_type="unknown", target_index=0,
                field="unknown", description=mf,
            ))

    logger.info(
        f"[ActionPlanner] 生成完成: strategies={len(draft.symbol_strategies)}, "
        f"intents={len(draft.allocation_intents)}, "
        f"missing={len(draft.missing_fields)}"
    )

    return draft


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _get_default_client():
    import openai
    api_key = os.environ.get("WEALTHPILOT_OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("WEALTHPILOT_OPENAI_API_KEY 未配置")
    return openai.OpenAI(api_key=api_key)


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)
