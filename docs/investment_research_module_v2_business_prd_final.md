# 投研观点模块 v2 · 业务 PRD(终稿)

> **版本**:v2.0-final
> **日期**:2026-04-21
> **状态**:业务 PRD 定稿,进入工程 PRD 阶段
> **前置阅读**:`docs/investment_research_module_current_state_v1.md`(代码现状)
> **迭代历程**:draft-1(初版) → draft-2(GPT 交叉评审第一轮) → final(GPT 交叉评审第二轮 + 用户拍板)

---

## 1. v2 定位澄清(最关键,必读)

### 1.1 v2 不是"给投研模块加一个 Alpha Vantage 数据源"

虽然 Alpha Vantage MCP 的接入是 v2 的触发事件,但 v2 的核心任务并不是"引入新信息源"。代码现状梳理揭示了一个更本质的问题:投研模块当前**同时跑着两条独立的路径**,它们互不共享、互相绕过——v2 必须先解决这个结构问题,新信息源才能真正落地。

### 1.2 v2 的核心问题诊断

**问题一:两条投研路径并存**

- 路径 A(投研模块侧):`Research.tsx → research_service → retrieve_research_context`
- 路径 B(决策模块侧):`decision_service → data_loader → _distill_research_cards + _search_research_online`

路径 B **完全绕开**路径 A,理由是注释里明确写明的"ResearchViewpoint 字段容易残缺"。前端 Tab 3"决策检索"用户以为在预览决策会看到什么,实际走的是路径 A——**与真实决策链路不是同一条**。

**问题二:ViewpointCard 信息残缺的根源**

信息在"Card → Viewpoint"这一步丢失。根源是:

- `approve_card` 允许用户编辑机器生成的事实字段,但不强制补全
- Card 的 `bull_case`(纯文本) 被赋值给 Viewpoint 的 `opposing_points`(list 字段),存在字段错位 bug
- 用户实际只会修改少数字段,其余字段透传时丢失或类型混乱

决策模块不信任 Viewpoint,于是回到 Card 层用 LLM 二次提炼——**绕路是表象,schema 设计问题是本质**。

**问题三:持仓和观点的关联仅靠 ILIKE 文本匹配**

持仓表的 `asset_name` 和观点表的 `object_name` 都是用户自由文本。匹配用 `ILIKE %asset_name%`,完全依赖用户文字写法一致。如果持仓写"理想汽车-W",观点写"LI",二者永远匹配不上,代码不会报错。

### 1.3 v2 要做什么

**统一两条路径,让投研模块成为决策链路的主要信息源。**

具体交付三件事:

1. **重新分层 ViewpointCard schema**,把"机器事实 / 人的判断 / 叙事"三类数据结构分离,从根本上消除残缺问题
2. **把联网搜索能力从决策模块搬到投研模块**,作为 InfoAdapter 的一个实现,与 Alpha Vantage、用户上传并列
3. **引入 Symbol + Entity 两级标准化**,投研观点和持仓通过标准 symbol 强关联,同时支持跨市场的公司级视图

### 1.4 "直接消费"不等于"盲目相信"

v2 实现后,决策模块**直接消费** ViewpointCard——不再有独立提炼路径。但决策模块**仍然按 confidence、endorsement、validity_status 做过滤**,这是主动选择,不是不信任。

ViewpointCard 的质量保证分为两层:入库时 schema 强校验(机器层可靠),审核时判断层强制必填(人层可控)。仍然可能残留错误判断入库,因此决策引擎侧**保留权重过滤机制**,见 §6.4。

### 1.5 v2 不做什么

- **不推倒重来**:DB 三张表的字段绝大多数保留;新增字段,不大面积删除字段
- **不改投研模块的核心交互**:上传研报、候选卡审核、观点库检索这些主流程保留
- **不做前端大改版**:Research.tsx 继续是单页三 Tab;Tab 3"决策检索"行为要修正,但不重构 UI
- **不做向量检索**:v2 仍不上 embedding,检索层保留当前的打分机制,只是评分字段适配新 schema
- **不碰 A 股深度覆盖**:v2 的 InfoAdapter 只包含 UserUpload、AlphaVantage、Perplexity
- **不做 MCP Server 暴露**:WealthPilot 自身作为 MCP Server 对外暴露是 v3 的事

