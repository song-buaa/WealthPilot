# 决策模块投研数据消费梳理 v1

> 基于代码快照 2026-04-24，纯事实梳理，不含改进建议。

---

## 1. 核心方法签名和位置

### 1.1 `_load_research`

| 项 | 值 |
|---|---|
| 文件 | `decision_engine/data_loader.py` |
| 行号 | 832–873 |
| 签名 | `def _load_research(session, pid: int, asset_name: Optional[str]) -> list[str]` |
| 返回类型 | `list[str]` — 带前缀标签的投研文本列表 |

### 1.2 `_distill_research_cards`

| 项 | 值 |
|---|---|
| 文件 | `decision_engine/data_loader.py` |
| 行号 | 175–292 |
| 签名 | `def _distill_research_cards(session, asset_name: str) -> list[str]` |
| 返回类型 | `list[str]` — 每条以 `[用户资料]` 为前缀，最多 5 条 |

### 1.3 `_search_research_online`

| 项 | 值 |
|---|---|
| 文件 | `decision_engine/data_loader.py` |
| 行号 | 295–443 |
| 签名 | `def _search_research_online(asset_name: str) -> list[str]` |
| 返回类型 | `list[str]` — 每条以 `[联网参考]` 为前缀，最多 8 条 |

### 1.4 `search_portfolio_research`（组合级搜索，独立方法）

| 项 | 值 |
|---|---|
| 文件 | `decision_engine/data_loader.py` |
| 行号 | 446–566 |
| 签名 | `def search_portfolio_research(positions: list) -> list[str]` |
| 返回类型 | `list[str]` — 每条以 `[联网参考]` 为前缀，最多 8 条 |

---

## 2. 数据流

### 2.1 `_load_research` — 三层融合编排器

```
入参: session (SQLAlchemy), pid, asset_name
  │
  ├── asset_name 为 None → 直接返回 _DEFAULT_MOCK_RESEARCH（L842-843）
  │
  ├── Layer 1: _distill_research_cards(session, asset_name) → card_research  (L846)
  │
  ├── Layer 2: 查 ResearchViewpoint 表（L851-863）
  │   ├── 过滤: object_name ILIKE %{asset_name}%
  │   ├── 排序: updated_at DESC, LIMIT 3
  │   ├── 提取: action_suggestion（≥15字）→ "[用户资料] 操作建议：..."
  │   ├── 提取: invalidation_conditions（≥15字）→ "[用户资料] 止损条件：..."
  │   └── 截断: vp_supplement[:2]
  │
  ├── 合并: user_research = card_research + vp_supplement  (L865)
  │
  └── Layer 3: _search_research_online(asset_name) → online  (L869/872)
      ├── user_research 为空 → 返回 online，online 也为空 → 返回 _DEFAULT_MOCK_RESEARCH
      └── user_research 非空 → 返回 user_research + online[:8]
```

最终返回格式示例：
```python
[
  "[用户资料] 核心论点：公司现金流健康...",      # 来自 Layer 1
  "[用户资料] 操作建议：维持持仓...",            # 来自 Layer 2
  "[联网参考][ref:https://...] 净利润同比...",   # 来自 Layer 3
]
```

### 2.2 `_distill_research_cards` — ResearchCard → LLM 蒸馏

