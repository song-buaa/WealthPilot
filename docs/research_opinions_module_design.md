# 投研观点模块设计说明

> 文档用途：描述 WealthPilot「投研观点」模块的当前实际实现，供外部 AI 进行测试与评估。
> 仅描述已实现版本，不包含理想方案或未来规划。
> 生成日期：2026-03-20

---

## 1. 产品定位与用户价值

### 1.1 背景

WealthPilot 是一款个人投资管理工具。用户日常大量阅读研报、博主分析、会议纪要等外部研究资料，但这些内容分散在公众号、PDF、网页等渠道，难以沉淀为可检索、可追踪的结构化知识。

### 1.2 产品目标

「投研观点」模块的目标是：

- **把碎片化研究资料转化为结构化投研判断**，供用户在投资决策时快速调用
- **建立个人观点库**，追踪每个观点的有效性状态（是否已过时/失效）
- **在做投资决策时检索相关观点**，辅助判断

### 1.3 当前版本用户价值

1. 粘贴一段研报或博文，AI 自动提炼：核心结论、看多/看空逻辑、关键指标、操作建议
2. 用户审核 AI 提炼结果，选择认可、修改后认可、或丢弃
3. 认可的观点正式入库，可按标的/市场/方向/有效性等维度筛选查阅
4. 输入自然语言问题（如"美团现在适不适合加仓"），系统召回相关观点

---

## 2. 数据流程总览

```
用户粘贴原始资料
       │
       ▼
  ResearchDocument（原始资料表）
  - parse_status: pending / saved_only / discarded / parsed
       │
       │ AI 解析（generate_research_card）
       ▼
  ResearchCard（候选观点卡表）
  - 一份 Document 对应一张 Card（1:1）
  - 由 LLM 结构化提炼，用户尚未认可
       │
       │ 用户审核（认可 / 修改后认可）
       ▼
  ResearchViewpoint（正式观点库表）
  - 用户认可的观点，有有效性状态管理
  - 可被检索系统召回
```

状态流转图（parse_status）：
```
pending ──AI解析成功──► parsed ──用户认可──► (绑定 Viewpoint)
pending ──仅保存──────► saved_only
pending ──丢弃────────► discarded
parsed  ──丢弃────────► discarded
```

---

## 3. 核心功能与用户操作流程

### 3.1 Tab 1：资料导入（📥 资料导入）

**用户能做什么：**
- 填写基本信息：标题（必填）、标的名称、市场、作者、发布时间、标签、备注
- 选择资料类型（4 种）：
  - **纯文本**：直接在文本框粘贴正文
  - **Markdown**：上传 `.md/.txt` 文件，或直接粘贴 Markdown 内容（文件上传后预填到可编辑文本框）
  - **链接**：输入 URL + 手动粘贴正文（无自动抓取）
  - **PDF**：上传 PDF 文件（文件名保存为 source_url）+ 手动粘贴关键段落（无 PDF 解析）
- 点击两个按钮之一：
  - **「仅保存资料」**：存入 DB，parse_status = `saved_only`，不触发 AI
  - **「保存并立即 AI 解析」**：存入 DB（parse_status = `pending`），调用 LLM，成功后写入 ResearchCard，parse_status 改为 `parsed`，自动跳转到候选观点卡 Tab

**页面下方同时展示「已导入资料」列表：**
- 以 DataFrame 形式展示最多 50 条历史资料（标题/类型/标的/市场/状态/上传时间）
- 对于 `pending` 状态的资料，提供 selectbox + 按钮，可单独触发 AI 解析

**输入校验：**
- 标题为空 → 报错，不提交
- 正文为空 → 报错，不提交

---

### 3.2 Tab 2：候选观点卡（🃏 候选观点卡）

**展示规则：**
- 只展示 parse_status = `parsed` 的资料对应的卡片
- 分两组：「待审核」（未绑定 Viewpoint 的卡）/ 「已处理」（已绑定 Viewpoint 的卡，折叠显示）

**卡片内容展示（每张卡展开后显示）：**

| 左列（3份） | 右列（2份） |
|---|---|
| 摘要（summary） | 关键驱动（key_drivers，JSON 列表） |
| 核心结论 thesis | 主要风险（risks，JSON 列表） |
| 看多逻辑 bull_case | 观察指标（key_metrics，JSON 列表） |
| 看空逻辑 bear_case | 建议标签（suggested_tags，JSON 列表） |
| 操作建议 action_suggestion | |
| 失效条件 invalidation_conditions | |

标题行展示：方向（看多/看空/中性/观察）| 资料标题 | 时间维度（短/中/长期）| 创建日期

