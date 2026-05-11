"""
M3.1 单元测试：actionable 硬规则判断。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_actionable.py -v
"""
import pytest

from backend.agents.expressing_agent import _is_actionable


class _FakeOutput:
    """模拟 ExpressionOutput 的最小结构。"""
    def __init__(self, structured_payload=None):
        self.structured_payload = structured_payload


class TestIsActionable:

    def test_buy_init_is_actionable(self):
        out = _FakeOutput({"decisionType": "buy_init"})
        actionable, hint = _is_actionable(out)
        assert actionable is True
        assert "建仓" in hint

    def test_buy_more_is_actionable(self):
        out = _FakeOutput({"decisionType": "buy_more"})
        actionable, hint = _is_actionable(out)
        assert actionable is True
        assert "加仓" in hint

    def test_trim_is_actionable(self):
        out = _FakeOutput({"decisionType": "trim"})
        actionable, hint = _is_actionable(out)
        assert actionable is True
        assert "减仓" in hint

    def test_exit_is_actionable(self):
        out = _FakeOutput({"decisionType": "exit"})
        actionable, hint = _is_actionable(out)
        assert actionable is True
        assert "清仓" in hint

    def test_hold_not_actionable(self):
        out = _FakeOutput({"decisionType": "hold"})
        actionable, hint = _is_actionable(out)
        assert actionable is False
        assert hint is None

    def test_wait_not_actionable(self):
        out = _FakeOutput({"decisionType": "wait"})
        actionable, hint = _is_actionable(out)
        assert actionable is False
        assert hint is None

    def test_need_info_not_actionable(self):
        out = _FakeOutput({"decisionType": "need_info"})
        actionable, hint = _is_actionable(out)
        assert actionable is False

    def test_none_payload_safe(self):
        out = _FakeOutput(None)
        actionable, hint = _is_actionable(out)
        assert actionable is False
        assert hint is None

    def test_empty_payload_safe(self):
        out = _FakeOutput({})
        actionable, hint = _is_actionable(out)
        assert actionable is False

    def test_missing_decision_type_safe(self):
        out = _FakeOutput({"rationale": ["some reason"]})
        actionable, hint = _is_actionable(out)
        assert actionable is False

    def test_no_structured_payload_attr(self):
        """对象完全没有 structured_payload 属性。"""

        class Bare:
            pass

        actionable, hint = _is_actionable(Bare())
        assert actionable is False
