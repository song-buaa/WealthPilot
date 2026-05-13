"""
wp-retrieve-principles Skill 单元测试。

测试 Tool 层的 execute_retrieve_principles 函数，
以及 SkillsLoader 能发现并注册此 Skill。
"""
import os
import pytest

HAS_API_KEY = bool(os.getenv("OPENAI_API_KEY"))
skip_no_key = pytest.mark.skipif(not HAS_API_KEY, reason="OPENAI_API_KEY not set")


class TestRetrievePrinciplesTool:
    """测试 backend.graph.tools.execute_retrieve_principles。"""

    def test_empty_query_returns_empty(self):
        from backend.graph.tools import execute_retrieve_principles
        result = execute_retrieve_principles(query="")
        assert result["chunks"] == []
        assert result["total_retrieved"] == 0

    def test_none_query_returns_empty(self):
        from backend.graph.tools import execute_retrieve_principles
        result = execute_retrieve_principles(query=None)
        assert result["chunks"] == []
        assert result["total_retrieved"] == 0

    @skip_no_key
    def test_normal_query_returns_chunks(self):
        from backend.graph.tools import execute_retrieve_principles
        result = execute_retrieve_principles(
            query="什么是动态再平衡",
            source_types=["allocation_principles"],
            top_k=3,
        )
        assert result["total_retrieved"] > 0
        chunk = result["chunks"][0]
        assert "content" in chunk
        assert chunk["source_type"] == "allocation_principles"
        assert chunk["source_channel"] == "local_principles"

    @skip_no_key
    def test_source_type_filter(self):
        """只传 allocation_principles 时不返回其他类型。"""
        from backend.graph.tools import execute_retrieve_principles
        result = execute_retrieve_principles(
            query="投资纪律",
            source_types=["allocation_principles"],
            top_k=5,
        )
        for chunk in result["chunks"]:
            assert chunk["source_type"] == "allocation_principles"

    @skip_no_key
    def test_default_source_types(self):
        """不传 source_types 时默认全部三类。"""
        from backend.graph.tools import execute_retrieve_principles
        result = execute_retrieve_principles(
            query="投资决策",
            top_k=10,
        )
        assert result["total_retrieved"] > 0


class TestSkillRegistration:
    """测试 SkillsLoader 能发现 wp-retrieve-principles。"""

    def test_skill_discoverable(self):
        from backend.skills.loader import SkillsLoader
        loader = SkillsLoader()
        loader.discover()
        skill = loader.get_skill("wp-retrieve-principles")
        assert skill is not None
        assert skill.name == "wp-retrieve-principles"
        assert skill.type == "function_call"
        assert skill.tool_name == "retrieve_principles"

    def test_skill_in_names_list(self):
        from backend.skills.loader import SkillsLoader
        loader = SkillsLoader()
        loader.discover()
        names = loader.list_skill_names()
        assert "wp-retrieve-principles" in names

    @skip_no_key
    def test_invoke_via_loader(self):
        """通过 SkillsLoader.invoke 调用。"""
        from backend.skills.loader import SkillsLoader
        loader = SkillsLoader()
        loader.discover()
        result = loader.invoke(
            "wp-retrieve-principles",
            query="资产配置原则",
            top_k=3,
        )
        assert isinstance(result, dict)
        assert "chunks" in result


class TestBundleConfiguration:
    """测试 Bundle 配置包含 wp-retrieve-principles。"""

    def test_position_single_bundle(self):
        from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
        assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["position_single"]

    def test_position_multi_bundle(self):
        from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
        assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["position_multi"]

    def test_portfolio_bundle(self):
        from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
        assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["portfolio"]

    def test_general_included(self):
        """general 路由已包含 wp-retrieve-principles（M5b）。"""
        from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
        assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["general"]

    def test_clarify_not_included(self):
        from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
        assert "wp-retrieve-principles" not in _SKILL_BUNDLES_BY_ROUTE.get("clarify", [])
