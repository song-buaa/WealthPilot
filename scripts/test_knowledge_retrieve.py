#!/usr/bin/env python3
"""
WealthPilot v3.6 Knowledge Layer 验证脚本。

验证"手动放 MD 文件 → 索引 → 检索"链路可用。

用法：
    python scripts/test_knowledge_retrieve.py

前置条件：
    - WEALTHPILOT_OPENAI_API_KEY 环境变量已设置
    - backend/knowledge_base/allocation_principles/ 下有 seed MD 文件
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def main():
    print("=" * 60)
    print("WealthPilot v3.6 Knowledge Layer 验证")
    print("=" * 60)

    # 检查 API Key
    if not os.getenv("WEALTHPILOT_OPENAI_API_KEY"):
        print("\n[ERROR] WEALTHPILOT_OPENAI_API_KEY 未设置。请在 .env 中配置。")
        sys.exit(1)

    # 导入知识层模块
    from backend.knowledge.store import KnowledgeStore
    from backend.knowledge.indexer import KnowledgeIndexer
    from backend.knowledge.chunker import KnowledgeChunker
    from backend.knowledge.status_tracker import StatusTracker

    # 初始化
    kb_root = ROOT / "knowledge_base"
    index_dir = kb_root / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[INFO] knowledge_base 路径: {kb_root}")

    # 重置单例（确保干净状态）
    KnowledgeStore.reset_instance()
    store = KnowledgeStore.get_instance()

    if not store.is_ready():
        print("[ERROR] KnowledgeStore 初始化失败。请检查 OpenAI API Key 和网络。")
        sys.exit(1)

    print(f"[INFO] KnowledgeStore 初始化成功，当前 chunk 数: {store.count()}")

    # 创建索引器
    tracker = StatusTracker(index_dir / "file_index.json")
    chunker = KnowledgeChunker()
    indexer = KnowledgeIndexer(
        knowledge_root=kb_root,
        store=store,
        chunker=chunker,
        status_tracker=tracker,
    )

    # 全量扫描索引
    print("\n[INFO] 开始全量扫描索引...")
    report = indexer.full_scan_and_sync()
    total_files = len(report.added_files) + len(report.updated_files)
    print(
        f"[INFO] 索引完成: {total_files} 个文件 → {report.total_chunks} 个 chunks "
        f"({report.duration_ms}ms)"
    )
    if report.added_files:
        print(f"  新增: {report.added_files}")
    if report.updated_files:
        print(f"  更新: {report.updated_files}")
    if report.failed_files:
        print(f"  失败: {report.failed_files}")

    # 索引状态
    status = tracker.summary
    print(f"[INFO] 索引状态: {status}")

    # 检索测试
    test_queries = [
        ("什么是动态再平衡", "中文配置原则查询"),
        ("五大类资产怎么配置", "中文资产配置查询"),
        ("asset allocation principles", "英文查询（中英混合鲁棒性）"),
    ]

    all_passed = True
    for i, (query, desc) in enumerate(test_queries, 1):
        print(f"\n测试 {i}: {desc}")
        print(f"  Query: \"{query}\"")
        results = store.retrieve(query, top_k=3)
        if not results:
            print(f"  [WARN] 召回为空!")
            all_passed = False
        else:
            print(f"  召回 chunks (top {len(results)}):")
            for r in results:
                print(
                    f"    - source_type={r.source_type}, "
                    f"score={r.semantic_score:.2f}, "
                    f"file={Path(r.parent_doc_path).name}"
                )

    # 故障场景验证
    print("\n" + "-" * 40)
    print("故障场景验证:")

    # 1. is_ready() 在正常状态返回 True
    assert store.is_ready(), "is_ready() 应返回 True"
    print("  [PASS] is_ready() 返回 True")

    # 2. failed / stale 列表接口可用
    failed = tracker.list_failed()
    stale = tracker.list_stale()
    print(f"  [PASS] list_failed()={len(failed)}, list_stale()={len(stale)}")

    # 3. 空 query 不崩溃
    empty_results = store.retrieve("", top_k=3)
    print(f"  [PASS] 空 query 返回 {len(empty_results)} 条（不崩溃）")

    # 总结
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有测试通过")
    else:
        print("⚠️ 部分测试有警告，请检查上方输出")
    print("=" * 60)

    KnowledgeStore.reset_instance()


if __name__ == "__main__":
    main()