```
入参: session, asset_name
  │
  ├── 缓存命中? → _CARD_DISTILL_CACHE.get(asset_name)  (L184-189)
  │   └── 命中且未过期 → 直接返回 cached data
  │
  ├── DB 查询: ResearchCard JOIN ResearchDocument  (L192-200)
  │   ├── 过滤: ResearchDocument.object_name ILIKE %{asset_name}%
  │   ├── 过滤: ResearchDocument.parse_status IN ("parsed", "saved_only")
  │   ├── 排序: ResearchDocument.uploaded_at DESC
  │   └── LIMIT 5
  │
  ├── 无结果 → 返回 []  (L201-202)
  │
  ├── 字段拼装 (L206-238):
  │   对每张 card 提取:
  │   ├── card.thesis        → "核心论点：..."
  │   ├── card.bull_case     → "看多逻辑：..."
  │   ├── card.bear_case     → "看空风险：..."
  │   ├── card.key_drivers   → "关键驱动：...；..." (JSON 字段，取前 4 条)
  │   ├── card.risks         → "主要风险：...；..." (JSON 字段，取前 3 条)
  │   └── card.action_suggestion → "操作建议：..."
  │   多张 card 之间用 "\n\n---\n" 拼接为 combined 字符串
  │
  ├── LLM 调用 (L240-273):
  │   ├── 模型: gpt-4.1-mini
  │   ├── max_tokens: 400
  │   ├── timeout: 15s
  │   ├── system: "你是投研助手，擅长从结构化投研资料中提炼关键投资观点。输出语言为中文，简洁专业。"
  │   └── user: "以下是用户上传的关于「{asset_name}」的投研资料解析内容：\n\n{combined}\n\n
  │             请从中提炼出最重要的3-5个投资观点，按重要性从高到低排序。\n
  │             要求：\n- 每条必须是完整的结论性句子，不少于15字，不超过60字\n
  │             - 禁止输出标题、前言、分节符\n- 每条以「- 」开头\n
  │             - 如果多份资料有矛盾，保留最重要的正反两面各一条"
  │
  ├── 解析输出 (L274-283):
  │   ├── 按 \n 拆行
  │   ├── 去除列表标记 (- • · * 1. 2.)
  │   ├── 过滤: len(cleaned) >= 15
  │   └── 加前缀: "[用户资料] {line}"，取前 5 条
  │
  ├── 异常处理: 任何 Exception → print 日志 + 返回 []  (L287-292)
  │
  └── 写入缓存: _CARD_DISTILL_CACHE[asset_name] = (time.time(), result)  (L285-286)
```

### 2.3 `_search_research_online` — 四维并行联网搜索

```
入参: asset_name
  │
  ├── 缓存命中? → _get_cached_research(asset_name)  (L307-309)
  │
  ├── API Key 选择 (L311-330):
  │   ├── PERPLEXITY_API_KEY 存在 → client=Perplexity, model=sonar-pro
  │   ├── 仅 OPENAI_API_KEY → client=OpenAI, model=gpt-4o-search-preview
  │   └── 均无 → 返回 []
  │
  ├── 构建 4 个 query (L332-341):
  │   （详见第 6 节 Perplexity 调用细节）
  │
  ├── 并行执行 (L401-405):
  │   └── ThreadPoolExecutor(max_workers=4)，4 个 query 并行
  │       每个 query → _run_single_query():
  │         ├── LLM 调用: model=sonar-pro/gpt-4o-search-preview, max_tokens=400, timeout=20s
  │         ├── 提取 annotations → url_map {end_index: url}
  │         ├── _parse_research_lines(content) → 文本行
  │         ├── 为每行匹配最近的 annotation URL → "[ref:url] text" 或 "text"
  │         └── 异常静默: print + return []
  │
  ├── 去重 (L407-421):
  │   ├── key = 文本前 30 字符（跳过 [ref:...] 前缀）
  │   └── 保留首次出现
  │
  ├── 加前缀 (L423-434):
  │   ├── 有 [ref:url] → "[联网参考][ref:url] text"
  │   └── 无 URL → "[联网参考] text"
  │   └── 截断: unique[:8]
  │
  └── 写入缓存: _RESEARCH_CACHE[asset_name] = (time.time(), result)  (L436-437)
```

### 2.4 `search_portfolio_research` — 组合宏观搜索