### 1.6 v2 交付后应该看到的状态

- 决策模块里的 `_distill_research_cards`、`_search_research_online`、`_load_research` 全部删除或替换
- 前端 Tab 3"决策检索"返回结果与决策模块真实看到的一致
- ViewpointCard 入库后,决策模块按 `ViewpointRepository.query(symbol, ...)` 消费
- 用户新增美股标的时,系统自动拉 Alpha Vantage NEWS_SENTIMENT + OVERVIEW,生成初始 ViewpointCard 候选
- 所有 ViewpointCard 通过标准 symbol 与持仓强关联,同时通过 entity_id 支持跨市场公司级视图

---

## 2. 核心对象:ViewpointCard v2 Schema

### 2.1 三层分离原则

| 层 | 填充主体 | 用户可编辑 | 服务对象 |
|---|---|---|---|
| **事实层**(facts) | 机器(Adapter) | ❌ 只读 | 决策引擎、追溯、审计 |
| **叙事层**(narrative) | LLM 加工 + 人类可追加 | ⚠️ 可追加 annotation,不能改原文 | 人类阅读、面试/复盘 |
| **判断层**(judgment) | 人类填写(LLM 可预填) | ✅ 必须填 | 决策引擎触发逻辑 |

### 2.2 事实层字段(facts layer)

事实层有意保持"原样",不做跨 adapter 归一化——归一化由叙事层的 `extracted_kpi` 承担。

| 字段 | 类型 | 说明 |
|---|---|---|
| `card_id` | UUID | 观点卡主键 |
| `affected_symbols` | list[Symbol] | 影响的标的(标准化 symbol) |
| `primary_symbol` | Symbol? | 主标的 |
| `primary_entity_id` | str? | 主公司实体 ID(见 §4.2) |
| `source_type` | enum | `user_upload` / `alpha_vantage_news` / `alpha_vantage_fundamental` / `alpha_vantage_earnings` / `perplexity_search` / `hybrid` |
| `source_refs` | list[SourceRef] | 具体来源引用(URL / 文档 ID / API 调用 ID) |
| `as_of` | datetime | 事实对应的时点 |
| `ingested_at` | datetime | 入库时间 |
| `raw_facts` | dict | adapter 原始返回的关键字段(形状因 source_type 而异) |
| `sentiment_raw` | dict? | 情感打分原始数据(仅 news 类) |

### 2.3 叙事层字段(narrative layer)

由 LLM 生成,用户**不能改原文**,可追加 annotation。

| 字段 | 类型 | 说明 |
|---|---|---|
| `thesis` | text | 核心论点(1-2 句) |
| `bull_case` | text | 看多逻辑 |
| `bear_case` | text | 看空逻辑 |
| `narrative_summary` | text | 综合叙述 |
| `event_type` | enum | 事件类型(见下) |
| `topics` | list[str] | 主题标签(受控词表,对齐 Alpha Vantage) |
| `extracted_kpi` | dict? | LLM 从事实层抽取的关键指标(见下) |
| `user_annotations` | list[Annotation] | 用户追加备注 |

#### event_type 枚举(14 项,定稿)

定位:**不是"新闻分类",是"决策信号类型"**。

```
earnings                    # 财报
guidance_update            # 指引变化(必须单独拆,对投资判断影响极大)
analyst_rating             # 评级变化
product_launch             # 产品发布
product_review             # 产品评测/反馈
delivery_or_sales_data     # 销量/交付(车企、电商核心)
industry_competition       # 行业竞争/价格战/补贴战
macro_policy               # 宏观政策
regulatory                 # 监管
management_change          # 管理层变动
executive_interview        # 高管访谈/表态
corporate_action           # 回购/分红/并购
market_movement            # 股价/波动
other
```

#### extracted_kpi 字段(定稿)

定位:**不是"展示数据",是"决策输入"**。总量控制在 18 个主字段 + guidance 子结构,不做财务数据库。

**第一优先(必须抽取)**

