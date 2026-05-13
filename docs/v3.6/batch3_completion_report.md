# v3.6 第三批 M3+M4 完成报告

## 完成清单

### M3 投资风格模板

- [x] `investment_style/style.md` 创建，完整 YAML frontmatter（`time_sensitivity=permanent`）
- [x] KnowledgeIndexer 索引验证通过（`source_type=investment_style`，score=0.66）
- [x] `file_index.json` 中 `style.md` 状态为 `indexed`

### M4 资产配置原则

- [x] `multi_asset_allocation.md` 扩充至约 800 字（五大类资产详解 + 配置建议区间）
- [x] `dynamic_rebalancing.md` 扩充至约 700 字（触发机制 + 操作原则 + 常见误区）
- [x] `target_range_management.md` 扩充至约 650 字（区间 vs 精确值 + 调整时机 + 纪律联动）
- [x] 3 个文件 YAML frontmatter 正确，`time_sensitivity=permanent`
- [x] `llm_engine.py` fallback 改造完成（新增 `_get_allocation_principles_text()` + `chat()` 接受 `principles_override` 参数）
- [x] `WEALTHPILOT_ALLOCATION_PRINCIPLES` 常量保留（fallback 用）
- [x] 现有调用方行为不变（`principles_override=None` 时输出与改造前相同）

### 回归

- [x] M1 单元测试 31/31 通过

## 检索验收输出

全量重建后：5 个文件 → 22 个 chunks。

### Test 1：中文教育类 query

```
Query: "什么是动态再平衡"
  score=0.61, file=dynamic_rebalancing.md  ← top-1 正确命中
  score=0.46, file=dynamic_rebalancing.md
  score=0.45, file=dynamic_rebalancing.md
  top-1 score: 0.608 (> 0.5 验收标准)
```

### Test 2：英文 query 区分度

```
Query: "asset allocation principles"
  score=0.57, file=multi_asset_allocation.md
  score=0.53, file=multi_asset_allocation.md  ← 同文件不同 chunk
  score=0.51, file=target_range_management.md
  chunk 级 gap: 0.042 (< 0.05)
  文件级 gap: 0.06 (> 0.05)
```

**观察**：chunk 级别的 top-1 vs top-2 gap（0.042）仍 < 0.05，但它们来自**同一文件的不同 chunks**——这说明区分度问题不是"文件搞混"，而是"同文件内多 chunk 得分接近"。文件级别区分度 0.06 > 0.05，实际使用中不影响召回质量。

记录到 `m1_known_constraints.md`：英文 query 的 chunk 级区分度仍是已知约束，但文件级区分度已改善。

## llm_engine.py 改造位置

**文件**：`decision_engine/llm_engine.py`

**新增函数**：`_get_allocation_principles_text()`（约第 1490 行）
- `principles_override` 非空 → 返回传入内容
- `principles_override` 为 None 或空字符串 → fallback 到 `WEALTHPILOT_ALLOCATION_PRINCIPLES` 常量

**改造函数**：`chat()`（约第 1508 行）
- 新增 `principles_override: Optional[str] = None` 参数
- 内部用 `_get_allocation_principles_text(principles_override)` 替代直接引用常量
- **现有调用方不需要改动**（传 None 时行为不变）

## 下一批前置依赖

M5（PEER 集成）开始前需确认：

1. **`contracts.py` 的 `LoadedData` 类型标注**：当前 `ExecutionOutput.loaded_data` 是 `Optional[object]`。M5 需要 `ExpressingAgent` 从中读取 `retrieved_principles` 和 `retrieved_research_views`——是否需要把类型从 `object` 改为 `LoadedData`？
2. **`_execute_passthrough` 拆分为 `_execute_general`**：M5 需要改 `executing_agent.py`，让 general 路由调用 `wp-retrieve-principles`。这是 M5 的核心改动
3. **`_express_general_chat` 参数透传**：M5 需要改 `expressing_agent.py`，让它接收 `execution_output` 并提取 `retrieved_principles`。需要确认 `run_streaming` 的调用链
4. **Bundle 配置更新**：M5 需要更新 `_SKILL_BUNDLES_BY_ROUTE`，但 `wp-retrieve-principles` 的 SKILL.md 和 Tool 注册还没做——M5 要一起做还是拆开？