```
入参: positions (list of PositionInfo)
  │
  ├── 缓存: key = "__portfolio__"  (L451)
  │
  ├── 固定 4 个宏观 query (L475-480):
  │   ├── "A股和港股市场整体走势"
  │   ├── "美股纳斯达克走势和科技股表现"
  │   ├── "债券市场利率走势和固收投资环境"
  │   └── "黄金价格和大宗商品走势"
  │
  ├── 动态行业推断 (L482-494):
  │   ├── 从 positions[:15] 的名称中匹配关键词 → 行业
  │   ├── 关键词映射: 新能源→新能源汽车, 科技→科技, AI→人工智能, 半导体→半导体 等
  │   └── 追加最多 2 个行业 query: "{sector}行业投资展望"
  │
  ├── 并行执行: ThreadPoolExecutor(max_workers=4)  (L536)
  │   ├── model/timeout/max_tokens 与 _search_research_online 相同
  │   └── 但 max_tokens=300（vs 单标的的 400）
  │
  ├── 去重 + 加前缀: 同 _search_research_online，上限 8 条
  │
  └── 缓存 key = "__portfolio__"  (L561)
```

---

## 3. 上游调用点

### 3.1 `_load_research` 被谁调用

| 调用位置 | 调用者 | 行号 | 传入参数 |
|---------|--------|-----|---------|
| `data_loader.py` | `load()` | L718 | `session=当前 DB session, pid=portfolio_id, asset_name=传入的 asset_name（可为 None）` |

`load()` 本身被两处调用：

| 调用位置 | 调用者 | 行号 | asset_name 值 |
|---------|--------|-----|--------------|
| `decision_flow.py` | `_run_pipeline()` | L135 | `intent.asset`（如 "理想汽车"） |
| `decision_service.py` | `_stream_portfolio_intent()._run()` | L610 | 固定 `None` |

### 3.2 `_distill_research_cards` 被谁调用

| 调用位置 | 调用者 | 行号 | 传入参数 |
|---------|--------|-----|---------|
| `data_loader.py` | `_load_research()` | L846 | `session=同上, asset_name=原样透传` |

仅此一处。

### 3.3 `_search_research_online` 被谁调用

| 调用位置 | 调用者 | 行号 | 传入参数 |
|---------|--------|-----|---------|
| `data_loader.py` | `_load_research()` | L869 | `asset_name`（无用户资料时） |
| `data_loader.py` | `_load_research()` | L872 | `asset_name`（有用户资料时，取联网补充） |

仅在 `_load_research` 内部调用，同一次 `_load_research` 执行只会命中其中一个分支。

### 3.4 `search_portfolio_research` 被谁调用

| 调用位置 | 调用者 | 行号 | 传入参数 |
|---------|--------|-----|---------|
| `decision_service.py` | `_stream_portfolio_intent()._run()` | L615 | `loaded.positions`（聚合后的持仓列表） |

仅在 PortfolioReview 意图下调用。

---

## 4. 下游消费点

### 4.1 LLM Prompt 拼装

#### PositionDecision

- **Payload 构建**：`llm_engine.py:_build_position_payload()` L831
  ```python
  "research": data.research,
  ```
- **Prompt 消费规则**：`llm_engine.py` L130-185 `_POSITION_DECISION_PROMPT`
  ```
  关于基本面和投研信息：
  - research字段中有具体数字的，必须直接引用原始数字（如"净利润同比下降94%"），禁止替换为"大幅下滑"等模糊表述
  - [用户资料]标注的内容优先引用，[联网参考]标注的内容作为补充
  - 如果research字段为空或无有效内容，跳过基本面引用，不编造数字
  - 分析师评级如果存在（如"大和重申买入"），在核心依据中一条带出
  ```
- **chat_answer 首轮格式**：`llm_engine.py` L199
  ```
  - 基本面关键数字（直接引用research字段中的具体数据，禁止模糊表述）
  ```
- **链接引用规则**：L178-185
  ```
  - 有[ref:url]标注的联网参考，引用时该句末尾必须附 [[来源]](url)，不得省略
  - 无[ref:url]标注的联网参考，引用时该句末尾附"（据公开信息）"文字，不附链接
  - [用户资料]标注的内容不附链接
  ```
- **LLM 调用**：`llm_engine.py:reason()` L725，model=`gpt-4.1`

#### PortfolioReview

- **Payload 构建**：`llm_engine.py:_build_portfolio_payload()` L1083
  ```python
  "research": data.research,
  ```