```
current_price              # 生成时价格(非实时,与 as_of 对齐)
target_price               # 分析师目标价
price_change_pct           # 价格变动百分比

revenue_yoy                # 营收同比
earnings_yoy               # 盈利同比

gross_margin               # 毛利率(产品竞争力)
net_margin                 # 净利率(最终口径)

free_cash_flow             # 自由现金流

deliveries_latest          # 最新交付/销量(车企、电商)
deliveries_yoy             # 交付/销量同比

analyst_target_upside      # 目标价相对当前价的上行空间(小数)
```

**第二优先(扩展用,允许 null)**

```
market_cap                 # 市值
pe_ttm                     # 市盈率(TTM)
forward_pe                 # 远期市盈率

cash_and_equivalents       # 现金及等价物

eps_surprise_pct           # EPS 超预期百分比(财报场景)

subsidy_or_marketing_spend # 补贴/营销投入(美团、电商补贴战场景)
```

**guidance 子结构**

```
guidance: {
  revenue?: {value, period, vs_consensus}
  delivery?: {value, period, vs_consensus}
  margin?: {value, period}
}?
```

**元数据**

```
notes: str?                # LLM 说明口径、一次性因素、换算假设
extra: dict?               # 兜底扩展,不鼓励使用
```

**关键约束**:
- `current_price` 是**生成时的快照**,不是实时价。前端如需实时价,另从 GLOBAL_QUOTE 拉
- `extracted_kpi` **仅由 LLM 抽取**,标记为"派生数据";审计时回 `raw_facts` 查原始数值
- `extracted_kpi` **不要求完整**,事实层没有的字段 null
- 不同 adapter 的口径差异(GAAP vs non-GAAP 等)**不做强制统一**,由 LLM 在 `notes` 说明
- `subsidy_or_marketing_spend` 的具体口径(是补贴、是营销费用、是合并计算)由 LLM 在 `notes` 注明

#### topics(受控词表)

初版对齐 Alpha Vantage 的 topics 枚举,不允许自由标签。v2.1 根据实际积累扩展词表。

#### Annotation 结构

```
{
  author: "user" | "system",
  created_at: datetime,
  content: text,
  attached_to: "thesis" | "bull_case" | "bear_case" | "summary" | null
}
```

**`bull_case` 等文本永远不变**,用户想补充或反驳时加 annotation。

### 2.4 判断层字段(judgment layer)

由人类填写(LLM 可预填低 confidence 草稿),决策引擎消费的核心。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `user_endorsement` | enum | ✅ | `endorse` / `reference_only` / `disagree` |
| `stance` | enum | ✅ | `bullish` / `bearish` / `neutral` / `watch` |
| `horizon` | enum | ✅ | `short`(<1月) / `medium`(1-6月) / `long`(>6月) |
| `confidence` | enum | ✅ | `high` / `medium` / `low` |
| `decision_signal` | DecisionSignal | ✅ | 结构化决策信号(见下) |
| `action_type` | enum | ✅ | `consider_add` / `consider_reduce` / `consider_exit` / `hold_observe` / `no_action` |
| `trigger_conditions` | text | ⚠️ | 什么情况下触发 action_type |
| `invalidation_conditions` | text | ⚠️ | 什么情况下观点失效 |
| `key_metrics_to_watch` | list[str] | ⚠️ | 需要跟踪的指标 |
| `validity_status` | enum | ✅ | `active` / `expired` / `invalidated` |
| `expires_at` | datetime? | ⚠️ | 自动失效时间(horizon 默认计算,可覆盖) |

**`decision_signal` 子结构**

```
{
  direction: int          # -1 / 0 / +1(bearish / neutral or watch / bullish)
  strength: float         # 0.0 ~ 1.0,观点强度
  confidence_score: float # 0.0 ~ 1.0,user confidence 数值映射
                          # low=0.3, medium=0.6, high=0.9
                          # LLM 预填默认 0.3(强制用户确认才上调)
}
```

`strength` 和 `confidence_score` 正交——"强观点但不确定"(strength=0.8, confidence=0.3)与"弱观点但很确定"(strength=0.3, confidence=0.9)是两种情况,组合比单一 confidence 精确。

**生成规则**:
- `direction` 由 `stance` 直接映射:bullish=+1, bearish=-1, neutral/watch=0
- `strength` 默认 0.5,LLM 预填根据叙事估算
- `confidence_score` 由 `confidence` enum 映射,用户可在滑动条微调

