# WealthPilot — 投资决策模块修复验证交接文档

> **文档版本**：v1.0 · 2026-03-23
> **对应源码版本**：commit `022229e`（branch: `feature/manus-handoff-v2`）
> **交接对象**：测试同学 / 外部 AI 代理（Manus）
> **用途**：定向回归验证，确认本轮 7 项缺陷已全部修复

---

## 1. 本轮修复目标

本轮修复基于 **《WealthPilot 投资决策模块正式测试报告 v1.1 - 修订版》**（Manus，2026-03-23）。

测试报告结论为"部分通过（Conditional Pass）"，共发现 7 项问题（P0×1 / P1×2 / P2×2 / 产品待确认×2）。

**本轮目标**：修复全部 P0、P1、P2 问题，并将 2 条"产品待确认"策略落地为代码实现。修复完成后，整体模块应能达到"正式通过"状态。

---

## 2. 修复项总览

| Bug ID | 优先级 | 涉及文件 | 修复内容摘要 | 当前状态 |
|--------|--------|---------|-------------|---------|
| BUG-01 | **P0** | `app_pages/strategy.py` | API Key 检查从页面入口下移至"开始分析"后 | ✅ 已修复 |
| BUG-02 | **P1** | `decision_engine/data_loader.py` | `_safe_pct` 负值抛 `ValueError`，不再静默替换为默认值 | ✅ 已修复 |
| BUG-03 | **P1** | `decision_engine/data_loader.py`<br>`decision_engine/decision_flow.py` | 歧义匹配检测，多候选时流程中断并提示用户精确输入 | ✅ 已修复 |
| BUG-04 | **P2** | `decision_engine/llm_engine.py`<br>`app_pages/strategy.py` | LLM 结论被自动修正时，UI 展示"已自动修正"提示 | ✅ 已修复 |
| BUG-05 | **P2** | `decision_engine/decision_flow.py` | 空仓减仓/卖出场景，流程 `ABORTED`，明确标注"无效操作" | ✅ 已修复 |
| BUG-06 | 产品确认 | `decision_engine/data_loader.py` | 不支持单字匹配（`len < 2` 直接返回空） | ✅ 已落地 |
| BUG-07 | 产品确认 | `decision_engine/data_loader.py` | 百分比 `0` 为合法值，不再被替换为默认值 | ✅ 已落地 |

---

## 3. 每个问题的修复说明

---

### BUG-01｜API Key 缺失导致入口级阻塞（P0）

**原问题**
`strategy.py` 在 `_render_decision_engine()` 函数入口处调用 `st.stop()`，导致未配置 API Key 时整个投资决策页面（包括 Tab 2 策略设定）全部不可用。

**代码修改**
- 移除入口处的 `st.stop()`
- 改为 `st.info()` 非阻塞提示（蓝色横幅）
- 在用户点击"开始分析"按钮后，再做 API Key 二次检查（`st.error()` + `return`，不用 `st.stop()`）

**现在预期行为**
- 未配置 API Key → 页面正常加载，顶部显示蓝色提示横幅，Tab 2 策略设定完全可用
- 点击"开始分析" → 显示红色错误提示，返回停止分析，其他页面功能不受影响

**验证重点**
- 无 API Key 时，确认 Tab 2 策略设定表单可正常访问和保存
- 确认错误提示样式为 `st.error`（红色），而非页面白屏或空页面

---

### BUG-02｜百分比负值被静默替换（P1）

**原问题**
`_safe_pct()` 使用 `return v if 0 < v <= 1.0 else default` 的条件判断，导致负数值（如 `-0.1`）被静默替换为默认值 `0.25`，用户无任何感知。

**代码修改**
```python
# 修改前（静默替换）
return v if 0 < v <= 1.0 else default

# 修改后（负值报错，0 值合法）
if v < 0:
    raise ValueError(f"百分比字段包含非法负值：{value}。请检查策略配置，确保所有百分比 ≥ 0。")
if v > 1.0:
    v = v / 100.0
return v  # 0.0 是合法边界值
```
同时 `decision_flow.py` 新增 `ValueError` 专属捕获，向用户展示"⚠️ 数据异常：[具体原因]"。

**现在预期行为**
- `_safe_pct(-0.1, 0.25)` → 抛出 `ValueError`（不再返回 `0.25`）
- 流程在数据加载阶段 `ABORTED`，错误消息包含"百分比字段包含非法负值"

**验证重点**
- 将 Portfolio 表中 `max_single_stock_pct` 设为负值（如 `-10`）后触发分析，确认流程中断并提示数据异常

---

### BUG-03｜模糊匹配首项风险（P1）