- **Prompt 消费规则**：`llm_engine.py` L219-274 `_PORTFOLIO_REVIEW_PROMPT`
  ```
  ### 市场背景
  引用 research 字段中与持仓最相关的联网参考内容，2-3条，用"-"开头。
  每条引用必须附 [[来源]](url)。
  聚焦与持仓行业或大类资产直接相关的内容。
  如果 research 字段为空或无相关内容，跳过此段，不输出"市场背景"标题。
  ```
- **LLM 调用**：`llm_engine.py:review_portfolio()` L1222，model=`gpt-4.1`

#### AssetAllocation

- **Payload 构建**：`llm_engine.py:_build_allocation_payload()` L1127 调用 `_build_portfolio_payload()`，继承 `research` 字段
- **Prompt 消费规则**：`llm_engine.py` L277-370 `_ASSET_ALLOCATION_PROMPT`
  ```
  关于引用链接（强制执行）：凡引用带[ref:url]标注的联网内容，必须在句末附 [[来源]](url)。
  无[ref:url]标注的联网参考，末尾附"（据公开信息）"。[用户资料]标注的不附链接。
  ```
- **LLM 调用**：`llm_engine.py:analyze_allocation()` L1294，model=`gpt-4.1-mini`

#### PerformanceAnalysis

- **Prompt**：`llm_engine.py` L373-448 `_PERFORMANCE_ANALYSIS_PROMPT`
  ```
  关于引用链接：收益分析不引用联网数据，不需要附来源链接。
  ```
- **实际传入值**：`loaded.research = []`（L620 清空）
- **LLM 调用**：`llm_engine.py:analyze_performance()` L1267，但 research 字段为空数组

#### Education / GeneralChat

- **路由**：`decision_service.py:_stream_general_chat()` L839-858
- **LLM 调用**：`llm_engine.chat(user_input, None)` L843
- **不加载 LoadedData**，不经过 `data_loader.load()`，**不消费任何投研数据**

### 4.2 DecisionContext 中的字段放置

research 数据在 `LoadedData` 中的字段：

```python
# data_loader.py L104
@dataclass
class LoadedData:
    research: list[str]          # 投研观点文本列表
```

在 LLM payload 中统一放在 JSON 根级 `"research"` 键下（L831、L1083），作为 `list[str]` 直接序列化。

### 4.3 二次加工

| 场景 | 位置 | 加工方式 |
|------|-----|---------|
| PortfolioReview | `decision_service.py` L614-617 | **整体替换**：`loaded.research = macro_research` |
| PerformanceAnalysis | `decision_service.py` L619-620 | **清空**：`loaded.research = []` |
| 信号引擎情绪推断 | `signal_engine.py` L126-150 | `" ".join(data.research)` 后做正/负面关键词命中计数 |
| Explain Panel 序列化 | `decision_service.py` L708 | 原样透传：`[r for r in (loaded.research if loaded else [])]` |
| Explain Panel 序列化 | `decision_service.py` L1014 | 原样透传：`ld.research` |

没有对 research 列表做排序、去重或过滤的二次加工（去重在 `_search_research_online` 返回前已完成）。

### 4.4 信号引擎消费细节

`signal_engine.py:_compute_fundamental_signal()` L126-150：

```python
def _compute_fundamental_signal(data: LoadedData) -> str:
    if not data.research:
        return "N/A"
    if len(data.research) == 1 and "暂无" in data.research[0]:
        return "N/A"
    combined = " ".join(data.research)
    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in combined)
    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in combined)
    if pos_hits > neg_hits:
        return "正面"
    elif neg_hits > pos_hits:
        return "负面"
    else:
        return "中性"
```

注意：`[用户资料]` 和 `[联网参考]` 前缀文本也参与关键词匹配，未做剥离。

---

## 5. 缓存机制

### 5.1 `_CARD_DISTILL_CACHE`