### 2.5 观点间关系:Relations

独立观点卡不足以支撑决策——同一 symbol 的多张卡常**互相印证、矛盾、或前后演进**。

| 字段 | 类型 | 说明 |
|---|---|---|
| `relations` | list[Relation] | 本卡与其他卡的关系 |

```
Relation = {
  related_card_id: UUID
  relation_type: enum   # reinforces / contradicts / supersedes / follows_up
  note: text?
  created_by: "user" | "llm_auto"
  created_at: datetime
}
```

**v2 落地策略**:schema 先定,v2.0 只支持用户手动标注,LLM 自动识别留到 v2.1。决策引擎 v2.0 可忽略 relations。

### 2.6 schema 和 v1 的映射关系(用于迁移)

| v1 字段 | v2 位置 | 处理 |
|---|---|---|
| `title` | 叙事层 `thesis` 第一句 | 直接拷贝 |
| `object_name` | 事实层 `primary_symbol` + `primary_entity_id` | symbol 标准化(见 §4) |
| `market_name` | 事实层 `raw_facts.market` | 直接拷贝 |
| `topic_tags` | 叙事层 `topics`(受控词表过滤) | 不符合的丢弃 |
| `thesis` | 叙事层 `thesis` | 直接拷贝 |
| `supporting_points` | 叙事层 `bull_case`(拼接成段落) | list → text |
| `opposing_points` | 叙事层 `bear_case` | 同上,修复错位 bug |
| `key_metrics` | 判断层 `key_metrics_to_watch` | 直接拷贝 |
| `risks` | 叙事层 `bear_case` 追加 | |
| `horizon` / `stance` | 判断层 `horizon` / `stance` | 直接拷贝,stance 加回 `watch` |
| `user_approval_level` | 判断层 `user_endorsement` | strong→endorse, partial/reference→reference_only |
| `validity_status` | 判断层 `validity_status` | active→active, watch→active+expires_at, outdated→expired, invalid→invalidated |
| `action_suggestion` | 判断层 `action_type` + `trigger_conditions` | LLM 辅助拆分,low confidence |
| `invalidation_conditions` | 判断层 `invalidation_conditions` | 直接拷贝 |
| `source_card_id` / `source_document_id` | 事实层 `source_refs` | 合并为 list |

v1 不存在但 v2 必填的字段(`decision_signal` / `event_type` / `confidence`):迁移时 LLM 推测,标记 low confidence,用户在首次打开时确认。

---

## 3. 核心对象:InfoAdapter 接口

### 3.1 设计哲学:薄 Adapter + 厚 Processor

**Adapter 只负责 fetch,不做 summarize 或 extract_signals**。所有加工逻辑集中在 ViewpointProcessor。

- 不同 Adapter 各自实现加工会导致输出质量参差、风格不统一
- 加工集中 → prompt 统一管理,迭代更容易
- 新 Adapter 接入成本低(只实现 fetch)

### 3.2 抽象接口定义

```
interface InfoAdapter:
    adapter_id: str
    source_type: SourceType
    coverage: Coverage
    rate_limits: RateLimits

    fetch(symbols: list[Symbol], since: datetime?) -> list[RawFact]
    is_symbol_supported(symbol: Symbol) -> bool
```

### 3.3 RawFact 标准契约

所有 Adapter 返回的 RawFact 必须含:

```
@dataclass
class RawFact:
    source_type: str                # 必填
    source_url: str?                # 网页/新闻必填
    as_of: datetime                 # 必填
    affected_symbols: list[Symbol]  # 必填,至少一个
    payload: dict                   # adapter 特有数据,成为 facts.raw_facts
    source_refs: list[SourceRef]    # 必填
```

### 3.4 v2 落地的三个 Adapter

**UserUploadAdapter**
- 迁移自当前 `parse_text` / `parse_url` / `parse_pdf`
- coverage: 全市场

**AlphaVantageAdapter**(按能力拆三个子 adapter)
- `alpha_vantage_news`:调 `NEWS_SENTIMENT`
- `alpha_vantage_fundamental`:调 `COMPANY_OVERVIEW`
- `alpha_vantage_earnings`:调 `EARNINGS`
- coverage: 美股 + 美股 ADR(`:US` 后缀)
- rate_limits: 免费 25/天, 付费 600/天(共享)