**四个操作按钮：**

| 按钮 | 行为 |
|---|---|
| ✅ 认可·直接录入 | 以 `strong` 认可度，从卡片数据直接创建 ResearchViewpoint，写入观点库 |
| ✏️ 修改后录入 | 展开编辑表单，用户可修改标题/标的/市场/类型/认可度/标签/thesis/操作建议/失效条件，提交后创建 Viewpoint |
| 💾 仅保留资料 | 将 Document parse_status 改为 `saved_only`，不创建 Viewpoint |
| 🗑️ 丢弃 | 将 Document parse_status 改为 `discarded`，不创建 Viewpoint |

**注意：** "修改后录入"的编辑表单只允许修改部分字段（标题、标的、市场、类型、认可度、标签、thesis、操作建议、失效条件）；其余字段（bull_case、bear_case、key_drivers、risks、key_metrics）直接从卡片原样复制到 Viewpoint，不可在此 UI 修改。

---

### 3.3 Tab 3：观点库（📚 观点库）

**筛选栏（5 维 + 关键词）：**
- 标的（所有观点中出现的 object_name，去重 selectbox）
- 市场（所有观点中的 market_name，去重 selectbox）
- 时间维度（short / medium / long）
- 方向（bullish / bearish / neutral / watch）
- 有效性（active / watch / outdated / invalid）
- 关键词搜索（在 title、thesis、action_suggestion 中做 Python `in` 字符串匹配，不分词）

**列表展示（每条 Expander 折叠）：**

Expander 标题：方向 | 观点标题 | 标的名称 | 时间维度 | 有效性 | 最后更新日期

展开后内容：
- 核心结论（thesis）
- 支撑逻辑（supporting_points，JSON 列表）
- 对立逻辑（opposing_points，JSON 列表）
- 操作建议
- 失效条件
- 观察指标（key_metrics，JSON 列表）
- 右侧边栏：认可程度、市场、标签、风险、有效性快速修改下拉

**有效性快速修改：**
- 右侧 selectbox 下拉选择新状态
- 当状态与当前不同时，出现「更新」按钮
- 点击按钮写入 DB，同时更新 updated_at

---

### 3.4 Tab 4：检索测试（🔍 检索测试）

**输入参数：**
- 查询语句（自然语言，如"美团现在适不适合加仓"）
- 精确标的（可空，辅助精确匹配）
- 市场（可空，辅助市场过滤）
- 最多返回条数（Slider，1-10，默认 5）
- 是否包含 outdated/invalid 观点（Checkbox，默认 False）

**校验：** 查询语句和标的名称至少填一项，否则提示警告不执行检索

**召回结果展示：**
- 按相关性评分倒序排列，第一条默认展开
- 每条展示：方向 | 标题 | 标的 | 时间维度 | 有效性 | 综合得分
- 展开内容：核心结论、操作建议、支撑逻辑（列表）、风险（列表）、失效条件
- 底部 caption：认可度、标签、最后更新日期

---

## 4. 页面结构与导航

### 4.1 导航实现

文件：`app_pages/research.py`，函数 `_research_nav()`

采用与其他页面统一的三级降级方案：
1. **优先**：`st.segmented_control`（Streamlit ≥ 1.40），带 `use_container_width=True`
2. **次选**：`st.segmented_control` 不带 `use_container_width`（Streamlit 1.36-1.39，捕获 `TypeError`）
3. **降级**：4 列等宽 `st.button` 组，Primary/Secondary 类型模拟选中态

导航状态存储在 `st.session_state["research_nav"]`，切换 Tab 不会触发页面数据重置。

### 4.2 主入口

```python
def render() -> None:
    init_db()           # 幂等建表，确保三张 research_* 表存在
    st.title("投研观点")
    active_nav = _research_nav()
    # 路由到 4 个 Section 函数之一
```

`init_db()` 在每次渲染时调用，但底层使用 SQLAlchemy `create_all(checkfirst=True)`，实际建表只在首次执行，重复调用安全无副作用。

---

## 5. 相关代码文件与职责

| 文件 | 职责 |
|---|---|
| `app_pages/research.py` | 完整 Streamlit UI（~900 行），包含 4 个 Tab 的所有渲染逻辑、导航、表单处理、DB 读写 |
| `app/models.py` | 三张数据库表的 SQLAlchemy ORM 定义：`ResearchDocument`、`ResearchCard`、`ResearchViewpoint` |
| `app/ai_advisor.py` | `generate_research_card()` 函数：调用 OpenAI API，将原文提炼为结构化 JSON 候选卡 |
| `app/research.py` | `retrieve_research_context()` 检索引擎：多因子评分召回，以及内部工具函数 `_parse_json_list`、`_keyword_score`、`_score_viewpoint` |
| `app/config.py` | `AI_RESEARCH_MODEL = "gpt-4.1-mini"`、`AI_RESEARCH_MAX_TOKENS = 2000` |
| `streamlit_app.py` | 全局路由：将"投研观点"导航项映射到 `research.render()` |

