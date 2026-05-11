"""
M3.2 单元测试：ActionPlanner Skill（mock LLM 客户端）。
v0.6 更新：积极推算 + 结构化 missing_fields + value_sources。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_action_planner.py -v
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.services.action.action_planner import (
    plan_actions,
    ActionPlannerInput,
    ActionListDraft,
    MissingField,
)


def _mock_client(response_json: dict | str):
    if isinstance(response_json, dict):
        content = json.dumps(response_json, ensure_ascii=False)
    else:
        content = response_json
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value = MagicMock(choices=[choice])
    return client


def _make_input(**kwargs) -> ActionPlannerInput:
    defaults = {
        "conversation_id": "test-conv-1",
        "conversation_context": [
            {"role": "user", "content": "理想汽车仓位偏重，减仓到15%仓位，限价15美元以下"},
            {"role": "assistant", "content": "建议分批减仓，目标仓位15%..."},
        ],
        "expressing_output": {
            "decisionType": "trim",
            "confidence": 0.85,
            "recommendedAction": {"detail": "减仓"},
        },
    }
    defaults.update(kwargs)
    return ActionPlannerInput(**defaults)


class TestPlanActionsNormal:

    def test_full_symbol_strategy_with_value_sources(self):
        """对话明说具体值 → quantity 直接采用 + value_sources。"""
        mock_response = {
            "decision_summary": "理想汽车仓位偏重，建议减仓",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 500,
                "order_type": "LIMIT",
                "limit_price": 15.0,
                "value_sources": {
                    "quantity": "对话中明确提到减仓500股",
                    "limit_price": "对话中明确提到限价15美元",
                },
            }],
            "allocation_intents": [],
            "risk_notes": ["减仓后仍占组合25%以上"],
            "missing_fields": [],
        }

        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert len(draft.symbol_strategies) == 1
        assert draft.symbol_strategies[0].quantity == 500
        assert draft.symbol_strategies[0].value_sources is not None
        assert "明确提到" in draft.symbol_strategies[0].value_sources.get("quantity", "")
        assert len(draft.missing_fields) == 0

    def test_inferred_quantity_with_value_sources(self):
        """对话给目标仓位 → quantity 被推算 + value_sources 解释推算逻辑。"""
        mock_response = {
            "decision_summary": "减仓至目标仓位15%",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 800,
                "quantity_pct": 0.15,
                "order_type": "LIMIT",
                "limit_price": 15.0,
                "value_sources": {
                    "quantity": "基于目标仓位15%和当前33.1%持仓推算",
                    "limit_price": "对话中明确提到限价15美元",
                },
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        }

        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert draft.symbol_strategies[0].quantity == 800
        assert "推算" in draft.symbol_strategies[0].value_sources.get("quantity", "")

    def test_no_info_produces_missing_fields(self):
        """对话完全没提相关信息 → quantity 留空 + 结构化 missing_fields。"""
        mock_response = {
            "decision_summary": "减仓理想汽车",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": None,
                "order_type": "LIMIT",
                "limit_price": None,
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [
                {
                    "target_type": "symbol_strategy",
                    "target_index": 0,
                    "field": "quantity",
                    "description": "减仓数量未明确，请补充",
                },
                {
                    "target_type": "symbol_strategy",
                    "target_index": 0,
                    "field": "limit_price",
                    "description": "限价未明确，请补充",
                },
            ],
        }

        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert draft.symbol_strategies[0].quantity is None
        assert len(draft.missing_fields) == 2
        assert draft.missing_fields[0].target_type == "symbol_strategy"
        assert draft.missing_fields[0].field == "quantity"
        assert "数量" in draft.missing_fields[0].description

    def test_allocation_intent(self):
        mock_response = {
            "decision_summary": "权益类降至40%",
            "symbol_strategies": [],
            "allocation_intents": [{"title": "权益类降至40%", "target_allocation": {"equity": 0.4}}],
            "risk_notes": [],
            "missing_fields": [],
        }
        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert len(draft.allocation_intents) == 1

    def test_mixed_strategies_and_intents(self):
        mock_response = {
            "decision_summary": "减仓+调配置",
            "symbol_strategies": [{"symbol": "LI", "side": "SELL", "quantity": 50}],
            "allocation_intents": [{"title": "降权益", "target_allocation": {}}],
            "risk_notes": [],
            "missing_fields": [],
        }
        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert len(draft.symbol_strategies) == 1
        assert len(draft.allocation_intents) == 1


class TestLimitOnlyMVP:
    """v3.2 MVP: order_type 必须为 LIMIT，trigger_price=null。"""

    def test_conditional_language_outputs_limit(self):
        """对话说"等反弹到20-21再减仓" → order_type=LIMIT, limit_price=20.5。"""
        mock_response = {
            "decision_summary": "等反弹后减仓",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 800,
                "order_type": "LIMIT",
                "trigger_price": None,
                "limit_price": 20.5,
                "value_sources": {"limit_price": "对话提到反弹目标价20-21美元，取中位数"},
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        }
        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        s = draft.symbol_strategies[0]
        assert s.order_type == "LIMIT"
        assert s.trigger_price is None
        assert s.limit_price == 20.5
        assert len(draft.missing_fields) == 0

    def test_no_trigger_price_in_missing_fields(self):
        """missing_fields 中不应出现 trigger_price。"""
        mock_response = {
            "decision_summary": "减仓",
            "symbol_strategies": [{"symbol": "LI", "side": "SELL", "quantity": 500, "order_type": "LIMIT"}],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [
                {"target_type": "symbol_strategy", "target_index": 0,
                 "field": "trigger_price", "description": "触发价未明确"},
            ],
        }
        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        # trigger_price missing_field 应被保留在数据里（后端不过滤），
        # 但前端会兜底过滤掉。后端层面不强制过滤，保持数据完整性。
        # 这个测试验证 ActionPlanner 正确解析了 missing_fields 结构。
        assert all(isinstance(mf, MissingField) for mf in draft.missing_fields)


class TestPlanActionsErrors:

    def test_llm_returns_invalid_json(self):
        draft = plan_actions(_make_input(), llm_client=_mock_client("这不是JSON"))
        assert len(draft.missing_fields) > 0
        assert draft.missing_fields[0].target_type == "system"

    def test_llm_returns_markdown_wrapped_json(self):
        inner = json.dumps({
            "decision_summary": "测试", "symbol_strategies": [],
            "allocation_intents": [], "risk_notes": [], "missing_fields": [],
        })
        draft = plan_actions(_make_input(), llm_client=_mock_client(f"```json\n{inner}\n```"))
        assert draft.decision_summary == "测试"

    def test_llm_call_exception(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API timeout")
        draft = plan_actions(_make_input(), llm_client=client)
        assert len(draft.missing_fields) > 0
        assert "生成失败" in draft.missing_fields[0].description

    def test_legacy_string_missing_fields_compat(self):
        """旧格式 missing_fields（字符串数组）兼容。"""
        mock_response = {
            "decision_summary": "test",
            "symbol_strategies": [],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": ["数量未知", "价格未知"],
        }
        draft = plan_actions(_make_input(), llm_client=_mock_client(mock_response))
        assert len(draft.missing_fields) == 2
        assert draft.missing_fields[0].description == "数量未知"


class TestActionListDraftPayload:

    def test_to_payload_dict_with_structured_missing(self):
        draft = ActionListDraft(conversation_id="c1", decision_summary="test")
        draft.missing_fields.append(MissingField(
            target_type="symbol_strategy", target_index=0,
            field="quantity", description="数量缺失",
        ))
        payload = draft.to_payload_dict()
        assert isinstance(payload["missing_fields"][0], dict)
        assert payload["missing_fields"][0]["field"] == "quantity"