**PerplexityAdapter**(从决策模块迁移)
- 迁移源:`decision_engine/data_loader.py::_search_research_online`
- coverage: 全市场(兜底)
- 4 维度搜索:财报 / 交付销量 / 评级 / 动态

### 3.5 Router:按 symbol 分发

| symbol 后缀 | 首选 Adapter | 备选 |
|---|---|---|
| `:US` | AlphaVantage(三子) | Perplexity |
| `:HK` | (v2 不覆盖) | Perplexity |
| `:SH` / `:SZ` | (v2 不覆盖) | Perplexity |
| 任意 | UserUpload | - |

**降级规则**:
- Alpha Vantage 限额触发 → 降级到 Perplexity,UI 明确告知
- Alpha Vantage 返回 0 条新闻 → 同时拉 Perplexity 补充
- 用户上传始终可用

### 3.6 决策模块侧的迁移

| v1 方法 | v2 替换 |
|---|---|
| `_distill_research_cards(asset_name)` | 删除。改为 `ViewpointRepository.query(symbol, since)` |
| `_search_research_online(asset_name)` | 删除。能力迁移到 PerplexityAdapter |
| `_load_research(session, pid, asset_name)` | 保留签名,内部改为调 ViewpointRepository |

决策模块不再有"投研能力",只有"投研消费"。

---

## 4. Symbol + Entity 两级标准化

### 4.1 标准 Symbol 格式

`<ticker>:<market>`:

- `LI:US` / `TSLA:US` / `NVDA:US` —— 美股
- `0700:HK` / `9988:HK` / `3690:HK` —— 港股
- `600519:SH` / `000858:SZ` —— A 股
- `TCEHY:US` / `BABA:US` —— 美股 ADR(与原股区分,数据独立维护)

### 4.2 Entity 层

```
@dataclass
class Entity:
    entity_id: str            # "li_auto" / "tencent_holdings"
    display_name_cn: str
    display_name_en: str
    symbols: list[Symbol]     # 跨市场聚合
```

**Entity 是增强层,不是强约束**:
- 不是所有 Symbol 都映射到 Entity(ETF、指数、宏观主题 → `entity_id=null`)
- 初期人工维护小映射表(10-20 家),后续 LLM 辅助扩展

**初始 Entity 锚点**(10-20 家,Claude 推断 + 用户人工确认):

```
li_auto            → [LI:US, 2015:HK]          # 双重上市
tencent_holdings   → [0700:HK, TCEHY:US]
alibaba_group      → [9988:HK, BABA:US]
meituan            → [3690:HK, MPNGY:US]
nvidia             → [NVDA:US]
tesla              → [TSLA:US]
apple              → [AAPL:US]
meta_platforms     → [META:US]
coinbase           → [COIN:US]
# ETF/指数:entity_id=null
QQQ:US             → null  (object_type=etf)
```

**object_type 扩充**:v1 已有 `asset` / `sector` / `market` / `macro` / `strategy`,v2 增加 `etf`。

### 4.3 跨市场查询行为(LI 双重上市案例)

Alpha Vantage 的 NEWS_SENTIMENT 返回的 `ticker_sentiment` 只给美股代码(如 `LI`),不会给 `2015.HK`。这产生一个关键设计问题:用户持仓如果是 `2015:HK`,Alpha Vantage 产生的观点卡 `affected_symbols=[LI:US]` —— 会匹配不上。

**v2 采用"查询时按 Entity 扩展"**:
- ViewpointCard 的 `affected_symbols` **保持来源原样**(来自 Alpha Vantage 就是 `[LI:US]`,不自动扩展)
- `ViewpointRepository.query(symbol=2015:HK)` 调用时:
  - 先查 Entity 表:`2015:HK → entity_id=li_auto → symbols=[LI:US, 2015:HK]`
  - 然后用这个扩展后的 symbol 列表查 `affected_symbols` 有交集的观点卡
- 默认行为:按 Entity 聚合,跨市场打通
- 支持参数 `entity_scope=false`:只查 `affected_symbols` 严格包含查询 symbol 的卡,不做扩展