**原问题**
`_find_position()` 遇到多个相似标的时（如"理想汽车"和"理想汽车-W_1"同时存在），静默选择第一个匹配项，无任何提示。

**代码修改**
- 重构 `_find_position` 为 `_find_all_positions`，引入**精确匹配优先**策略：
  - 第一轮：寻找归一化名称完全相等 或 ticker 完全相等的精确匹配
  - 若有精确匹配 → 返回精确结果（1 个或多个同名项）
  - 若无精确匹配 → 返回模糊匹配结果（可能多个）
- `LoadedData` 新增 `ambiguous_matches: list[PositionInfo]` 字段
- `decision_flow.py` 在数据加载后检测：若 `ambiguous_matches` 非空 → `FlowStage.ABORTED`，展示候选名称列表

**现在预期行为**
- 输入"理想汽车"且 DB 中存在精确名称"理想汽车" → 唯一匹配，流程正常
- 输入"理想"且 DB 中存在多个含"理想"的标的 → 流程中断，警告展示所有候选标的名称，提示用户精确输入

**验证重点**
- 模糊关键词（如"理想"）触发歧义时，确认错误提示为 `st.warning`（黄色），并列出候选标的名称

---

### BUG-04｜LLM 结论自动修正缺乏提示（P2）

**原问题**
LLM 返回非标准值（如"观望"、"继续持有"）时，`_build_result` 静默将其转为 `HOLD`，用户无法感知结论已被修正。

**代码修改**
- `LLMResult` 新增两个字段：`decision_corrected: bool` 和 `original_decision: Optional[str]`
- `_build_result` 检测到非标准 decision 时，设置上述字段为 `True` / 原始值
- UI `_render_final_decision` 中：`decision_corrected=True` 时展示 `st.caption` 提示

**现在预期行为**
- LLM 正常返回 `BUY/HOLD/SELL` → 无额外提示
- LLM 返回非标准值 → 最终结论旁显示灰色小字："AI 原始输出「xxx」不在标准选项内，已自动修正为「观望」"

**验证重点**
- 此场景在正常使用中较难触发（LLM 通常遵守 prompt），可通过 mock 或查看 `llm_result.decision_corrected` 字段验证逻辑正确

---

### BUG-05｜空仓减仓场景 FlowStage 与提示文案歧义（P2）

**原问题**
用户对未持有的标的执行"减仓判断/卖出判断"时，`rule_engine` 设置 `violation=True`，但流程仍继续运行至 LLM 并返回 `FlowStage.DONE`，最终展示 `HOLD` 建议，用户无法判断这是无效操作。

**代码修改**
在 `decision_flow.py` 规则校验后新增中断检测：
```python
if rule_result.violation and rule_result.current_weight == 0.0 \
        and intent.action_type in ("减仓判断", "卖出判断"):
    result.stage = FlowStage.ABORTED
    result.aborted_reason = "⛔ 无效操作：当前未持有...无法执行..."
    return result
```

**现在预期行为**
- 未持有标的 + 减仓/卖出意图 → `FlowStage.ABORTED`，UI 展示红色"⛔ 无效操作"错误
- 未持有标的 + 加仓/买入意图 → 流程正常，提示"新建仓操作"（不受影响）

**验证重点**
- 确认 FlowStage 为 `ABORTED`（不再是 `DONE`）
- 确认错误提示为 `st.error`（红色），包含"无效操作"文案
- 确认"加仓贵州茅台"（未持有）仍能正常跑完流程（不受此规则影响）

---

## 4. 产品策略确认项

以下两条已由产品确认，当前代码已按此实现：

### 策略 A：不支持单字匹配

> **结论**：输入单字（如"理"）不匹配任何标的，系统不报错，以"未找到该标的"处理。

**代码实现**（`data_loader.py: _find_all_positions`）：
```python
name_lower = asset_name.lower().replace(" ", "")
if len(name_lower) < 2:
    return []  # 单字直接返回空，不进行任何匹配
```

**测试验证**：输入"理"作为标的名称，`target_position` 为 `None`，`ambiguous_matches` 为空，流程中不应出现误匹配。

---

### 策略 B：百分比 0 值为合法边界值，负值为非法输入

> **结论**：`0` 是合法值（如"0% 仓位上限"），应进入规则校验。负值（如 `-10`）是非法输入，应明确报错。

**代码实现**（`data_loader.py: _safe_pct`）：
```python
if v < 0:
    raise ValueError(...)  # 负值 → 报错
if v > 1.0:
    v = v / 100.0
return v  # 0.0 直接返回，不替换为默认值
```

