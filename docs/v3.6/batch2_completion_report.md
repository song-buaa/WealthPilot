# v3.6 第二批 M2 完成报告

## 完成清单

### 任务一：投研观点 MD 落盘

- [x] `research_service.py:parse_text()` 末尾追加 `_persist_to_knowledge_base()` 调用
- [x] `_persist_to_knowledge_base()` 实现：原文 + YAML frontmatter → MD 文件 → 触发索引
- [x] `backend/knowledge/slug.py` — slug 生成工具（拼音转换 + mapping 自动积累）
- [x] `asset_slug_mapping.json` 预填 10 个常见标的
- [x] `ai_advisor.py:generate_research_card_full()` 的 LLM prompt 追加 `time_sensitivity` 字段
- [x] `requirements.txt` 追加 `pypinyin>=0.53.0`
- [x] `data_loader.py` 第 7a 步追加投研 RAG 检索，填充 `retrieved_research_views`
- [x] `LoadedData` 新增 `retrieved_research_views` 字段

### 任务二：投资纪律手册迁移

- [x] `data/handbook_official.md` → `backend/knowledge_base/investment_principles/handbook_official.md`（复制）
- [x] `discipline_service.py` 路径更新（3 处）
- [x] `data/handbook_official.md` 原文件保留（未删除）
- [x] Rule Engine 回归测试全部通过

## Rule Engine 回归测试输出

```
[PASS] get_handbook() source=official, content_length=5371
[PASS] get_rules() max_position_pct=0.4
[PASS] parse_rules_config() parsed 9 top-level keys
✅ Rule Engine 回归测试全部通过
```

## 投资纪律索引验证

手册迁移到 `knowledge_base/` 后，KnowledgeIndexer 自动扫描并索引：

```
[INFO] Indexed: added=['backend/knowledge_base/investment_principles/handbook_official.md']
       chunks=14（HTML JSON RULES_CONFIG 块已被切片器跳过）
[PASS] 投资纪律 RAG 检索: query="单一标的不超过总仓位" → top-1 score=0.56, file=handbook_official.md
```

## M1 单元测试回归

```
31 passed in 7.94s（无回归）
```

## 18-case 评测说明

18-case 评测脚本需要后端服务器运行（HTTP 请求 localhost:8000），非离线可执行。M2 的改动均为追加式：
- `research_service.py`：在 `parse_text()` 返回前追加一个 `_persist_to_knowledge_base()` 调用，`try/except` 包住，失败不影响主流程
- `data_loader.py`：在第 7 步后追加第 7a 步 RAG 检索，`if is_ready()` 判断，知识库未就绪时静默跳过
- `discipline_service.py`：仅修改路径常量，函数逻辑不变

## 设计调整点

### 1. [设计扩展] slug 命名优化

`xiaopengqiche` 这样的纯拼音 slug 可读性较差。后续可以优化为"让 LLM 在解析投研观点时同时生成英文 title"的方案（PRD 已提到）。当前拼音兜底可用，不阻塞。

### 2. [设计确认] handbook_custom.md 不存在

当前环境 `data/handbook_custom.md` 不存在（用户未上传自定义手册）。迁移时只复制了 `handbook_official.md`。当用户首次上传自定义手册时，`discipline_service.save_handbook()` 会自动在新路径 `backend/knowledge_base/investment_principles/handbook_custom.md` 创建文件。

### 3. [观察] 投资纪律手册切片数

handbook_official.md（v1.4，11 条规则）被切成 14 个 chunks。HTML 注释内的 RULES_CONFIG JSON 块被 M1 的 chunker 正确跳过（不进 RAG）。切片粒度合理——后续 M3 batch 如果实现按规则编号切片，数量会更精准。

## 下一批前置依赖

第三批（M3 投资风格 + M4 配置原则扩充 + M5 PEER 集成）开始前需确认：

1. **端到端 Case 1 人工评测**需要启动后端服务器，建议在第三批开始前做一次真实环境验证
2. **投资风格模块**是完全新增（无现有代码），需要 UI 设计 + 后端 API + DB 表设计
3. **M5 PEER 集成**涉及 `executing_agent.py`、`expressing_agent.py`、`contracts.py` 的改动，风险较高，建议单独一个 batch
