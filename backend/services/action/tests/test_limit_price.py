"""
M7.1.5-C: limit_price 预填防复发测试。

测试 ActionPlanner prompt 是否正确指导 LLM 积极推算 limit_price。
使用 mock LLM 返回，验证解析逻辑。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_limit_price.py -v
"""
import json
from unittest.mock import MagicMock, patch

from backend.services.action.action_planner import (
    plan_actions, ActionPlannerInput, ActionListDraft,
)


def _mock_llm_response(content: str):
    """构造 mock OpenAI chat completion response。"""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = content
    return mock_resp


def _make_input(expressing_output: dict = None) -> ActionPlannerInput:
    return ActionPlannerInput(
        conversation_id="test-session",
        conversation_context=[
            {"role": "user", "content": "理想汽车仓位偏重，要不要减仓"},
            {"role": "assistant", "content": "建议将理想汽车仓位从33.1%降至15%，分批减仓。"},
        ],
        expressing_output=expressing_output or {
            "decisionType": "trim",
            "confidence": 0.85,
            "asset": "理想汽车",
            "recommendedAction": {"detail": "建议减仓至15%"},
            "target_position": {
                "name": "理想汽车",
                "weight": 0.331,
                "market_value_cny": 486625,
                "current_price": 18.0,
                "currency": "USD",
                "estimated_shares": 2800,
                "profit_loss_rate": -0.317,
                "platforms": ["老虎证券", "雪盈证券"],
            },
            "total_assets": 1470000,
        },
    )


class TestLimitPricePreFill:
    """limit_price 预填测试套件。"""

    def test_limit_price_uses_current_price_when_no_user_input(self):
        """对话没明确限价 + current_price 已知 → 用 current_price 作为默认值"""
        llm_output = json.dumps({
            "decision_summary": "减仓理想汽车",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 1530,
                "order_type": "LIMIT",
                "limit_price": 18.0,
                "value_sources": {
                    "quantity": "基于当前持仓 2800 股，从 33.1% 降至 15%",
                    "limit_price": "基于当前价 $18 推算",
                },
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_output)

        result = plan_actions(_make_input(), llm_client=mock_client)

        assert len(result.symbol_strategies) == 1
        s = result.symbol_strategies[0]
        # limit_price 应非 None，且在 current_price ±5% 范围内
        assert s.limit_price is not None
        assert 17.0 <= s.limit_price <= 19.0, f"limit_price={s.limit_price} 不在合理范围"
        # missing_fields 不应包含 limit_price
        assert not any(mf.field == "limit_price" for mf in result.missing_fields)

    def test_limit_price_uses_user_specified_value(self):
        """对话明确说了限价 → 用用户值"""
        llm_output = json.dumps({
            "decision_summary": "挂限价 19 卖出理想汽车",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 1530,
                "order_type": "LIMIT",
                "limit_price": 19.0,
                "value_sources": {
                    "quantity": "推算",
                    "limit_price": "对话中明确提到挂限价 19",
                },
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_output)

        inp = _make_input()
        inp.conversation_context.append(
            {"role": "user", "content": "挂限价19卖"},
        )
        result = plan_actions(inp, llm_client=mock_client)

        assert result.symbol_strategies[0].limit_price == 19.0

    def test_limit_price_missing_when_current_price_unknown(self):
        """current_price=None + 对话没说限价 → 标记 missing_fields"""
        llm_output = json.dumps({
            "decision_summary": "减仓某标的",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 1530,
                "order_type": "LIMIT",
                "limit_price": None,
                "value_sources": {"quantity": "推算"},
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [{
                "target_type": "symbol_strategy",
                "target_index": 0,
                "field": "limit_price",
                "description": "当前价格不可用，请手动填写限价",
            }],
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_output)

        # 不传 current_price
        inp = _make_input(expressing_output={
            "decisionType": "trim",
            "confidence": 0.85,
            "asset": "理想汽车",
            "recommendedAction": {"detail": "减仓"},
            "target_position": {
                "name": "理想汽车",
                "weight": 0.331,
                "market_value_cny": 486625,
                "profit_loss_rate": -0.317,
                "platforms": ["老虎证券"],
            },
        })
        result = plan_actions(inp, llm_client=mock_client)

        assert result.symbol_strategies[0].limit_price is None
        assert any(mf.field == "limit_price" for mf in result.missing_fields)

    def test_limit_price_midpoint_for_range(self):
        """对话给了价格区间 → 取中位数"""
        llm_output = json.dumps({
            "decision_summary": "在 18-20 区间挂限价卖出",
            "symbol_strategies": [{
                "symbol": "LI",
                "side": "SELL",
                "quantity": 1530,
                "order_type": "LIMIT",
                "limit_price": 19.0,
                "value_sources": {
                    "quantity": "推算",
                    "limit_price": "对话提到 18-20 区间，取中位数 19",
                },
            }],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(llm_output)

        inp = _make_input()
        inp.conversation_context.append(
            {"role": "user", "content": "挂限价18到20之间卖"},
        )
        result = plan_actions(inp, llm_client=mock_client)

        assert result.symbol_strategies[0].limit_price == 19.0
        assert not any(mf.field == "limit_price" for mf in result.missing_fields)
