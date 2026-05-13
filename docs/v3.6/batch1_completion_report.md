# v3.6 第一批 M1 完成报告

## 完成清单

### 必做交付物

- [x] 目录结构：`backend/knowledge_base/` 及所有子目录
- [x] `backend/knowledge_base/README.md`（使用说明）
- [x] `backend/knowledge_base/_template.md`（YAML frontmatter 模板）
- [x] `_index/` 加入 `.gitignore`
- [x] `backend/knowledge/store.py` — KnowledgeStore（Chroma + OpenAI Embedding，单例）
- [x] `backend/knowledge/indexer.py` — KnowledgeIndexer（增量同步）
- [x] `backend/knowledge/chunker.py` — KnowledgeChunker（MVP 最小可用版，RecursiveCharacterTextSplitter 兜底）
- [x] `backend/knowledge/frontmatter.py` — 双格式解析（YAML + HTML JSON 跳过）
- [x] `backend/knowledge/schemas.py` — RetrievedChunk / ChunkInput / FileStatus / SyncReport
- [x] `backend/knowledge/status_tracker.py` — index_status 状态管理
- [x] `backend/knowledge/decay.py` — 空壳函数（MVP 不启用，代码就位）
- [x] `backend/knowledge/asset_slug_mapping.json` — 初始为空 `{}`
- [x] `backend/config/knowledge.yaml`（配置文件）
- [x] `requirements.txt` 增加 chromadb / python-frontmatter / langchain-text-splitters
- [x] `scripts/test_knowledge_retrieve.py`（验证脚本）
- [x] `tests/knowledge/test_store.py`（6 个测试）
- [x] `tests/knowledge/test_indexer.py`（8 个测试）
- [x] `tests/knowledge/test_chunker.py`（6 个测试）
- [x] `tests/knowledge/test_frontmatter.py`（10 个测试）
- [x] 3 个 seed MD 文件（多元资产配置 / 动态再平衡 / 目标区间管理）

### 验收标准

- [x] 31 个单元测试全部通过（`pytest tests/knowledge/`）
- [x] 手动检索验证通过（3 个 query 全部正确召回）
- [x] `is_ready()` 在 `enabled=false` 时返回 False
- [x] 删除 `_index/` 后能全量重建
- [x] 修改 MD 文件后 hash 变化被检测并重新索引
- [x] `file_index.json` 正确记录每个文件的 status / content_hash / last_indexed_at
- [x] `list_failed()` / `list_stale()` 接口可用
- [x] `knowledge.yaml` 配置参数生效

## 手动验证输出

```
[INFO] 扫描 knowledge_base/, 发现 3 个 MD 文件
[INFO] 索引完成: 3 个文件 → 3 个 chunks (1493ms)
[INFO] 索引状态: indexed=3, pending=0, failed=0

测试 1: "什么是动态再平衡"
  - allocation_principles, score=0.61, file=dynamic_rebalancing.md  ✅
  - allocation_principles, score=0.37, file=target_range_management.md
  - allocation_principles, score=0.29, file=multi_asset_allocation.md

测试 2: "五大类资产怎么配置"
  - allocation_principles, score=0.73, file=multi_asset_allocation.md  ✅
  - allocation_principles, score=0.52, file=target_range_management.md
  - allocation_principles, score=0.49, file=dynamic_rebalancing.md

测试 3: "asset allocation principles"（英文）
  - allocation_principles, score=0.56, file=multi_asset_allocation.md  ✅
  - allocation_principles, score=0.55, file=target_range_management.md
  - allocation_principles, score=0.50, file=dynamic_rebalancing.md
```

## 设计调整点

### 1. [设计扩展] 根目录 MD 文件跳过

架构文档未明确说明 `knowledge_base/README.md` 是否应被索引。实现中增加了"根目录下的 MD 文件不索引，只索引子目录中的文件"规则，因为 README.md 和 _template.md 是元文档，不是知识内容。

### 2. [设计扩展] 空 query 防御

OpenAI Embedding API 不接受空字符串输入。在 `KnowledgeStore.retrieve()` 开头增加了空 query 检查，直接返回空列表。

### 3. [观察] Seed 数据 chunk 粒度

3 个 seed 文件（每个 200-300 字）在 chunk_size=600 下各自只产生 1 个 chunk。这是预期行为——文件内容短于 chunk_size 时整体作为一个 chunk。后续 M4 阶段扩充内容到 500-1000 字时，自然会产生多个 chunks。

### 4. [观察] 召回 score 范围

中文 query 的 top-1 score 在 0.61-0.73 之间，英文 query 在 0.50-0.56 之间。这个范围合理——text-embedding-3-small 在中英混合场景下的表现符合预期。后续如果需要提升英文查询准确度，可考虑切换到 text-embedding-3-large。

## 下一批前置依赖

第二批（M2 投研观点知识化 + M3 投资纪律知识化）开始前需确认：

1. **M1 代码是否需要先提交/合并？** 建议先 commit M1，再开始 M2
2. **投资纪律手册的知识化路径**：直接索引 `data/handbook_*.md` 还是拷贝到 `knowledge_base/investment_principles/`？（上一份勘探报告的遗留问题 #1）
3. **投研观点 MD 落盘的文件命名规则**：`{date}_{title_slug}.md` 中 title_slug 的中文处理方式（拼音？保留中文？）
