"""
KnowledgeStore 单元测试。

注意：需要 WEALTHPILOT_OPENAI_API_KEY 环境变量。
如果未设置，依赖真实 Embedding 的测试会被跳过。
"""
import os
import shutil
import tempfile

import pytest

from backend.knowledge.schemas import ChunkInput

# 检查是否有 API key 可用
HAS_API_KEY = bool(os.getenv("WEALTHPILOT_OPENAI_API_KEY"))
skip_no_key = pytest.mark.skipif(
    not HAS_API_KEY,
    reason="WEALTHPILOT_OPENAI_API_KEY not set",
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestKnowledgeStoreInit:
    def test_is_ready_returns_bool(self):
        """is_ready() 始终返回 bool，不抛异常。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        store = KnowledgeStore.get_instance()
        result = store.is_ready()
        assert isinstance(result, bool)
        KnowledgeStore.reset_instance()

    def test_singleton_pattern(self):
        """get_instance() 返回同一实例。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        a = KnowledgeStore.get_instance()
        b = KnowledgeStore.get_instance()
        assert a is b
        KnowledgeStore.reset_instance()

    def test_retrieve_when_not_ready(self):
        """is_ready()=False 时 retrieve() 返回空列表，不抛异常。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        store = KnowledgeStore.get_instance()
        if not store.is_ready():
            result = store.retrieve("测试查询")
            assert result == []
        KnowledgeStore.reset_instance()


@skip_no_key
class TestKnowledgeStoreCRUD:
    def test_add_and_retrieve(self):
        """添加 chunks 后能检索到。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        store = KnowledgeStore.get_instance()
        if not store.is_ready():
            pytest.skip("Store not ready")

        chunks = [
            ChunkInput(
                content="动态再平衡是指当配置出现偏离时，优先通过新增资金自然修正。",
                source_type="allocation_principles",
                parent_doc_path="test/rebalance.md",
                chunk_index=0,
                metadata={"date": "2026-05-13", "time_sensitivity": "permanent"},
            ),
        ]
        added = store.add_chunks(chunks)
        assert added == 1

        results = store.retrieve("什么是动态再平衡", top_k=3)
        assert len(results) >= 1
        assert results[0].source_type == "allocation_principles"
        assert "再平衡" in results[0].content

        # 清理
        store.delete_by_parent_doc("test/rebalance.md")
        KnowledgeStore.reset_instance()

    def test_delete_by_parent_doc(self):
        """删除指定文件的所有 chunks。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        store = KnowledgeStore.get_instance()
        if not store.is_ready():
            pytest.skip("Store not ready")

        chunks = [
            ChunkInput(
                content="测试内容用于删除验证。",
                source_type="investment_style",
                parent_doc_path="test/to_delete.md",
                chunk_index=0,
            ),
        ]
        store.add_chunks(chunks)
        deleted = store.delete_by_parent_doc("test/to_delete.md")
        assert deleted >= 1
        KnowledgeStore.reset_instance()

    def test_source_type_filter(self):
        """source_types 过滤只返回匹配类型。"""
        from backend.knowledge.store import KnowledgeStore
        KnowledgeStore.reset_instance()
        store = KnowledgeStore.get_instance()
        if not store.is_ready():
            pytest.skip("Store not ready")

        chunks = [
            ChunkInput(
                content="投资纪律：单标不超过40%。",
                source_type="investment_principles",
                parent_doc_path="test/principles.md",
                chunk_index=0,
            ),
            ChunkInput(
                content="多元资产配置原则。",
                source_type="allocation_principles",
                parent_doc_path="test/allocation.md",
                chunk_index=0,
            ),
        ]
        store.add_chunks(chunks)

        results = store.retrieve(
            "纪律", source_types=["investment_principles"], top_k=5
        )
        for r in results:
            assert r.source_type == "investment_principles"

        # 清理
        store.delete_by_parent_doc("test/principles.md")
        store.delete_by_parent_doc("test/allocation.md")
        KnowledgeStore.reset_instance()
