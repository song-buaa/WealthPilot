# v3.6 第四批 M5a 完成报告

## 完成清单

### 任务 1：新建 wp-retrieve-principles Skill

- [x] `skills/wp-retrieve-principles/SKILL.md` — 完整 frontmatter + 能力说明 + 使用/不使用场景
- [x] `backend/graph/tools.py` 中注册 `execute_retrieve_principles` 函数 + `RETRIEVE_PRINCIPLES_SCHEMA`
- [x] SkillsLoader 能发现并注册此 Skill（`loader.get_skill("wp-retrieve-principles")` 正常）

### 任务 2：Bundle 配置更新

- [x] `position_single` Bundle 包含 `wp-retrieve-principles`（在 `wp-fetch-research` 之后）
- [x] `position_multi` Bundle 包含 `wp-retrieve-principles`
- [x] `portfolio` Bundle 包含 `wp-retrieve-principles`
- [x] `general` / `clarify` / `low_confidence` 未包含（M5b 才加 general）

### 任务 3：wp-load-context 第 7b 步

- [x] `data_loader.py` 新增 `LoadedData.retrieved_principles` 字段
- [x] `load()` 第 7b 步追加原则 RAG 检索（投资纪律 + 投资风格 + 资产配置原则）
- [x] 失败 graceful degrade（try/except + warning 日志）

## 测试结果

```
44 passed in 8.83s

新增 Skill 测试（13 个）：
- TestRetrievePrinciplesTool: 5 个（空 query / None query / 正常 query / 过滤 / 默认类型）
- TestSkillRegistration: 3 个（可发现 / 在名单中 / invoke 调用）
- TestBundleConfiguration: 5 个（三路由包含 / general 不包含 / clarify 不包含）

M1 回归（31 个）：全部通过
```

## Skill 注册验证

```python
from backend.skills.loader import SkillsLoader
loader = SkillsLoader()
loader.discover()
skill = loader.get_skill("wp-retrieve-principles")
# name=wp-retrieve-principles, type=function_call, tool_name=retrieve_principles
```

## Bundle 配置验证

```python
from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE
assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["position_single"]  # ✅
assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["position_multi"]   # ✅
assert "wp-retrieve-principles" in _SKILL_BUNDLES_BY_ROUTE["portfolio"]        # ✅
assert "wp-retrieve-principles" not in _SKILL_BUNDLES_BY_ROUTE["general"]      # ✅ M5b 才加
```

## 设计说明

### Skill 目录命名

指令中写的是 `wp_retrieve_principles`（下划线），但现有 Skills 全部使用 hyphen（`wp-fetch-research`、`wp-check-discipline` 等）。按现有命名规范使用 `wp-retrieve-principles`。

### Skill 实现位置

现有 Skill 目录里只有 SKILL.md（无 skill.py），实际逻辑全在 `backend/graph/tools.py` 的 `TOOL_EXECUTORS` 中注册。`wp-retrieve-principles` 遵循同一模式：SKILL.md 在 `skills/` 下，执行函数在 `tools.py` 中。

### LoadedData.retrieved_principles 字段

M2 添加了 `retrieved_research_views` 但未添加 `retrieved_principles`——本批次补上。字段类型为 `list`（与 `retrieved_research_views` 一致），默认空列表。

## 下一批（M5b）开始前需确认的问题

1. **`contracts.py` 改动范围**：`ExecutionOutput.loaded_data` 当前是 `Optional[object]`。M5b 的 `_execute_general` 需要构造一个 `LoadedData(rules=None, ...)`——这需要先把 `LoadedData.rules` 改为 `Optional[InvestmentRules]`。这个改动在 `data_loader.py` 而非 `contracts.py`，确认可以做？

2. **`_express_general_chat` 的调用链**：需要从 `run_streaming` 透传 `execution_output` 到 `_express_general_chat`。请确认 `run_streaming` 的签名中是否已有 `execution_output` 参数。

3. **引用来源输出**：M5b 是否需要在 ExpressingAgent 的 prompt 中加入 `retrieved_principles` 和 `retrieved_research_views` 的内容？还是留到后续 batch？