**测试验证**：`_safe_pct(0, 0.25)` 返回 `0.0`；`_safe_pct(-0.1, 0.25)` 抛出 `ValueError`。

---

## 5. 最小回归测试建议

| 编号 | 场景 | 操作方式 | 预期结果 |
|------|------|---------|---------|
| **R1** | 无 API Key 进入页面 | 不设置 `ANTHROPIC_API_KEY`，启动后进入"投资决策" → "决策引擎" Tab | 页面正常加载，顶部显示蓝色 `st.info` 提示横幅；Tab 2 策略设定可正常打开 |
| **R2** | 无 API Key 点击"开始分析" | R1 基础上输入任意内容，点击"开始分析" | 显示红色 `st.error` 错误提示"未配置 ANTHROPIC_API_KEY"；页面不崩溃，不白屏 |
| **R3** | 输入"理想汽车" | 设置有效 API Key，输入"理想汽车下周有发布会，适合加仓吗？" | 流程正常跑完，意图解析识别标的为理想汽车；持仓区块展示匹配持仓；无歧义警告 |
| **R4** | 输入"理想"验证歧义 | 输入"理想现在适合买入吗？"（短词模糊） | 若 DB 中多个标的含"理想" → 流程 ABORTED，展示黄色 `st.warning`，列出所有候选标的名称，提示精确输入 |
| **R5** | 减仓未持有标的 | 输入"我想卖出贵州茅台，合适吗？" | 流程 ABORTED，展示红色 `st.error`，包含"无效操作"文案；`FlowStage` 为 ABORTED 而非 DONE |
| **R6** | 百分比字段为负数 | 在"策略设定"中将"单一持仓上限"设为合法范围内的值保存后，直接在 DB 中将 `max_single_stock_pct` 改为 `-10`，再触发分析 | 流程 ABORTED，提示"数据异常：百分比字段包含非法负值" |
| **R7** | 百分比字段为 0 | 在"策略设定"中将某百分比上限设为 `0`（若 UI 允许），或直接在 DB 中将 `max_single_stock_pct` 改为 `0` | 流程不报错，`0` 进入规则校验；规则校验中 `max_position=0.0`，所有持仓均 violation（因为任何持仓都 > 0%） |

---

## 6. 受影响文件清单

| 文件 | 修改内容 |
|------|---------|
| `app_pages/strategy.py` | BUG-01：移除入口 `st.stop()`，API Key 检查下移至点击后；BUG-04：LLM 修正提示；BUG-05：歧义/空仓中断的 UI 渲染分支 |
| `decision_engine/data_loader.py` | BUG-02：`_safe_pct` 负值报错；BUG-03：重构为 `_find_all_positions`（精确优先 + 歧义检测）；`LoadedData` 新增 `ambiguous_matches` 字段；BUG-06/07：单字/0值产品策略 |
| `decision_engine/llm_engine.py` | BUG-04：`LLMResult` 新增 `decision_corrected` / `original_decision` 字段；`_build_result` 记录修正事件 |
| `decision_engine/decision_flow.py` | BUG-03：歧义匹配中断逻辑；BUG-05：空仓减仓中断逻辑；BUG-02：`ValueError` 专属捕获分支 |

> **未改动文件**：`intent_parser.py`、`pre_check.py`、`rule_engine.py`、`signal_engine.py`、`streamlit_app.py` 及所有其他模块。

---

## 7. 注意事项 / 已知说明

以下行为属于**正常表现**，请勿误报为 Bug：

| 场景 | 说明 |
|------|------|
| 情绪信号固定显示"中性" | MVP 阶段固定值，未接入真实数据，属已知限制 |
| 用户画像显示"中高 / 长期增值" | Mock 数据，非真实用户设置，属已知限制 |
| 分析耗时 3~10 秒 | 依赖 Anthropic API 响应速度，有 Spinner 提示，属正常延迟 |
| "加仓贵州茅台（未持有）"流程正常跑完 | 未持有 + 加仓 = 新建仓场景，规则提示"新建仓操作"但**不中断流程**，属预期行为 |
| `position_ratio` 显示超过 100%（如 148%）| 当前仓位已超单标上限时的正常计算结果（如 22% / 15% = 148%） |
| 歧义提示展示多个标的 | BUG-03 修复后的预期行为，不是错误 |
| R7 测试中所有持仓显示 violation | `max_single_stock_pct=0` 时任意持仓都超限，属 0 值边界条件的正常计算结果 |
| `LLMResult.decision_corrected` 通常为 `False` | LLM 正常遵守 Prompt 输出标准格式，BUG-04 修复是防御性逻辑，正常情况不触发 |
