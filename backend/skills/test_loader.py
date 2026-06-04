"""SkillsLoader 单元测试（含 v3.0 invoke 能力）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


# ════════════════════════════════════════════════
# v2.6 原有测试
# ════════════════════════════════════════════════

def test_discover():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    loader._loaded = False
    loader.discover()
    names = loader.list_skill_names()
    print(f"✅ 发现 {len(names)} 个 Skill: {sorted(names)}")
    # v3.7: wealthpilot-position-decision 已归档删除，改用 wp-fetch-holdings 验证
    assert "wp-fetch-holdings" in names


def test_get_metadatas():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    loader._loaded = False
    loader.discover()
    metas = loader.get_skill_metadatas()
    assert len(metas) >= 1
    # v3.7: wealthpilot-position-decision 已归档删除，改用 wp-fetch-holdings 验证
    target = next((m for m in metas if m.name == "wp-fetch-holdings"), None)
    assert target is not None
    assert "持仓" in target.description
    print(f"✅ Metadata 解析正确")


def test_load_body():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    loader._loaded = False
    loader.discover()
    # v3.7: wealthpilot-position-decision 已归档删除，改用 wp-fetch-holdings 验证
    body = loader.load_skill_body("wp-fetch-holdings")
    assert body is not None
    assert len(body) > 100
    print(f"✅ body 加载正确，长度 {len(body)} 字符")


def test_load_nonexistent():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    loader._loaded = False
    loader.discover()
    body = loader.load_skill_body("does-not-exist")
    assert body is None
    print(f"✅ 不存在的 Skill 返回 None")


def test_references_archived():
    """v3.7: wealthpilot-position-decision 已归档至 docs/archived/，验证归档完整性。"""
    from pathlib import Path
    archived_dir = Path(__file__).parent.parent.parent / \
        "docs" / "archived" / "v3.0_position_decision_skill" / "references"
    assert archived_dir.exists(), "归档目录不存在"
    files = list(archived_dir.glob("*.md"))
    assert len(files) >= 2
    print(f"✅ 归档 references/ 目录存在 {len(files)} 个文件")


# ════════════════════════════════════════════════
# v3.0 Step 7c：invoke 能力测试
# ════════════════════════════════════════════════

def test_skill_meta_extended_fields():
    """验证 SkillMeta 的扩展字段。"""
    from backend.skills import get_skills_loader

    loader = get_skills_loader()
    loader._loaded = False
    loader.discover()

    expected = [
        ("wp-fetch-holdings", "function_call", "fetch_holdings"),
        ("wp-generate-signals", "function_call", "generate_signals"),
        ("wp-output-validator", "validation", None),
        ("wp-citation-rules", "prompt_inject", None),
        ("wp-reasoning", "llm_dispatch", None),
    ]

    for skill_name, expected_type, expected_tool in expected:
        meta = loader.get_skill(skill_name)
        assert meta is not None, f"找不到 {skill_name}"
        assert meta.type == expected_type, \
            f"{skill_name} type={meta.type} (expect {expected_type})"
        if expected_tool:
            assert meta.tool_name == expected_tool, \
                f"{skill_name} tool_name={meta.tool_name} (expect {expected_tool})"

    print(f"✅ SkillMeta 扩展字段全部正确")


def test_invoke_function_call():
    """function_call 类型：通过 M2 Tool 调用。"""
    from backend.skills import invoke_skill

    result = invoke_skill("wp-fetch-holdings", portfolio_id=1)
    assert hasattr(result, "positions") or hasattr(result, "count")
    print(f"✅ wp-fetch-holdings invoke 成功")

    result = invoke_skill("wp-calc-allocation-deviation", portfolio_id=1)
    assert hasattr(result, "by_class")
    print(f"✅ wp-calc-allocation-deviation invoke 成功")

    result = invoke_skill(
        "wp-generate-signals",
        asset_name="贵州茅台",
        portfolio_id=1,
    )
    assert hasattr(result, "position_signal")
    print(f"✅ wp-generate-signals invoke: position={result.position_signal}")


def test_invoke_prompt_inject():
    """prompt_inject 类型：返回 SKILL.md body。"""
    from backend.skills import invoke_skill

    body = invoke_skill("wp-citation-rules")
    assert isinstance(body, str)
    assert len(body) > 100
    assert "引用规则" in body or "数据引用" in body
    print(f"✅ wp-citation-rules invoke: {len(body)} 字符 body")


def test_invoke_validation():
    """validation 类型：调用 validate_decision_output。"""
    from backend.skills import invoke_skill
    from decision_engine.llm_engine import LLMResult

    llm_result = LLMResult(
        decision="HOLD",
        reasoning=["理由1", "理由2"],
        risk=["风险1"],
        strategy=["策略1"],
        chat_answer="### 结论\n建议持有当前仓位，理由是市场环境稳定。这是一段足够长的回答。",
        raw_output="...",
    )

    result = invoke_skill(
        "wp-output-validator",
        result=llm_result,
        intent_type="PositionDecision",
    )
    assert hasattr(result, "passed")
    print(f"✅ wp-output-validator invoke: passed={result.passed}")


def test_invoke_llm_dispatch_not_implemented():
    """llm_dispatch 类型：未支持的 template_id 抛 NotImplementedError。"""
    from backend.skills import invoke_skill

    try:
        invoke_skill("wp-reasoning", prompt_template_id="position_decision")
        assert False, "应该抛 NotImplementedError"
    except NotImplementedError as e:
        assert "待 C6" in str(e) or "position_decision" in str(e)
        print(f"✅ wp-reasoning 正确抛 NotImplementedError")


def test_invoke_unknown_skill():
    """不存在的 Skill：抛 ValueError。"""
    from backend.skills import invoke_skill

    try:
        invoke_skill("does-not-exist", x=1)
        assert False, "应该抛 ValueError"
    except ValueError as e:
        assert "不存在" in str(e)
        print(f"✅ 不存在的 Skill 抛 ValueError")


if __name__ == "__main__":
    test_discover()
    test_get_metadatas()
    test_load_body()
    test_load_nonexistent()
    test_references_archived()
    # v3.0 Step 7c
    test_skill_meta_extended_fields()
    test_invoke_function_call()
    test_invoke_prompt_inject()
    test_invoke_validation()
    test_invoke_llm_dispatch_not_implemented()
    test_invoke_unknown_skill()
    print("\n🎉 SkillsLoader 11/11 测试通过（含 v3.0 invoke 能力）")