这样的好处:
- 观点卡内容稳定,不随 Entity 表变化而改
- 跨市场聚合只发生在查询时,可开关
- 新增 Symbol 到 Entity 表,历史观点卡自动覆盖到,不需要回填

### 4.4 迁移方式

所有 v1 的 `object_name` 需要映射:

1. **自动映射**:维护映射表(`理想汽车 → LI:US, entity=li_auto`)
2. **LLM 辅助**:无法自动映射的,LLM 根据 thesis 和 market_name 推测,生成待确认清单
3. **人工确认**:用户在迁移页面逐条确认。非标的观点映射为 `object_type="sector"/"macro"`,`primary_symbol=null`,`primary_entity_id=null`

### 4.5 持仓侧对齐

持仓表的 `asset_name` 同时标准化到 Symbol + Entity。这是 v2 配套改动,工程 PRD 阶段单独出一份"symbol+entity 标准化迁移专项 PRD"。

---

## 5. 核心业务流程

### 5.1 流程 A:用户上传研报

```
用户粘贴研报 / URL / PDF
  → UserUploadAdapter.fetch → RawFact
  → ViewpointProcessor 生成三层:
      事实层:source_type=user_upload, raw_facts={...}, affected_symbols=[LLM 识别]
      叙事层:thesis / bull_case / bear_case / event_type / topics / extracted_kpi
      判断层:LLM 低 confidence 预填
  → 存为候选卡(status=pending_review)
  → 用户审核(判断层必须 confirm 或修改,叙事层只能追加,事实层只读)
  → 判断层必填字段补全后 → status=active
```

### 5.2 流程 B:Alpha Vantage 自动拉取(v2 新增)

触发时机:
- 用户在持仓新增 `:US` 标的 → 自动拉一次
- 用户在投研模块点"刷新"
- 定时调度(v2.0 不做,v2.1 评估)

```
trigger(symbol=LI:US)
  → InfoRouter 选择 AlphaVantageAdapter(三子 adapter 并行)
  → 每个 RawFact → ViewpointProcessor → 一张 ViewpointCard
  → 去重(source_refs.url + as_of)
  → pending_review 状态
```

**关键点**:Alpha Vantage 拉来的数据**也要用户审核**才成为正式观点。审核成本比 v1 低:事实层和叙事层机器生成,用户只需过判断层。

### 5.3 流程 C:观点库检索(决策侧消费)

```
决策模块调 ViewpointRepository.query(
    symbol=LI:US,                    # 会按 Entity 扩展,除非 entity_scope=false
    since=30 天前,
    validity=[active],
    min_endorsement=reference_only,
    min_confidence_score=0.5,
    top_k=10
)
  → 返回 ViewpointCard 列表
  → 决策模块直接消费(extracted_kpi + decision_signal + action_type + invalidation_conditions)
  → 组装 DecisionContext.research
```

### 5.4 流程 D:前端 Tab 3"决策检索"(v2 修正)

v2 里这个 Tab **真的反映决策链路看到什么**:调用决策模块相同的 ViewpointRepository 查询接口,按相同过滤规则呈现。

v1 的 `retrieve_research_context` 关键词匹配保留,作为 Tab 2"观点库"的"人找观点"检索。

---

## 6. 业务规则

### 6.1 候选卡 → 正式观点

**入库前校验**:判断层必填字段必须全部填写;⚠️字段不填显示警示但不阻塞;事实层、叙事层由机器保证完整性。

**审核动作**(3 个按钮,v1 的"修改后录入"取消):
- `认可并入库` → status=active, user_endorsement=endorse
- `仅保留为参考` → status=active, user_endorsement=reference_only
- `丢弃` → 删除 ViewpointCard,保留 ResearchDocument(历史追溯)

### 6.2 批量审核

- 批量勾选 → 批量认可:只允许统一设置判断层字段(stance / confidence / endorsement)
- 单条编辑:仍支持精细化
- 混合:批量操作后可再单条修改
- 约束:批量认可必须至少设置 stance + confidence + endorsement 三项

### 6.3 有效性生命周期

- `active`:有效
- `expired`:过 `expires_at`,系统自动标记,可手动恢复
- `invalidated`:触发 `invalidation_conditions`,用户手动标记

`expires_at` 默认:short=30天, medium=180天, long=365天,用户可覆盖。

