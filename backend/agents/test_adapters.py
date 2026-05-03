"""Adapter 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


def test_discipline_adapter_pass():
    """DisciplineCheckOutput → RuleResult：通过场景。"""
    from backend.graph.tools import DisciplineCheckOutput
    from backend.agents.adapters import discipline_output_to_rule_result

    output = DisciplineCheckOutput(
        violation=False,
        warning=None,
        current_weight=0.10,
        max_position=0.40,
        position_ratio=0.25,
        rule_details=["仓位健康"],
    )

    rule_result = discipline_output_to_rule_result(output)

    assert rule_result.violation is False
    assert rule_result.warning is None
    assert rule_result.current_weight == 0.10
    assert rule_result.max_position == 0.40
    assert rule_result.position_ratio == 0.25
    assert rule_result.rule_details == ["仓位健康"]
    print(f"✅ Adapter pass 场景：6 字段全部正确映射")


def test_discipline_adapter_violation():
    """DisciplineCheckOutput → RuleResult：违规场景。"""
    from backend.graph.tools import DisciplineCheckOutput
    from backend.agents.adapters import discipline_output_to_rule_result

    output = DisciplineCheckOutput(
        violation=True,
        warning="超过单标仓位上限",
        current_weight=0.45,
        max_position=0.40,
        position_ratio=1.125,
        rule_details=["纪律 3 违反", "建议减仓"],
    )

    rule_result = discipline_output_to_rule_result(output)

    assert rule_result.violation is True
    assert rule_result.warning == "超过单标仓位上限"
    assert rule_result.current_weight == 0.45
    assert len(rule_result.rule_details) == 2
    assert hasattr(rule_result, "status_label")
    print(f"✅ Adapter violation 场景：违规字段正确，status_label property 可用")


def test_discipline_adapter_empty_rule_details():
    """rule_details 为空列表时正确处理。"""
    from backend.graph.tools import DisciplineCheckOutput
    from backend.agents.adapters import discipline_output_to_rule_result

    output = DisciplineCheckOutput(
        violation=False,
        warning=None,
        current_weight=0.0,
        max_position=0.40,
        position_ratio=0.0,
        rule_details=[],
    )

    rule_result = discipline_output_to_rule_result(output)
    assert rule_result.rule_details == []
    print(f"✅ Adapter 空 rule_details 正确处理")


def test_discipline_adapter_via_invoke_skill():
    """端到端：invoke_skill → Adapter → RuleResult。"""
    from backend.skills import invoke_skill
    from backend.agents.adapters import discipline_output_to_rule_result

    output = invoke_skill(
        "wp-check-discipline",
        asset_name="贵州茅台",
        portfolio_id=1,
        action_type="HOLD",
    )

    rule_result = discipline_output_to_rule_result(output)

    print(f"   violation: {rule_result.violation}")
    print(f"   current_weight: {rule_result.current_weight:.2%}")
    print(f"   max_position: {rule_result.max_position:.2%}")
    print(f"   warning: {rule_result.warning}")
    print(f"✅ end-to-end: invoke_skill → Adapter 正常工作")


if __name__ == "__main__":
    test_discipline_adapter_pass()
    test_discipline_adapter_violation()
    test_discipline_adapter_empty_rule_details()
    test_discipline_adapter_via_invoke_skill()
    print("\n🎉 Adapter 4/4 测试通过")