| 项 | 值 |
|---|---|
| 定义位置 | `data_loader.py` L158 |
| 类型 | `dict[str, tuple[float, list[str]]]` |
| Key 构造 | `asset_name` 原始字符串（如 "理想汽车"） |
| Value | `(写入时的 time.time(), 蒸馏结果 list[str])` |
| TTL | 24 小时 = 86400 秒（`_CARD_DISTILL_TTL`，L160） |
| 过期判断 | `time.time() - ts <= _CARD_DISTILL_TTL`（L188） |
| 淘汰方式 | 惰性：过期条目不主动删除，下次 get 时发现过期则 fallthrough 重新计算 |
| 写入条件 | `if result:`（L285），空结果不写入 |
| 主动失效 | 无。无手动清除接口，无 DB 变更通知 |
| 进程重启 | 全部丢失（纯内存 dict） |

### 5.2 `_RESEARCH_CACHE`

| 项 | 值 |
|---|---|
| 定义位置 | `data_loader.py` L157 |
| 类型 | `dict[str, tuple[float, list[str]]]` |
| Key 构造 | 单标的：`asset_name` 原始字符串；组合级：固定 `"__portfolio__"`（L451/561） |
| Value | `(写入时的 time.time(), 搜索结果 list[str])` |
| TTL | 4 小时 = 14400 秒（`_CACHE_TTL_SECONDS`，L159） |
| 过期判断 | `time.time() - ts > _CACHE_TTL_SECONDS`（L169） |
| 淘汰方式 | 惰性 + 主动删除过期条目：`del _RESEARCH_CACHE[asset_name]`（L170） |
| 写入条件 | `if result:`（L436/560），空结果不写入 |
| 主动失效 | 无 |
| 进程重启 | 全部丢失 |

### 5.3 命中率估算方式

代码中**无命中率统计**。无计数器、无日志标记。只能通过 print 日志间接判断：
- `_distill_research_cards` 缓存命中时直接 return，不打印任何日志
- `_search_research_online` 缓存命中时直接 return，不打印任何日志
- 缓存 miss 触发 LLM/API 调用时会打印：`[data_loader] 联网搜索完成 (xxx): N 条结果`

---

## 6. Perplexity 调用细节

### 6.1 Endpoint 与客户端

```python
# data_loader.py L324-327
client = _openai.OpenAI(
    api_key=perplexity_key,
    base_url="https://api.perplexity.ai",
)
model = "sonar-pro"
```

使用 OpenAI Python SDK 的兼容接口调用 Perplexity。无 Perplexity Key 时降级：

```python
# L329-330
client = _openai.OpenAI(api_key=openai_key)
model = "gpt-4o-search-preview"
```

### 6.2 单标的搜索：4 个 Query 模板原文

```python
# data_loader.py L337-340
current_year = datetime.now().year

queries = [
    f"请搜索「{asset_name}」{current_year}年财报数据，以中文返回2条简洁摘要。每条不超过80字，聚焦：营收、净利润、同比变化、毛利率等关键财务指标。每条必须在开头标注数据对应的年月（如[2026-03]），格式：以「- [YYYY-MM] 」开头，禁止输出标题和链接。",

    f"请搜索「{asset_name}」{current_year}年交付量或销量数据，以中文返回2条简洁摘要。每条不超过80字，聚焦：月度/季度交付量、同比环比变化、市场份额。每条必须在开头标注数据对应的年月，格式：以「- [YYYY-MM] 」开头，禁止输出标题和链接。",

    f"请搜索「{asset_name}」{current_year}年分析师评级和目标价，以中文返回2条简洁摘要。每条不超过80字，聚焦：机构名称、评级变动、目标价。每条必须在开头标注发布年月，格式：以「- [YYYY-MM] 」开头，禁止输出标题和链接。",

    f"请搜索「{asset_name}」{current_year}年最新动态和风险因素，以中文返回2条简洁摘要。每条不超过80字，聚焦：产品发布、战略变化、行业竞争、政策风险。每条必须在开头标注事件年月，格式：以「- [YYYY-MM] 」开头，禁止输出标题和链接。",
]
```

System prompt（L345）：
```
你是一个专业的投资研究助手，擅长从市场最新信息中提炼简洁的投研观点。每条摘要必须在开头标注数据对应的年月（格式 [YYYY-MM]），如无法确定则标注 [日期未知]。
```