### 6.4 决策上下文注入规则

默认过滤:
- `validity_status = active`
- `user_endorsement ∈ [endorse, reference_only]`(disagree 排除)
- `confidence_score >= 0.5`(排除未确认的 LLM 低置信草稿)

排序与聚合:
- 按 `as_of desc` 排序
- 默认 top 10
- 同一 symbol 多张卡聚合:`portfolio_signal = Σ(direction_i × strength_i × confidence_score_i) / count`
- 聚合值与单张卡并存输入决策引擎

决策引擎 prompt 按 `event_type` 分组呈现(具体格式在决策模块 PRD)。

### 6.5 LLM 预填与强制确认

Alpha Vantage 和 UserUpload 产生的卡,判断层由 LLM 预填:
- `confidence` 默认 `low`(confidence_score=0.3)
- UI 标记"LLM 预填,请确认"
- 用户**必须点击 confirm**(即便不改),confidence 才上调到 medium(0.6)
- 未 confirm 的卡 `confidence_score < 0.5`,被决策上下文过滤

### 6.6 去重规则

**入库时**:按 `source_refs.url + as_of` 联合判重;用户上传按 `raw_content_hash` 判重。

**检索时**:同一 `primary_symbol` + 相同 `event_type` 在 7 天内只保留 confidence 最高或 as_of 最新的一张。

### 6.7 Adapter 错误处理

- Alpha Vantage 限额:UI 明确告知"今日额度用尽,降级 Perplexity"
- Alpha Vantage 返回 0 条:提示"最近无新闻",不生成空卡
- Perplexity 失败:记录日志,返回空列表,不阻塞主流程

---

## 7. 分期交付:Phase 切分

### 7.1 v2.0 — 核心架构升级

**范围**:
- 三层 schema 数据库迁移
- Symbol + Entity 标准化(持仓表和观点表同步)
- UserUploadAdapter 重构到新 schema
- AlphaVantageAdapter 接入(三子)
- ViewpointProcessor 实现(统一加工层)
- ViewpointRepository 实现
- 决策模块迁移:删除 `_distill_research_cards` / `_load_research`,改调 ViewpointRepository
- 前端:候选卡审核页适配新 schema(事实/叙事/判断三区)
- 前端:Tab 3 与决策模块共用查询接口

**v2.0 不做**:
- Perplexity 迁移(决策模块侧暂保留 `_search_research_online`)
- 关系自动识别
- 定时调度

#### v2.0 稳定验收标准(硬性)

四条全部达标才算"稳定",进入 v2.1:

1. ✅ 全部持仓(10-20 只)都能通过 Alpha Vantage 或 UserUpload 产生至少 3 张可用 ViewpointCard
2. ✅ 决策模块的 18 个测试用例全部通过,每个用例 DecisionContext.research 字段非空
3. ✅ 前端 Tab 3 决策检索返回的结果,和决策模块实际消费的结果,手动抽样 5 个 case 一致
4. ✅ Symbol + Entity 标准化迁移完成,v1 数据全部映射或标记 legacy

### 7.2 v2.1 — 联网搜索定位决策(2026-04-27 更新)

**v2.1 关于联网搜索的决策**:

经过 v2.0 真实使用验证,决定**不做 Perplexity 迁移、不做 OpenAISearchAdapter**。

理由:
- 联网搜索作为决策模块的兜底 fallback 已能满足真实使用
- 联网内容入库会污染 v2 库定义("用户认可的观点")
- 简单架构便于长期维护
- pending 卡堆积是负担不是资产,自动入库会产生大量需要审核的低价值卡

v2.1 实际改动:
- `_search_research_online` 文档化为"永久 fallback",不再标记"待迁移"
- 全持仓真实 API 拉取验证 + fixture 补齐

**v2.1+ backlog(视需求启动)**:
- 关系自动识别(LLM 判断 reinforces / contradicts / supersedes)
- 批量审核 UI 优化

### 7.3 v2.2 — 高级能力(可选)

- 定时调度(根据使用后体感决定频次)
- 关系用于决策加权
- decision_signal 用于量化回测
- Entity 级别跨市场视图
- topics 词表扩展 + 检索优化

### 7.4 v3(不在本 PRD 范围)

