"""SkillsLoader 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")


def test_discover():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    loader.discover()
    names = loader.list_skill_names()
    print(f"✅ 发现 {len(names)} 个 Skill: {names}")
    assert "wealthpilot-position-decision" in names, \
        f"期望发现 wealthpilot-position-decision，实际 {names}"


def test_get_metadatas():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    metas = loader.get_skill_metadatas()
    assert len(metas) >= 1
    target = next((m for m in metas if m.name == "wealthpilot-position-decision"), None)
    assert target is not None
    assert "单一持仓标的" in target.description
    assert target.intent_binding == "PositionDecision"
    print(f"✅ Metadata 解析正确：")
    print(f"   name: {target.name}")
    print(f"   intent_binding: {target.intent_binding}")
    print(f"   description (前 80 字): {target.description[:80]}...")
    print(f"   tags: {target.tags}")
    print(f"   body 长度: {len(target.body)} 字符")


def test_load_body():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    body = loader.load_skill_body("wealthpilot-position-decision")
    assert body is not None
    assert len(body) > 500
    assert "工作流程" in body or "数据加载" in body
    print(f"✅ body 加载正确，长度 {len(body)} 字符")


def test_load_nonexistent():
    from backend.skills import get_skills_loader
    loader = get_skills_loader()
    body = loader.load_skill_body("does-not-exist")
    assert body is None
    print(f"✅ 不存在的 Skill 返回 None")


def test_references_exist():
    """验证 references/ 目录有文件。"""
    from pathlib import Path
    refs_dir = Path(__file__).parent.parent.parent / \
        "skills" / "wealthpilot-position-decision" / "references"
    assert refs_dir.exists()
    files = list(refs_dir.glob("*.md"))
    assert len(files) >= 2, f"期望至少 2 个 references 文件，实际 {len(files)}"
    print(f"✅ references/ 目录存在 {len(files)} 个文件: {[f.name for f in files]}")


if __name__ == "__main__":
    test_discover()
    test_get_metadatas()
    test_load_body()
    test_load_nonexistent()
    test_references_exist()
    print("\n🎉 SkillsLoader 5/5 测试通过")