---

## 6. 数据库表结构详述

### 6.1 `research_documents`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 自增主键 |
| title | String(300) | 资料标题，必填 |
| source_type | String(20) | text / markdown / link / pdf |
| source_url | Text | URL 或 PDF 文件名，可为 null |
| raw_content | Text | 原始正文，全量存储 |
| uploaded_at | DateTime | 上传时间，默认 now |
| publish_time | String(50) | 允许模糊格式（如"2025-Q1"） |
| author | String(100) | 作者/来源，可为 null |
| object_name | String(100) | 标的名称，可为 null |
| market_name | String(50) | 市场（港股/美股/A股/宏观等），可为 null |
| tags | Text | JSON 字符串列表，可为 null |
| parse_status | String(20) | pending / parsed / saved_only / discarded |
| notes | Text | 用户备注，可为 null |

### 6.2 `research_cards`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 自增主键 |
| document_id | Integer FK | 关联 research_documents.id |
| summary | Text | 资料主要讲什么（1-2句）|
| thesis | Text | 核心投研结论 |
| bull_case | Text | 看多逻辑 |
| bear_case | Text | 看空/风险逻辑 |
| key_drivers | Text | JSON 字符串列表：关键驱动因素 |
| risks | Text | JSON 字符串列表：主要风险 |
| key_metrics | Text | JSON 字符串列表：待观察指标 |
| horizon | String(20) | short / medium / long / null |
| stance | String(20) | bullish / bearish / neutral / watch / null |
| action_suggestion | Text | 操作建议 |
| invalidation_conditions | Text | 失效条件 |
| suggested_tags | Text | JSON 字符串列表：AI 建议标签 |
| created_at | DateTime | 创建时间 |

一份 Document 对应一张 Card（1:1），重复解析不会自动清理旧 Card（已有 Card 的 document 再次被解析会创建第二张 Card，当前 UI 不暴露此场景但逻辑上可能发生）。

### 6.3 `research_viewpoints`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 自增主键 |
| title | String(300) | 观点标题 |
| object_type | String(20) | asset / sector / market / macro / strategy |
| object_name | String(100) | 标的名称 |
| market_name | String(50) | 市场 |
| topic_tags | Text | JSON 字符串列表 |
| thesis | Text | 核心结论 |
| supporting_points | Text | JSON 字符串列表（来源：card.key_drivers） |
| opposing_points | Text | JSON 或纯文本（来源：card.bear_case，非 JSON 列表） |
| key_metrics | Text | JSON 字符串列表 |
| risks | Text | JSON 字符串列表 |
| action_suggestion | Text | 操作建议 |
| invalidation_conditions | Text | 失效条件 |
| horizon | String(20) | short / medium / long |
| stance | String(20) | bullish / bearish / neutral / watch |
| user_approval_level | String(20) | strong / partial / reference |
| validity_status | String(20) | active / watch / outdated / invalid |
| source_card_id | Integer FK | 来源 Card（可为 null，预留手动创建路径） |
| source_document_id | Integer FK | 来源 Document（可为 null） |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 最后更新时间（有效性修改时手动更新） |

---

## 7. AI 解析模块详述

### 7.1 调用方式

文件：`app/ai_advisor.py`，函数 `generate_research_card()`

```python
def generate_research_card(
    raw_content: str,   # 原始正文（最多取前 4000 字符）
    title: str,         # 资料标题
    object_name: str,   # 标的名称（可为空字符串）
    market_name: str,   # 市场（可为空字符串）
) -> dict:              # 返回结构化 dict，或 {"error": "<msg>"}
```

- 使用 OpenAI Python SDK（非 Anthropic）
- 模型：`gpt-4.1-mini`（来自 `app/config.py`）
- 温度：0.3（全局 `AI_TEMPERATURE`）
- 最大 Token：2000
- 启用 `response_format={"type": "json_object"}` 强制 JSON 输出
- 原文超过 4000 字符时截断（`raw_content[:4000]`）

### 7.2 输出字段规范

LLM 被要求输出以下固定字段（缺失信息填 null，不允许编造）：