- WealthPilot 自身暴露为 MCP Server
- 向量检索 / RAG 升级
- HK/A 股专项 adapter

---

## 8. 范围边界(v2 明确不做)

| 边界 | 原因 |
|---|---|
| 不做向量检索 | v2 焦点是 schema 和路径,RAG 放 v3 |
| 不做 HK/A 股专属 Adapter | v2 港股/A股走 Perplexity 兜底 |
| 不做 WealthPilot MCP Server | v3 的事 |
| 不做大规模定时调度 | v2.0 做触发式,定时调度 v2.2 |
| 不做 Tab 结构重构 | Research.tsx 拆分是工程优化 |
| 不做 app_pages/research.py 下线 | Streamlit 版历史遗留 |

---

## 9. 关键决策汇总(终稿)

1. **三层 schema**:facts / narrative / judgment,事实不可编辑,叙事只能追加,判断必须人填
2. **决策信号数值化**:`decision_signal {direction, strength, confidence_score}`
3. **event_type 14 项**:决策信号型分类,`guidance_update` 单独拆,`market_movement` 替代 commentary
4. **extracted_kpi 18 主字段 + guidance 子结构**:决策输入,不做财务数据库
5. **归一化折中**:事实层不归一化,叙事层 `extracted_kpi`(LLM 派生,可追溯)
6. **受控 topics**:对齐 Alpha Vantage,不加自由 tags
7. **relations**:schema 先定,v2.0 手动,v2.1 加自动
8. **Entity 层**:增强层,允许 null,跨市场聚合在查询时发生(方案 A)
9. **薄 Adapter**:只 fetch,加工统一由 Processor 做
10. **LLM 预填 + 强制确认**:默认 low confidence,用户必须 confirm
11. **批量审核**:支持,批量只改判断层,至少 3 个最小字段
12. **v1 数据迁移**:混合策略,能映射迁,不能映射标 legacy 或丢弃
13. **UI**:Tab1 内增加"自动拉取"区块,不加新 Tab
14. **Perplexity 迁移硬时间表**:v2.0 + 3/7/10 天节奏
15. **v2.0 稳定验收 4 条**:全部达标才进 v2.1

---

## 10. 迭代记录

### draft-1 → draft-2(GPT 交叉评审第一轮)
- 新增 `decision_signal`、`event_type`、`topics`、`extracted_kpi`、`relations`、`Entity` 层
- 新增 LLM 预填强制确认、批量审核
- 新增 Phase 切分
- 拒绝:能力型 Adapter、事实层 normalized_facts
- 有条件调整:Perplexity 迁移时机(v2.0 不迁,v2.1 迁)

### draft-2 → final(GPT 交叉评审第二轮 + 用户拍板)
- event_type:最终 14 项(GPT 提议 `guidance_update` 单独拆、`market_movement` 替代 commentary)
- extracted_kpi:精简到 18 主字段 + guidance 子结构,砍掉 operating_margin / revenue / net_income / eps_ttm / analyst_target_price 等展示型字段;`current_price` 语义澄清为"生成时快照"
- Entity 初始锚点列表明确
- LI 双重上市 → 采用方案 A(查询时按 Entity 扩展,affected_symbols 保持来源原样)
- v2.0 → v2.1 改为硬约束时间表(T+3 / T+7 / T+10)
- 新增 v2.0 稳定验收 4 条硬标准

---

## 附录:关键术语表

- **Adapter**:信息源适配器,只 fetch
- **ViewpointProcessor**:观点加工器,统一把 RawFact 转三层 ViewpointCard
- **ViewpointRepository**:观点仓库,CRUD + 查询(默认按 Entity 扩展)
- **InfoRouter**:按 symbol 决定调哪个 Adapter
- **RawFact**:单条事实
- **ViewpointCard**:三层结构的观点卡,决策引擎消费对象
- **Symbol**:`<ticker>:<market>` 标准化标识
- **Entity**:公司实体,多 Symbol 聚合(增强层,允许 null)
- **DecisionSignal**:判断层结构化信号 {direction, strength, confidence_score}
- **extracted_kpi**:叙事层 LLM 派生关键指标,决策引擎消费
- **event_type**:叙事层事件分类(14 项,决策信号型)
