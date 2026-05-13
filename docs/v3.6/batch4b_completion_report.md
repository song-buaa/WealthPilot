# v3.6 第四批 M5b 完成报告

## 完成清单

### 任务 0：勘探

- [x] `_express_general_chat()` 签名：`(self, out, user_query, conversation_history=None)`，无 `execution_output`
- [x] `run_streaming()` 已有 `execution_output` 参数（第 320 行），但调用 `_express_general_chat` 时未传入
- [x] `_execute_passthrough()` 对 general/clarify/low_confidence 统一 SKIP

### 任务 1：LoadedData.rules Optional 化

- [x] `data_loader.py:104`：`rules: Optional[InvestmentRules] = None`
- [x] `research` 和 `total_assets` 也加了默认值（解决 dataclass 字段顺序约束）
- [x] Rule Engine 回归通过

### 任务 2：_execute_general + 路由拆分

- [x] `executing_agent.py`：general 路由从 `_execute_passthrough` 拆出，改调 `_execute_general()`
- [x] `_execute_general()` 实现：子意图判断 + 轻量 RAG + 构造 LoadedData(rules=None)
- [x] `_should_retrieve_principles()` 关键词判断验证通过
- [x] clarify / low_confidence 仍走 `_execute_passthrough`，不受影响

### 任务 3：expressing_agent 透传 + 引用输出

- [x] `_express_general_chat()` 新增 `execution_output` 参数（默认 None，向后兼容）
- [x] `run_streaming()` 第 355 行：调用处加 `execution_output=execution_output`
- [x] 知识库内容通过 `principles_override` 传给 `llm_engine.chat()`
- [x] 引用来源区块：`_build_citation_block()` 生成 `📚 参考来源` 文本
- [x] 引用来源同时加入 position_decision 和 portfolio 路径

### 任务 4+5：Bundle + style.md + 验收

- [x] `general` Bundle 加入 `wp-retrieve-principles`
- [x] `style.md` 填写测试内容（价值主张 + 红线 + 偏好赛道 + 持仓哲学）

## 勘探结果

`run_streaming` 签名（第 317-323 行）：
```python
async def run_streaming(
    self,
    planning_output: PlanningOutput,
    execution_output: ExecutionOutput,  # ← 已有
    user_query: str,
    conversation_history: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
```

透传改动：仅在第 355 行的 `_express_general_chat` 调用处加 `execution_output=execution_output`（1 行改动）。

## 端到端 Case 验收

### Case 3：动态再平衡教育问答

```
Query: "什么是动态再平衡"
_should_retrieve_principles → True（命中"再平衡"关键词）
检索结果: top-1 score=0.61, file=dynamic_rebalancing.md
引用区块: [资产配置] allocation_principles/dynamic_rebalancing.md
```

✅ 通过：正确走 general 路由，召回知识库内容，生成引用来源

### Case 4：期权红线约束

```
Query: "我要不要买期权搏一把"
检索结果: top-1 score=0.40, type=investment_principles, file=handbook_official.md
命中内容: "规则1 — 杠杆工具分级管理 🔴 HARD"（含期权禁止条款）
```

✅ 通过：召回纪律手册中的杠杆/期权 HARD 规则

### Case 5：知识库故障降级

```
操作: enabled=false
KnowledgeStore.is_ready() = False
_execute_general: retrieved_principles=[]
_build_citation_block: 返回空字符串（无引用区块）
```

✅ 通过：系统不崩，决策流程正常，无引用来源区块

### Case 1+2 说明

Case 1（理想汽车加仓）和 Case 2（美团买入）需要启动完整后端服务器 + 真实持仓数据。本次验证了数据链路的关键组件：
- `data_loader.load()` 第 7a 步（投研 RAG）和第 7b 步（原则 RAG）均已注入
- `_build_citation_block()` 正确生成引用来源
- position_decision 和 portfolio 路径已追加引用来源

完整 Case 1+2 的端到端验收需要在启动后端服务器后人工执行。

## 测试结果

```
44 passed in 9.42s（31 M1 + 13 Skill 测试，含 Bundle 配置更新后的断言）
```

## 设计说明

### dataclass 字段顺序调整

`LoadedData.rules` 改为 `Optional[InvestmentRules] = None` 后，`research: list[str]` 和 `total_assets: float` 因为没有默认值而报错（Python dataclass 要求非默认字段不能跟在有默认值的字段后面）。解决方案：给 `research` 加 `field(default_factory=list)`，给 `total_assets` 加默认值 `0.0`。现有调用方（`load()` 函数）始终传入这两个参数，所以行为不变。

### 引用来源区块位置

引用来源追加在 `chat_answer` 文本末尾（position_decision / portfolio / general_chat 三条路径），在 `_emit_text_chunks` 流式输出之前。前端渲染时会自然显示在答复末尾。

## 下一批（M6 文档 & Release）前置依赖

1. **Case 1+2 完整端到端验收**需启动后端，建议 Songbin 手动执行
2. **CHANGELOG 更新**
3. **AGENTS.md 更新**（v3.6 架构变更、新增 Skill、Bundle 配置）
4. **README 更新**（可选，v3.6 新增能力简述）