| 字段 | 类型 | 枚举约束 |
|---|---|---|
| summary | string | 1-2 句 |
| thesis | string | 核心判断，直接说结论 |
| bull_case | string\|null | 看多逻辑 |
| bear_case | string\|null | 看空逻辑 |
| key_drivers | array[string] | 驱动因素列表 |
| risks | array[string] | 风险列表 |
| key_metrics | array[string] | 观察指标列表 |
| horizon | string\|null | 仅 short/medium/long |
| stance | string\|null | 仅 bullish/bearish/neutral/watch |
| action_suggestion | string\|null | 加仓/减仓/持有观察/避开等 |
| invalidation_conditions | string\|null | 失效条件 |
| suggested_tags | array[string] | 标签 |

### 7.3 错误处理

返回 `{"error": "<msg>"}` 的情况：
- `OPENAI_API_KEY` 环境变量未设置 → `"配置错误：..."`
- LLM 返回内容无法解析为 JSON → `"AI 返回结果无法解析为 JSON：..."`
- 其他异常（网络、超时等）→ `"AI 解析失败：..."`

---

## 8. 检索模块详述

文件：`app/research.py`，函数 `retrieve_research_context()`

### 8.1 工作流程

1. 从 DB 查询所有 ResearchViewpoint
2. 默认过滤 `validity_status NOT IN (outdated, invalid)`（即只保留 active/watch）
3. 对每条 Viewpoint 调用 `_score_viewpoint()` 计算综合得分
4. 按得分降序排序，取 top_k 条
5. 返回 list[dict]，每个 dict 包含关键字段 + `_score`

### 8.2 评分规则（`_score_viewpoint`）

| 评分项 | 分值 |
|---|---|
| object_name 精确匹配（大小写不敏感） | +20 |
| object_name 模糊包含 | +10 |
| market_name 包含匹配 | +8 |
| topic_tags 中每个标签与 query 词命中 | +5/次 |
| thesis / supporting_points / opposing_points / risks / action_suggestion 关键词命中 | +1/词 |
| validity_status 权重：active=10, watch=5, outdated=1, invalid=0 | +固定值 |
| user_approval_level 权重：strong=3, partial=2, reference=1 | +固定值 |
| 新鲜度：180天内线性衰减，最多 +5 | +0~5 |

关键词匹配规则（`_keyword_score`）：
- 按空格和中文标点分词（不使用 jieba 等分词库）
- 保留 2 字及以上的词（过滤单字）
- 在目标字段中做 `in` 字符串匹配（大小写不敏感）
- 中文不分词，按原词组匹配

---

## 9. 已实现 vs 未实现

### 9.1 已完整实现

- [x] 三张数据库表及 ORM 模型
- [x] 资料导入表单（纯文本/Markdown 上传/链接/PDF 上传4种类型）
- [x] AI 解析调用（`generate_research_card`）及结果写库
- [x] 候选观点卡展示（待审核/已处理分组）
- [x] 4 种审核动作（认可/修改后认可/仅保留/丢弃）
- [x] 修改后录入的编辑表单（部分字段可编辑）
- [x] 观点库展示（5 维筛选 + 关键词搜索）
- [x] 有效性状态快速修改（下拉 + 更新按钮）
- [x] 关键词检索召回（多因子评分）
- [x] 检索结果展示（含得分）

### 9.2 简化实现（功能存在但有明显局限）

- **Markdown 上传**：文件内容读取后预填到 `st.text_area`，等同于手动粘贴，无特殊处理
- **PDF 上传**：只记录文件名到 `source_url`，无 PDF 文本提取，依赖用户手动粘贴关键段落
- **链接导入**：只存储 URL，无网页自动抓取，依赖用户手动粘贴内容
- **观点库关键词搜索**：Python `in` 匹配，无分词，仅在 title/thesis/action_suggestion 三个字段搜索
- **检索分词**：正则按标点和空格切分，无 jieba/结巴等中文分词，短语匹配可能不准
- **重复解析保护**：若一份 Document 被多次触发 AI 解析，会创建多张 Card，UI 不做去重处理

### 9.3 未实现（当前为占位或注释提示）

- 网页/公众号自动抓取（代码中标注 `🚧 规划中`）
- PDF 自动文字识别和解析（代码中标注 `🚧 规划中`）
- 手动直接创建 Viewpoint（绕过 Document → Card 流程）
- 批量导入（多份资料批量 AI 解析）
- 观点编辑（Viewpoint 入库后不支持编辑全部字段，只能修改 validity_status）
- 观点删除（无 Delete 操作入口）
- 资料删除（无 Delete 操作入口）
- 观点与持仓的关联（如自动匹配当前持有的标的）
- Embedding / RAG 检索（代码注释中标注为后续升级路径）
- 检索结果被投资决策页使用（`retrieve_research_context` 接口已定义，但决策页未接入）