### 6.3 组合级搜索：4+N 个 Query 模板原文

固定 4 个（L476-480）：
```python
f"请搜索{current_year}年A股和港股市场整体走势，返回2条简洁摘要。每条不超过80字。格式：以「- 」开头。"
f"请搜索{current_year}年美股纳斯达克走势和科技股表现，返回2条简洁摘要。每条不超过80字。格式：以「- 」开头。"
f"请搜索{current_year}年债券市场利率走势和固收投资环境，返回2条简洁摘要。每条不超过80字。格式：以「- 」开头。"
f"请搜索{current_year}年黄金价格和大宗商品走势，返回1条简洁摘要。每条不超过80字。格式：以「- 」开头。"
```

动态行业 query（L491-494）：最多追加 2 条，模板：
```python
f"请搜索{current_year}年{sector}行业投资展望，返回1条简洁摘要。不超过80字。格式：以「- 」开头。"
```

System prompt（L498）：`"你是投资研究助手，擅长提炼宏观和行业投研观点。"`

### 6.4 并行与超时

| 项 | 单标的 `_search_research_online` | 组合级 `search_portfolio_research` |
|---|---|---|
| 并行度 | `ThreadPoolExecutor(max_workers=4)` L402 | `ThreadPoolExecutor(max_workers=4)` L536 |
| 单次 timeout | 20s（L353） | 20s（L503） |
| max_tokens | 400（L352） | 300（L503） |
| 重试逻辑 | **无**。单维度失败 → print 日志 + 返回 `[]`（L397） | **无**。同（L531-533） |
| 整体异常 | 外层 try/except → print + 返回 `[]`（L441-443） | 外层 try/except → print + 返回 `[]`（L564-566） |

### 6.5 URL 提取逻辑

从 LLM 响应的 `annotations` 字段提取（L363-372）：
```python
annotations = getattr(msg, "annotations", None) or []
for ann in annotations:
    cite = getattr(ann, "url_citation", None)
    if cite and hasattr(cite, "url") and hasattr(cite, "end_index"):
        url = cite.url
        url = re.sub(r'[?&]utm_source=[^&]*', '', url).rstrip('?&')
        url_map[cite.end_index] = url
```

行文本与 URL 匹配（L378-393）：
```python
line_pos = content.find(line[:30])
if line_pos >= 0:
    line_end = line_pos + len(line) + 50
    for end_idx, url in url_map.items():
        if line_pos <= end_idx <= line_end:
            matched_url = url
```

逻辑：取文本行在原始 content 中的位置，向后扩展 50 字符，找落在此范围内的 annotation end_index 对应的 URL。

### 6.6 `_parse_research_lines` 辅助函数

位置：`data_loader.py` L569-612

处理流程：
1. 按 `\n` 拆行
2. 跳过空行和 ≤8 字符的短行
3. 去除列表标记：`^[-•·*\s\t]*(?:\d{1,2}\.\s*)?`
4. 去除 `**` 粗体标记
5. 跳过以 `:` 或 `：` 结尾的行（通常是标题）
6. 提取 inline URL：
   - 模式 1：`([text](url))` → 提取 url，截断 markdown 链接
   - 模式 2：`(https://...)` → 提取 url，截断括号
7. 清理 URL 中的 `utm_source` 参数
8. 最终格式：`"[ref:url] text"` 或 `"text"`

---

## 7. 各意图对投研数据的消费差异

### 7.1 PositionDecision（单标的决策）

| 项 | 说明 |
|---|---|
| 数据来源 | `_load_research(session, pid, asset_name="具体标的名")` |
| 三层融合 | 全部启用（ResearchCard 蒸馏 + ViewPoint 补充 + 联网搜索） |
| 替换/清空 | 无 |
| Prompt 消费 | `_POSITION_DECISION_PROMPT`：要求引用具体数字，区分 [用户资料]/[联网参考] 优先级 |
| 信号引擎 | 参与 `_compute_fundamental_signal()` 关键词情绪推断 |
| Payload 字段 | `payload["research"] = data.research` (L831) |
| LLM 模型 | gpt-4.1 (L725) |
| Explain Panel | `data.research` 原样输出到前端 (L1014) |

