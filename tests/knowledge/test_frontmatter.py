"""FrontmatterParser 单元测试。"""
import pytest
from backend.knowledge.frontmatter import parse_from_text, infer_source_type
from pathlib import Path


class TestParseFromText:
    def test_yaml_frontmatter(self):
        text = """---
source: 系统预置
date: 2026-05-13
time_sensitivity: permanent
tags: [资产配置, 方法论]
---

# 标题

正文内容。
"""
        fm, body = parse_from_text(text)
        assert fm["source"] == "系统预置"
        assert fm["time_sensitivity"] == "permanent"
        assert "资产配置" in fm["tags"]
        assert body.startswith("# 标题")
        assert "正文内容" in body

    def test_html_json_block(self):
        text = """<!-- RULES_CONFIG
{
  "single_asset_limits": {"max_position_pct": 0.40}
}
-->

**核心原则：**

规则正文内容。
"""
        fm, body = parse_from_text(text)
        assert fm == {}  # HTML JSON 不解析为 frontmatter
        assert "RULES_CONFIG" not in body  # HTML 块被移除
        assert "核心原则" in body
        assert "规则正文内容" in body

    def test_no_frontmatter(self):
        text = "# 普通 Markdown\n\n没有 frontmatter。"
        fm, body = parse_from_text(text)
        assert fm == {}
        assert "普通 Markdown" in body

    def test_empty_file(self):
        fm, body = parse_from_text("")
        assert fm == {}
        assert body == ""

    def test_yaml_with_chinese_values(self):
        text = """---
source: 蚂小财
asset: 理想汽车
market: US
---

对话内容。
"""
        fm, body = parse_from_text(text)
        assert fm["source"] == "蚂小财"
        assert fm["asset"] == "理想汽车"
        assert fm["market"] == "US"


class TestInferSourceType:
    def test_from_frontmatter(self):
        assert infer_source_type(
            Path("any/path.md"),
            {"source_type": "investment_style"},
        ) == "investment_style"

    def test_from_path_allocation(self):
        assert infer_source_type(
            Path("knowledge_base/allocation_principles/rebalance.md"),
            {},
        ) == "allocation_principles"

    def test_from_path_principles(self):
        assert infer_source_type(
            Path("knowledge_base/investment_principles/rules.md"),
            {},
        ) == "investment_principles"

    def test_from_path_research(self):
        assert infer_source_type(
            Path("knowledge_base/research_views/li_auto/2026.md"),
            {},
        ) == "research_views"

    def test_default_fallback(self):
        assert infer_source_type(
            Path("some/unknown/path.md"),
            {},
        ) == "allocation_principles"