---

## 10. 已知限制与潜在风险

### 10.1 数据完整性

**问题：重复 Card**
- 若用户对同一份 `pending` Document 多次点击「AI 解析」，每次都会创建新的 ResearchCard
- 候选观点卡 Tab 会展示该 Document 的所有 Card
- 当前代码无去重逻辑

**问题：opposing_points 字段类型不一致**
- 候选卡的 `bear_case` 是纯文本（Text），写入 Viewpoint 的 `opposing_points` 字段时直接赋值
- 但 `opposing_points` 在展示时先尝试 `_parse_json_list()` 解析，失败则 fallback 为 `[bear_case]`
- 导致该字段在观点库中表现为单条列表，而非结构化列表

**问题：DB 无迁移机制**
- 使用 SQLAlchemy `create_all(checkfirst=True)`，仅首次建表
- 若表结构变更（如新增字段），不会自动 ALTER TABLE，需手动处理

### 10.2 AI 解析质量

- 原文超过 4000 字符会被截断，长研报的后半部分内容丢失
- LLM 对 `horizon`/`stance` 枚举的遵守依赖 prompt 约束，偶发返回非枚举值（如 `"medium-long"`），写库后检索时可能出现意外分组
- `response_format={"type": "json_object"}` 要求模型支持此参数（OpenAI gpt-4系列支持，其他模型可能报错）

### 10.3 检索准确性

- 无向量检索，语义相似但用词不同的查询召回效果有限
- 中文不分词，短语匹配（如"港股流动性"不会拆成"港股"+"流动性"）
- 观点库条数较少时（如 < 5 条），top_k 设置无意义，全部返回
- 所有观点都参与评分（全表扫描），数量极多时性能退化，但当前体量下无问题

### 10.4 UI 交互

- 候选观点卡 Tab 的「修改后录入」编辑表单状态存储在 `session_state`（key: `card_{id}_editing`），刷新页面后状态丢失，表单内容也一并丢失
- 观点库页面无分页，观点数量极大时会一次性渲染所有 Expander，可能出现性能问题
- 有效性修改「更新」按钮点击后，`st.success("已更新")` 会在下次 rerun 时消失，无持久反馈

### 10.5 API Key 依赖

- AI 解析功能需要有效的 `OPENAI_API_KEY` 环境变量
- 未设置时，点击「AI 解析」按钮会弹出错误提示，但不影响「仅保存」流程
- 模型名称 `gpt-4.1-mini` 为 OpenAI 自定义命名，需确认环境内该模型名称可用

---

## 11. 文件依赖关系

```
streamlit_app.py
    └── app_pages/research.py (render 入口)
            ├── app/models.py
            │       ├── ResearchDocument
            │       ├── ResearchCard
            │       ├── ResearchViewpoint
            │       └── get_session, init_db (re-export from app/database.py)
            ├── app/ai_advisor.py
            │       └── generate_research_card()
            │               └── app/config.py (AI_RESEARCH_MODEL, AI_RESEARCH_MAX_TOKENS)
            └── app/research.py
                    └── retrieve_research_context()
                            └── app/models.py (ResearchViewpoint, get_session)
```

---

## 12. 测试建议

评估时重点关注以下场景：

| 场景 | 关注点 |
|---|---|
| 纯文本导入 + 立即解析 | AI 返回 JSON 是否合规，字段是否完整 |
| Markdown 文件上传 | 文件读取编码是否正常，内容是否正确预填 |
| PDF 上传 | 是否只存文件名，不触发 AI 解析 PDF 本身 |
| AI 解析失败（无 API Key） | 错误提示是否清晰，DB 数据是否保持一致 |
| 多次触发同一文档解析 | 是否产生重复 Card，展示是否异常 |
| 候选卡「认可·直接录入」 | Viewpoint 字段是否完整，parse_status 是否正确 |
| 候选卡「修改后录入」 | 编辑字段是否覆盖 AI 输出，其余字段是否保留 |
| 观点库筛选 | 多维度组合筛选是否正确交集 |
| 有效性状态更新 | updated_at 是否同步更新，检索评分新鲜度是否体现 |
| 检索（object_name 精确匹配） | 精确匹配是否得分最高，排序是否合理 |
| 检索（query 空，object_name 有值） | 是否能正常召回 |
| 观点库为空时检索 | 是否返回友好空结果提示 |