### 7.2 PortfolioReview（组合评估）

| 项 | 说明 |
|---|---|
| 数据来源 | `data_loader.load(asset_name=None)` 先加载 → 返回 `_DEFAULT_MOCK_RESEARCH` |
| 替换 | `decision_service.py` L614-617：**整体替换**为 `search_portfolio_research(loaded.positions)` 的宏观搜索结果 |
| 三层融合 | 不走（因为 asset_name=None，`_load_research` 直接返回 mock，然后被替换） |
| Prompt 消费 | `_PORTFOLIO_REVIEW_PROMPT`：在"市场背景"段引用 2-3 条联网参考，每条必须附 [[来源]](url) |
| 信号引擎 | 替换后的宏观 research 参与情绪推断（但组合意图可能不走信号引擎） |
| Payload 字段 | `payload["research"] = data.research` (L1083) |
| LLM 模型 | gpt-4.1 (L1222) |
| Explain Panel | 替换后的 research 输出 (L708) |

### 7.3 AssetAllocation（资产配置）

| 项 | 说明 |
|---|---|
| 数据来源 | `data_loader.load(asset_name=None)` → `_DEFAULT_MOCK_RESEARCH` |
| 替换/清空 | **无**。不走 PortfolioReview 的替换分支，保留 mock 数据 |
| Prompt 消费 | `_ASSET_ALLOCATION_PROMPT`：引用链接规则与 PositionDecision 相同 |
| Payload 字段 | 继承自 `_build_portfolio_payload()` 的 `"research"` (L1083) |
| LLM 模型 | gpt-4.1-mini (L1294) |
| 实际效果 | research 内容为 mock（"暂无该标的的投研观点..."），Prompt 中有空值跳过逻辑 |

### 7.4 PerformanceAnalysis（收益分析）

| 项 | 说明 |
|---|---|
| 数据来源 | `data_loader.load(asset_name=None)` → `_DEFAULT_MOCK_RESEARCH` |
| 清空 | `decision_service.py` L619-620：`loaded.research = []` |
| Prompt 消费 | `_PERFORMANCE_ANALYSIS_PROMPT` L448：明确声明"收益分析不引用联网数据，不需要附来源链接" |
| Payload 字段 | `payload["research"] = []`（空数组） |
| LLM 模型 | gpt-4.1-mini（推测，与 PortfolioReview 共用 `_build_portfolio_payload`） |
| 实际效果 | 完全不消费投研数据 |

### 7.5 Education / GeneralChat（教育/通用对话）

| 项 | 说明 |
|---|---|
| 数据来源 | **不加载 LoadedData** |
| 路由 | `decision_service.py:_stream_general_chat()` L839-858 |
| LLM 调用 | `llm_engine.chat(user_input, None)` — 仅传入用户问题，无 research context |
| LLM 模型 | gpt-4.1-mini (L1027) |
| 实际效果 | 完全不消费投研数据 |

### 消费矩阵汇总

| 意图 | 加载方式 | 来源 | 二次加工 | Prompt 中是否消费 | 信号引擎 |
|------|---------|------|---------|-----------------|---------|
| PositionDecision | `load(asset="标的名")` | 三层融合 | 无 | ✅ 核心依据 | ✅ 情绪推断 |
| PortfolioReview | `load(asset=None)` + 替换 | 宏观联网搜索 | 整体替换 | ✅ 市场背景段 | ✅ |
| AssetAllocation | `load(asset=None)` | Mock 数据 | 无 | ⚠️ 传入但内容为 mock | ✅ 但返回 N/A |
| PerformanceAnalysis | `load(asset=None)` + 清空 | 无 | 清空为 [] | ❌ 明确不使用 | ❌ 返回 N/A |
| Education/GeneralChat | 不经过 load | 无 | — | ❌ 无 context | ❌ |
