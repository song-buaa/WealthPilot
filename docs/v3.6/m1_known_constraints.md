# v3.6 M1 已知约束与观察记录

## 1. 英文 query 区分度预警

M1 召回测试中，英文 query "asset allocation principles" 的 top-1 (0.56) vs top-2 (0.55) 差距仅 0.01，区分度低。

**原因分析**：
- seed 数据少（只有 3 个文件）
- 3 个文件都是配置原则，主题高度相关
- text-embedding-3-small 对英文也支持，但中文 query 区分度更明显

**M2 上线后的观察标准**：
- 如果英文 query 的 top-1 vs top-2 差距持续 < 0.05 成为常态（超过 50% 测试 query）
- 启动 embedding 模型切换评估（候选：text-embedding-3-large）

不是阻塞项，先观察。

## 2. 根目录 MD 文件不索引

**设计扩展**：`knowledge_base/` 根目录下的 .md 文件（`README.md` / `_template.md`）不被索引，只索引子目录中的文件。

**理由**：这些是元文档，不是知识内容。

**实现位置**：`backend/knowledge/indexer.py` 的 `_discover_md_files()` 方法，通过 `len(parts) < 2` 条件跳过根目录文件。
