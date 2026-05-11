# 投研观点模块 v2 · 工程 PRD

> **版本**:v2-engineering-draft-1
> **日期**:2026-04-24
> **状态**:工程 PRD 初稿,用于交付给 Claude Code 执行
> **前置文档**:
> - 业务 PRD:`investment_research_module_v2_business_prd_final.md`
> - 现状梳理 1:`docs/investment_research_module_current_state_v1.md`
> - 现状梳理 2:`docs/decision_module_research_consumption_v1.md`

---

## 0. 本文档使用说明

### 0.1 文档性质

本文档是**工程实施蓝图**,不是业务讨论文档。阅读顺序:

1. 先读 §1(关键决策速查)—— 所有与业务 PRD 不同 / 需要明确的工程决策
2. 再读 §11(风险清单)—— 按爆炸概率排序的风险点,Claude Code 实现前必须先了解
3. 再按 §2–§10 按 Phase 执行

### 0.2 与 Claude Code 的分工

**Claude Code 负责**:
- 按 §3–§8 的任务清单,逐条实现
- 每个任务完成后,按 §9 的验证清单自测

**你(产品侧)负责**:
- §4 的 Entity 初始映射表人工确认
- §9 的验收 checkpoint 审查
- 每个 Phase 结束的稳定性判断

**绝对不让 Claude Code 自作主张的事项**见 §11.0。

---

## 1. 关键工程决策速查(read first)

以下是业务 PRD 没覆盖、或需要工程级精确化的决策。**每条都直接影响代码**。

### 1.1 `search_portfolio_research` 保留,不迁移

- 它产出的是**宏观背景(context)**,不是**标的观点(opinion)**
- 不进入 ViewpointCard schema,不属于投研模块
- v2.1 的"删除决策模块联网搜索"**仅指 `_search_research_online`**,`search_portfolio_research` 保留
- 保留位置:`decision_engine/data_loader.py`(或迁到一个新文件 `decision_engine/macro_context.py`,见 §6.3)

### 1.2 新增 ViewpointRenderer 层

职责:`ViewpointCard → list[str]`(带 `[用户资料]` / `[联网参考]` / `[ref:url]` 前缀)

**渲染规则**:

```python
# 伪代码,具体在 §5.3
def render_for_decision_prompt(card: ViewpointCard) -> str:
    # 前缀
    if card.facts.source_type == "user_upload":
        prefix = "[用户资料]"
    elif card.facts.source_type in ALL_ONLINE_SOURCES:
        prefix = "[联网参考]"
    else:
        prefix = "[联网参考]"  # 兜底
    
    # URL 标注
    url = _pick_primary_url(card.facts.source_refs)
    if url:
        prefix += f"[ref:{url}]"
    
    # 正文(必须是"句子级",不能是段落)
    body = _compose_sentence(card)
    
    return f"{prefix} {body}"
```

**关键约束**:
- 输出必须是**单行句子**(`" ".join(...)` 的消费方要求)
- 不换行、不含 markdown 标题、不含多段落
- 长度控制在 80-200 字(和现有 `_distill_research_cards` 产出一致)

### 1.3 ViewpointRepository 的双形态输出

```python
class ViewpointRepository:
    # 形态 1:结构化(给前端、给 Tab 3 决策检索、给批量审核)
    def query_cards(self, ...) -> list[ViewpointCard]: ...
    
    # 形态 2:渲染后的字符串(给决策引擎,通过 ViewpointRenderer)
    def query_for_decision(self, ...) -> list[str]: ...
```

**决策引擎只用 `query_for_decision`**,不直接拿 ViewpointCard 对象——这样决策引擎对 ViewpointCard schema 变更天然解耦。

### 1.4 缓存层级布局

| 层 | 缓存? | TTL | 备注 |
|---|---|---|---|
| Adapter 层(Alpha Vantage) | ❌ | - | API 本身有限额,不做额外缓存,每次 fetch 都命中 API |
| Adapter 层(Perplexity) | ✅ | 4h | 从 `_RESEARCH_CACHE` 搬过来,key 按 symbol |
| ViewpointProcessor 层 | ❌ | - | 一次性加工,无需缓存 |
| ViewpointRepository 层 | ❌ | - | DB 即持久化,不在上层再缓存 |
| ViewpointRenderer 层 | ❌ | - | 纯函数,不需要缓存 |

**删除**:`_CARD_DISTILL_CACHE`(因为 `_distill_research_cards` 整个删除)。

**保留**:`_RESEARCH_CACHE` 在 v2.0 暂时保留(因为 `_search_research_online` v2.0 还在用),v2.1 迁到 PerplexityAdapter 内部。

### 1.5 signal_engine 不改

`signal_engine.py:_compute_fundamental_signal()` 的前缀污染问题已知,**v2.0 / v2.1 / v2.2 均不修**,记入 v3 backlog。

### 1.6 `_DEFAULT_MOCK_RESEARCH` 保留

AssetAllocation 意图依赖 mock 文本,这是 prompt 兼容契约。v2 保留 mock,但**内容可以更新**——改成"暂无该标的/场景的投研观点,请基于用户偏好和市场数据直接分析"之类的兜底引导语(语义不变,文本可优化)。

### 1.7 Entity 初始映射表的交付方式

- 在 `data/` 下新增 `entity_registry.yaml`(不是 json,便于人工 review 和加注释)
- 初始范围:10-20 家,参照业务 PRD §4.2 列表
- 交付流程:Claude Code 根据持仓生成草稿 → 用户人工确认 → 入库用于迁移
- Claude Code **不能自作主张地扩充这个表**——必须用户确认后才 commit

### 1.8 数据库迁移策略:新旧表并存,不 in-place 改 v1 表

**关键决策**:不改 v1 的三张表结构,v2 新增三张表。迁移脚本从 v1 读、写入 v2。v1 表保留一段时间(至少 v2.1 完成后)才删。

| v1 表 | v2 新表 | 关系 |
|---|---|---|
| `research_documents` | `research_documents_v2` | 迁移 + 补充字段 |
| `research_cards` | *(废弃,不直接对应)* | Card 被三层 schema 取代,迁移到 `viewpoint_cards_v2` 的 pending 状态 |
| `research_viewpoints` | `viewpoint_cards_v2` | 迁移 + schema 重构 |
| *(新增)* | `entities_v2` | Entity 表 |
| *(新增)* | `symbols_v2` | Symbol 字典表(可选,看工程便利性) |

**不在 v1 表上 ALTER**的理由:
- 失败可回滚(直接切回 v1 读路径)
- 迁移过程中新旧数据共存可验证
- 避免 SQLite 的 ALTER 限制

### 1.9 Alembic 迁移采用不采用?

**不采用**。理由:
- v1 现状:`create_all(checkfirst=True)`,没有 Alembic 历史
- 一次性迁移脚本(Python 直写)比引入 Alembic 成本更低
- 迁移完成后的日常演进再考虑是否引入 Alembic

**v2 的迁移方式**:手写 `migrations/v2_migrate.py`,一次性执行,脚本幂等(可重复运行不出错)。

### 1.10 版本化字段 `schema_version`

所有 v2 新表增加 `schema_version` 字段(INTEGER,默认 2),为未来再升级留口子。不强求立刻利用,但留好。

---

## 2. 总体架构图与目录布局

### 2.1 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│                     前端 Research.tsx                        │
│         Tab1 (上传+审核)  Tab2 (观点库)  Tab3 (决策检索)      │
└─────────────────┬───────────────────────────────────────────┘
                  │ REST
┌─────────────────▼───────────────────────────────────────────┐
│                    backend/api/research.py                   │
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│              backend/services/research_service.py            │
│                   (编排层,调 Repository 和 Processor)         │
└─────────┬──────────────┬──────────────┬─────────────────────┘
          │              │              │
┌─────────▼────┐  ┌──────▼───────┐  ┌──▼─────────────────────┐
│   Adapter    │  │  Processor   │  │   Repository           │
│   Router     │  │  (LLM加工)    │  │   (CRUD + 查询)         │
└─────┬────────┘  └──────┬───────┘  └──┬─────────────────────┘
      │                  │              │
┌─────▼────────────┐     │           ┌──▼─────────────┐
│ UserUploadAdapter│     │           │ Renderer       │
│ AlphaVantage*3   │     │           │ (→list[str])   │
│ PerplexityAdapter│     │           └──┬─────────────┘
└──────────────────┘     │              │
                         │       ┌──────▼──────────┐
                         │       │ 决策模块消费      │
                         │       │ (llm_engine.py) │
                         ▼       └─────────────────┘
                    ┌─────────┐
                    │ LLM(gpt)│
                    └─────────┘
```

### 2.2 目录布局

```
wealthpilot_backend/
├── app/
│   ├── models.py                      # v1 模型保留,新增 v2 模型
│   ├── models_v2.py                   # 新增:ViewpointCardV2 / EntityV2 等
│   ├── research.py                    # v1 retrieve_research_context 保留
│   └── ...
├── research_v2/                        # 新增模块目录
│   ├── __init__.py
│   ├── schemas.py                     # ViewpointCard 的 Pydantic 模型
│   ├── symbol.py                      # Symbol / Entity 类
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                    # InfoAdapter 抽象基类
│   │   ├── user_upload.py
│   │   ├── alpha_vantage_news.py
│   │   ├── alpha_vantage_fundamental.py
│   │   ├── alpha_vantage_earnings.py
│   │   └── perplexity.py              # v2.1 才实现,v2.0 占位
│   ├── router.py                      # InfoRouter
│   ├── processor.py                   # ViewpointProcessor
│   ├── repository.py                  # ViewpointRepository
│   ├── renderer.py                    # ViewpointRenderer(§1.2)
│   └── prompts/
│       ├── processor_user_upload.txt
│       ├── processor_alpha_vantage_news.txt
│       ├── processor_alpha_vantage_fundamental.txt
│       ├── processor_alpha_vantage_earnings.txt
│       └── processor_perplexity.txt
├── backend/
│   ├── api/research.py                # 扩展:新增 v2 endpoints
│   ├── services/research_service.py   # 扩展:调用 research_v2
│   └── ...
├── decision_engine/
│   ├── data_loader.py                 # 改造:_load_research 走 Repository
│   ├── macro_context.py               # 新增:从 data_loader 搬 search_portfolio_research
│   └── llm_engine.py                  # 不改(prompt 合约保持)
├── migrations/
│   ├── v2_migrate.py                  # 一次性迁移脚本
│   └── data/
│       └── entity_registry.yaml       # Entity 初始表
└── tests/
    └── research_v2/
        ├── test_adapters.py
        ├── test_processor.py
        ├── test_repository.py
        ├── test_renderer.py
        └── test_migration.py
```

**命名原则**:新代码放 `research_v2/`,不污染 v1 文件。v1 代码只在迁移完成后删除。

---

## 3. 数据库设计

### 3.1 `viewpoint_cards_v2` 表

```sql
CREATE TABLE viewpoint_cards_v2 (
    -- 主键与元数据
    card_id TEXT PRIMARY KEY,                   -- UUID
    schema_version INTEGER NOT NULL DEFAULT 2,
    ingested_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    status TEXT NOT NULL,                       -- pending_review / active / reference_only / discarded
    
    -- ===== 事实层 =====
    primary_symbol TEXT,                        -- "LI:US"
    primary_entity_id TEXT,                     -- "li_auto",FK entities_v2.entity_id
    affected_symbols TEXT NOT NULL,             -- JSON list[str],至少一个
    source_type TEXT NOT NULL,                  -- enum 见业务 PRD
    source_refs TEXT NOT NULL,                  -- JSON list[SourceRef]
    as_of DATETIME NOT NULL,
    raw_facts TEXT NOT NULL,                    -- JSON dict
    sentiment_raw TEXT,                         -- JSON dict or null
    
    -- ===== 叙事层 =====
    thesis TEXT,
    bull_case TEXT,
    bear_case TEXT,
    narrative_summary TEXT,
    event_type TEXT NOT NULL,                   -- enum 14 项
    topics TEXT,                                -- JSON list[str]
    extracted_kpi TEXT,                         -- JSON dict or null
    user_annotations TEXT,                      -- JSON list[Annotation]
    
    -- ===== 判断层 =====
    user_endorsement TEXT,                      -- endorse / reference_only / disagree
    stance TEXT,                                -- bullish / bearish / neutral / watch
    horizon TEXT,                               -- short / medium / long
    confidence TEXT,                            -- high / medium / low
    confidence_score REAL,                      -- 0.0-1.0
    decision_direction INTEGER,                 -- -1 / 0 / +1
    decision_strength REAL,                     -- 0.0-1.0
    action_type TEXT,                           -- consider_add / ... / no_action
    trigger_conditions TEXT,
    invalidation_conditions TEXT,
    key_metrics_to_watch TEXT,                  -- JSON list[str]
    validity_status TEXT NOT NULL,              -- active / expired / invalidated
    expires_at DATETIME,
    
    -- ===== 关系(v2 留字段) =====
    relations TEXT,                             -- JSON list[Relation]
    
    -- ===== 迁移元信息 =====
    legacy_source TEXT,                         -- v1 时标记来源 "v1:card/123" 或 "v1:viewpoint/45"
    
    FOREIGN KEY (primary_entity_id) REFERENCES entities_v2(entity_id)
);

CREATE INDEX idx_vpc_primary_symbol ON viewpoint_cards_v2(primary_symbol);
CREATE INDEX idx_vpc_primary_entity ON viewpoint_cards_v2(primary_entity_id);
CREATE INDEX idx_vpc_status ON viewpoint_cards_v2(status);
CREATE INDEX idx_vpc_as_of ON viewpoint_cards_v2(as_of DESC);
CREATE INDEX idx_vpc_event_type ON viewpoint_cards_v2(event_type);
```

**字段展开决策**:`decision_signal` 子结构在 DB 层展开为 `decision_direction` / `decision_strength` / `confidence_score` 三个独立字段(便于 WHERE/ORDER BY),在 Python 层重新组合成 `DecisionSignal` 对象。

**`affected_symbols` JSON list**:SQLite 不支持数组,用 JSON 字符串。查询时用 `LIKE '%"LI:US"%'` 或 JSON 函数——**这是性能热点**,见 §11 风险 R3。

### 3.2 `research_documents_v2` 表

大部分继承 v1 `research_documents`,补充:

```sql
CREATE TABLE research_documents_v2 (
    -- 继承 v1 所有字段
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'text',
    source_url TEXT,
    raw_content TEXT,
    uploaded_at DATETIME NOT NULL,
    publish_time TEXT,
    author TEXT,
    market_name TEXT,
    tags TEXT,
    parse_status TEXT DEFAULT 'pending',
    notes TEXT,
    
    -- v2 新增
    schema_version INTEGER NOT NULL DEFAULT 2,
    raw_content_hash TEXT,                      -- SHA256 去重用
    parsed_primary_symbol TEXT,                 -- Parser 识别出的主 symbol
    parsed_affected_symbols TEXT,               -- JSON list
    
    -- 替代 v1 的 object_name(改为 symbol)
    legacy_object_name TEXT                     -- v1 数据迁移时保留原文,v2 查 parsed_primary_symbol
);

CREATE INDEX idx_doc_v2_hash ON research_documents_v2(raw_content_hash);
```

### 3.3 `entities_v2` 表

```sql
CREATE TABLE entities_v2 (
    entity_id TEXT PRIMARY KEY,                 -- "li_auto"
    display_name_cn TEXT NOT NULL,
    display_name_en TEXT NOT NULL,
    symbols TEXT NOT NULL,                      -- JSON list[str] ["LI:US", "2015:HK"]
    notes TEXT,
    created_at DATETIME NOT NULL
);

CREATE INDEX idx_entity_symbols ON entities_v2(symbols);
```

**初始数据**由 `entity_registry.yaml` 导入。

### 3.4 `positions` 表的改造(持仓侧)

现有持仓表字段需要增加:

```sql
ALTER TABLE positions ADD COLUMN symbol_v2 TEXT;          -- "LI:US"
ALTER TABLE positions ADD COLUMN entity_id_v2 TEXT;       -- "li_auto" or null
```

**不删 `asset_name`**,保留做人类阅读。`symbol_v2` 作为新的关联主键。

### 3.5 索引策略

上面已经列了核心索引。**不要**在 `affected_symbols`(JSON)上建索引——SQLite JSON 索引支持弱,v2.0 不做,查询层用 LIKE 兜底,性能够用。v3 上 Postgres 时再优化。

---

## 4. Phase 切分与任务清单

### 4.1 Phase 总览

| Phase | 内容 | 预计工作量 |
|---|---|---|
| **P0** | 基础设施搭建(schema 定义 + Entity 表 + 目录结构) | 1 天 |
| **P1** | Adapter + Router + Processor(骨架能跑通) | 2-3 天 |
| **P2** | Repository + Renderer + API 层 | 2 天 |
| **P3** | 前端改造(审核页 + Tab 3) | 2 天 |
| **P4** | 数据迁移(v1 → v2) | 1-2 天 |
| **P5** | 决策模块改造(_load_research 改造) | 1-2 天 |
| **P6** | v2.0 稳定验收 | 1 天 |
| **P7(v2.1)** | Perplexity 迁移 + 旧路径删除 | 3-5 天 |

每个 Phase 的具体任务清单见下文。

### 4.2 P0 任务清单:基础设施

| # | 任务 | 文件 | Claude Code 完成判据 |
|---|---|---|---|
| P0-1 | 创建目录结构 | `research_v2/` 全部子目录 | 目录存在,`__init__.py` 可 import |
| P0-2 | 定义 Symbol 类 | `research_v2/symbol.py` | 支持 parse/canonical/相等性,单元测试通过 |
| P0-3 | 定义 Entity 类 | `research_v2/symbol.py` | `Entity.contains(symbol)` 可用 |
| P0-4 | 定义 ViewpointCard Pydantic 模型 | `research_v2/schemas.py` | 可序列化/反序列化,业务 PRD §2 全部字段 |
| P0-5 | 定义 ORM 模型 | `app/models_v2.py` | `viewpoint_cards_v2` / `research_documents_v2` / `entities_v2` 三表 |
| P0-6 | 生成 entity_registry.yaml 草稿 | `migrations/data/entity_registry.yaml` | 按业务 PRD §4.2 列出 10-20 家,**不 commit 到 DB**,等用户确认 |
| P0-7 | 数据库迁移脚本骨架 | `migrations/v2_migrate.py` | 脚本可运行但不执行实际迁移(只建表) |

**P0 验收**:`pytest tests/research_v2/test_schemas.py` 全绿,`entity_registry.yaml` 草稿等用户 review。

### 4.3 P1 任务清单:Adapter + Router + Processor

| # | 任务 | 文件 | 关键细节 |
|---|---|---|---|
| P1-1 | `InfoAdapter` 抽象基类 | `research_v2/adapters/base.py` | 参照业务 PRD §3.2,加 `RawFact` dataclass |
| P1-2 | `UserUploadAdapter` | `research_v2/adapters/user_upload.py` | 包装现有 `parse_text`/`parse_url`/`parse_pdf` |
| P1-3 | `AlphaVantageNewsAdapter` | `research_v2/adapters/alpha_vantage_news.py` | MCP 调用;注意限额处理 |
| P1-4 | `AlphaVantageFundamentalAdapter` | `research_v2/adapters/alpha_vantage_fundamental.py` | 调 COMPANY_OVERVIEW |
| P1-5 | `AlphaVantageEarningsAdapter` | `research_v2/adapters/alpha_vantage_earnings.py` | 调 EARNINGS |
| P1-6 | `PerplexityAdapter` **占位** | `research_v2/adapters/perplexity.py` | v2.0 留空实现,v2.1 再实现 |
| P1-7 | `InfoRouter` | `research_v2/router.py` | 按 symbol 市场后缀分发,含降级逻辑 |
| P1-8 | `ViewpointProcessor` | `research_v2/processor.py` | 按 source_type 分派不同 prompt |
| P1-9 | Processor prompt 文件 | `research_v2/prompts/*.txt` | 4 个 prompt 文件,每个对应一种 source_type |
| P1-10 | 单元测试 | `tests/research_v2/test_adapters.py`, `test_processor.py` | 用 mock 数据测试,不真调 API |
| P1-11 | 联调测试脚本 | `scripts/test_p1_e2e.py` | 真调 Alpha Vantage 一次,产出一张 ViewpointCard 打印出来 |

**P1 验收**:用 `LI:US` 调用整条链路,产出结构正确的 ViewpointCard(三层都填),人工审查 1 份结果觉得"可用"。

#### P1 关键实现约束

**Adapter 统一限额处理**:

```python
# adapters/base.py 加一个
class AdapterQuotaError(Exception):
    """Adapter 限额触发,应降级"""
    pass

# Router 捕获 AdapterQuotaError 后降级
```

**Processor 的 prompt 必须满足**:

- 每个 source_type 一个独立 prompt 文件,便于迭代
- prompt 内部分"事实层 → 叙事层 → 判断层预填"三段
- 判断层预填时 **`confidence` 固定输出 `low`**,不允许 LLM 自己说 high/medium(强制确认机制依赖这个)
- 输出强制 JSON,失败时 Processor 重试一次,再失败则抛异常

### 4.4 P2 任务清单:Repository + Renderer + API

| # | 任务 | 文件 | 关键细节 |
|---|---|---|---|
| P2-1 | `ViewpointRepository` 类 | `research_v2/repository.py` | 双形态输出(§1.3) |
| P2-2 | Repository 查询方法 | `research_v2/repository.py` | `query_cards(symbol, since, validity, ...)` |
| P2-3 | Repository 写入方法 | `research_v2/repository.py` | `insert(card)` / `update_judgment(card_id, ...)` / `bulk_approve([ids])` |
| P2-4 | Entity 扩展查询 | `research_v2/repository.py` | 按 Entity 扩展 symbols,见业务 PRD §4.3 方案 A |
| P2-5 | `ViewpointRenderer` | `research_v2/renderer.py` | 见 §1.2 规则 |
| P2-6 | Renderer 单元测试 | `tests/research_v2/test_renderer.py` | 覆盖所有 source_type 的渲染 |
| P2-7 | REST API 扩展 | `backend/api/research.py` | 新增 v2 endpoints,见 §5 |
| P2-8 | Service 层编排 | `backend/services/research_service.py` | 新增 v2 方法,不删除 v1 方法 |

**P2 验收**:可以通过 API 上传 → Processor 加工 → 写入 DB → 查询回来 → Renderer 输出带前缀字符串。

### 4.5 P3 任务清单:前端

| # | 任务 | 文件 | 关键细节 |
|---|---|---|---|
| P3-1 | 审核页适配三层 schema | `frontend/src/pages/Research.tsx` | 事实/叙事/判断三区,权限严格 |
| P3-2 | 强制确认交互 | `frontend/src/pages/Research.tsx` | LLM 预填字段必须 confirm 才提高 confidence |
| P3-3 | 批量审核 UI | `frontend/src/pages/Research.tsx` | 多选 + 批量 approve(限判断层 3 字段) |
| P3-4 | Tab 1 自动拉取区块 | `frontend/src/pages/Research.tsx` | 添加持仓时触发 / 手动刷新按钮 |
| P3-5 | Tab 3 决策检索修正 | `frontend/src/pages/Research.tsx` | 调用 `/v2/research/decision_query`,返回决策引擎会看到的列表 |
| P3-6 | 前端类型定义 | `frontend/src/lib/api.ts` | 新增 ViewpointCardV2 等类型 |

**P3 验收**:用户能在 UI 里完整走完"粘贴 → 审核 → 入库"流程,UI 上能看到事实/叙事/判断三区,判断层编辑能保存回后端。

### 4.6 P4 任务清单:数据迁移

| # | 任务 | 文件 | 关键细节 |
|---|---|---|---|
| P4-1 | Entity 表写入 | `migrations/v2_migrate.py` | 读 `entity_registry.yaml` 写入 `entities_v2` |
| P4-2 | 持仓表 symbol 标准化 | `migrations/v2_migrate.py` | `positions.asset_name` → `symbol_v2`(LLM 辅助 + 人工确认清单) |
| P4-3 | `research_documents` 迁移 | `migrations/v2_migrate.py` | 加 hash、加 parsed_symbol(LLM 推断) |
| P4-4 | `research_viewpoints` 迁移 | `migrations/v2_migrate.py` | 按业务 PRD §2.6 映射规则 |
| P4-5 | `research_cards` 迁移 | `migrations/v2_migrate.py` | Card 作为 pending_review 状态的 ViewpointCard v2 |
| P4-6 | 迁移报告 | `migrations/v2_migrate_report.md` | 统计:迁了多少、丢弃多少、待确认多少 |

**P4 验收**:迁移脚本幂等(可重跑不出错),迁移后 v1 表数据完整保留,v2 表数据可查询。

#### P4 关键实现约束

**迁移脚本的幂等性**:每条 v1 记录迁移时带 `legacy_source` 字段(如 `"v1:viewpoint/45"`),脚本重跑时按 `legacy_source` 去重,不重复插入。

**LLM 辅助映射的产物**:

```
migrations/data/symbol_mapping_drafts.yaml  # LLM 生成的草稿
migrations/data/symbol_mapping_confirmed.yaml  # 用户确认后的最终映射
```

迁移脚本**只读 confirmed,不读 drafts**——确保所有映射都经过人工 review。

### 4.7 P5 任务清单:决策模块改造

| # | 任务 | 文件 | 关键细节 |
|---|---|---|---|
| P5-1 | `_load_research` 改造 | `decision_engine/data_loader.py` | 内部改调 `ViewpointRepository.query_for_decision` |
| P5-2 | 删除 `_distill_research_cards` | `decision_engine/data_loader.py` | 直接删除函数 |
| P5-3 | 保留 `_search_research_online` (v2.0) | `decision_engine/data_loader.py` | 暂时不动,v2.1 才删 |
| P5-4 | 搬迁 `search_portfolio_research` | `decision_engine/macro_context.py` | 从 data_loader 搬到新文件,语义不变 |
| P5-5 | 删除 `_CARD_DISTILL_CACHE` | `decision_engine/data_loader.py` | 代码和常量都删 |
| P5-6 | 保留 `_RESEARCH_CACHE` (v2.0) | `decision_engine/data_loader.py` | v2.1 随 Perplexity 迁移一起搬 |
| P5-7 | 验证 prompt 兼容 | 手动抽样测试 | `[用户资料]` / `[联网参考]` / `[ref:url]` 前缀在 Renderer 输出中存在 |

**P5 验收**:决策模块的 18 个测试用例全部通过,DecisionContext.research 字段内容与 v1 高度相似(格式一致、前缀一致)。

### 4.8 P6 任务清单:v2.0 稳定验收

按业务 PRD §7.1 的 4 条验收标准:

| # | 验收项 | 判据 |
|---|---|---|
| P6-1 | 全部持仓产生可用 ViewpointCard | 持仓 10-20 只 × 每只至少 3 张 = 30-60 张可用卡 |
| P6-2 | 18 个决策测试用例通过 | 运行决策模块回归测试,全绿 |
| P6-3 | Tab 3 和决策实际一致 | 抽样 5 个 case,手动对比 |
| P6-4 | 迁移完成 | `migrations/v2_migrate_report.md` 显示 100% 处理 |

P6 通过后,v2.0 正式稳定,**启动 v2.1 倒计时**(业务 PRD §7.2 的 T+3/T+7/T+10)。

### 4.9 P7(v2.1)任务清单

| # | 任务 | 关键细节 |
|---|---|---|
| P7-1 | `PerplexityAdapter` 实现 | 从 `_search_research_online` 搬代码到 Adapter |
| P7-2 | 缓存搬迁 | `_RESEARCH_CACHE` 搬到 Adapter 内部 |
| P7-3 | 删除 `_search_research_online` | 决策模块彻底只消费 Repository |
| P7-4 | 关系自动识别(LLM) | ViewpointProcessor 新增 relations 识别逻辑 |
| P7-5 | 批量审核 UI 优化 | 根据 v2.0 使用反馈优化 |

---

## 5. API 契约

### 5.1 v2 新增 endpoints

**写路径**:

```
POST   /api/research/v2/ingest/upload              # 用户上传(替代 /parse/text, /parse/url, /parse/pdf)
POST   /api/research/v2/ingest/alpha_vantage       # 触发 AV 拉取 { symbol }
POST   /api/research/v2/cards/{id}/judgment        # 更新判断层
POST   /api/research/v2/cards/{id}/confirm         # 用户 confirm(提升 confidence)
POST   /api/research/v2/cards/{id}/annotate        # 追加 annotation
POST   /api/research/v2/cards/bulk_approve         # 批量认可 { card_ids, judgment_overrides }
DELETE /api/research/v2/cards/{id}                 # 丢弃
```

**读路径**:

```
GET    /api/research/v2/cards                      # 列表(分页、过滤 status/symbol/event_type)
GET    /api/research/v2/cards/{id}                 # 单张
GET    /api/research/v2/decision_query             # Tab 3 用:query_for_decision 的 JSON 封装
                                                   # 返回 [{ card_id, rendered: "[用户资料]...", card: {...} }]
```

**v1 endpoints 保留不删**,v2.0 阶段前端主要走 v2,v1 只在迁移未完成时兜底。

### 5.2 兼容性约定

v1 的 `/api/research/viewpoints` 等 endpoint 在 v2.0 仍然工作(后端适配层从 `viewpoint_cards_v2` 查数据,按 v1 schema 输出)。这样即使前端有未切换的页面,也不会挂。

---

## 6. 决策模块的详细改造

### 6.1 `_load_research` 的 v2 实现

```python
# decision_engine/data_loader.py (v2 后)

def _load_research(session, pid: int, asset_name: Optional[str]) -> list[str]:
    """v2:走 ViewpointRepository,保持原签名兼容上游"""
    if asset_name is None:
        return _DEFAULT_MOCK_RESEARCH
    
    # 解析 asset_name 到 Symbol
    symbol = _resolve_symbol(asset_name, session)
    if symbol is None:
        # 无法解析,降级到联网搜索(v2.0 还有 _search_research_online)
        return _search_research_online(asset_name) or _DEFAULT_MOCK_RESEARCH
    
    # 走 Repository(自动按 Entity 扩展)
    repo = ViewpointRepository(session)
    rendered = repo.query_for_decision(
        symbol=symbol,
        since=datetime.now() - timedelta(days=60),
        validity=[ValidityStatus.ACTIVE],
        min_endorsement=[Endorsement.ENDORSE, Endorsement.REFERENCE_ONLY],
        min_confidence_score=0.5,
        top_k=10,
    )
    
    # v2.0 临时:如果观点库为空,再查联网(v2.1 后彻底删这段)
    if not rendered:
        rendered = _search_research_online(asset_name)
    
    return rendered or _DEFAULT_MOCK_RESEARCH
```

**关键细节**:
- 函数签名保持 `(session, pid, asset_name) -> list[str]` 不变,调用方无需改动
- `asset_name → Symbol` 的解析通过 `_resolve_symbol()`:查持仓表(若 pid 对应的持仓有该 asset_name,直接拿 symbol_v2);其次查 Entity 的 display_name;最后 LLM fallback
- v2.0 保留 `_search_research_online` 的 fallback 分支——这是过渡期

### 6.2 `_resolve_symbol` 的实现

```python
def _resolve_symbol(asset_name: str, session) -> Optional[Symbol]:
    # 1. 查当前 portfolio 的持仓表
    pos = session.query(Position).filter(
        Position.asset_name == asset_name,
        Position.symbol_v2.isnot(None)
    ).first()
    if pos and pos.symbol_v2:
        return Symbol.parse(pos.symbol_v2)
    
    # 2. 查 Entity 的 display_name
    entity = session.query(EntityV2).filter(
        or_(EntityV2.display_name_cn == asset_name,
            EntityV2.display_name_en == asset_name)
    ).first()
    if entity:
        symbols = json.loads(entity.symbols)
        if symbols:
            return Symbol.parse(symbols[0])  # 取第一个作为主 symbol
    
    # 3. 全都失败:返回 None,让上游走 fallback
    return None
```

### 6.3 `search_portfolio_research` 的搬迁

从 `decision_engine/data_loader.py` 整段搬到 `decision_engine/macro_context.py`,**函数签名、内部逻辑完全不变**,只是换个文件,让职责更清晰。

调用方(`decision_service.py:_stream_portfolio_intent`)更新 import 路径:

```python
# before:
from decision_engine.data_loader import search_portfolio_research

# after:
from decision_engine.macro_context import search_portfolio_research
```

---

## 7. Prompt 设计

### 7.1 ViewpointProcessor prompt 架构

4 个 prompt 模板文件,对应 4 种 source_type。**公共结构**:

```
[系统指令]
你是一名专业投研分析师,从以下{source_type}数据中生成一张 ViewpointCard。
严格按 JSON Schema 输出,不加 ```json 包裹。

[事实层 - 来自 adapter,已给定]
{raw_facts_pretty_printed}

[任务]
1. 确认 primary_symbol 和 affected_symbols
2. 生成叙事层:thesis / bull_case / bear_case / narrative_summary
3. 标注 event_type(从 14 项枚举选)
4. 标注 topics(受控词表)
5. 从事实层抽取 extracted_kpi
6. 为判断层生成 **低置信预填**:
   - confidence = "low"(硬性要求,不许改)
   - confidence_score = 0.3
   - 其他字段按叙事判断填
7. 给出 trigger_conditions / invalidation_conditions / key_metrics_to_watch

[输出 JSON Schema]
{json_schema}
```

### 7.2 四个 prompt 文件的差异

- **user_upload**:输入是"用户粘贴研报原文",LLM 要先从中识别 symbol/entity。允许 LLM 基于内容识别 `primary_symbol`。
- **alpha_vantage_news**:输入是一条 AV news + ticker_sentiment。`primary_symbol` 直接取 AV 的 relevance 最高的 ticker。sentiment 数据直接填 `sentiment_raw`。
- **alpha_vantage_fundamental**:输入是 OVERVIEW 数据。`event_type` 固定为 `market_movement` 或新设 `fundamental_snapshot`(考虑加 event_type 子类)。
- **alpha_vantage_earnings**:输入是 EARNINGS 数据。`event_type` 固定为 `earnings`。`extracted_kpi.eps_surprise_pct` 必填。

### 7.3 关于 event_type 的补充

业务 PRD 定了 14 项 event_type。但 fundamental snapshot(COMPANY_OVERVIEW)不是"事件",硬分类到 14 项会有点别扭——建议在工程阶段增加 `fundamental_snapshot` 作为第 15 个 event_type,专门给基本面快照类卡用。

**这是对业务 PRD 的微调**,我列在这里,你如果同意我在最终实现时加上;不同意就让 fundamental_snapshot 卡归入 `market_movement`。

---

## 8. 前端改动清单

### 8.1 Research.tsx 的改动范围

**不做**:
- 不拆文件(保持 1225 行单文件)
- 不改三 Tab 结构
- 不改样式大方向

**要改**:
- Tab 1 的审核区块:从"flat 表单"改成"事实/叙事/判断三区"
- Tab 1 新增"自动拉取"区块(按钮:手动刷新当前持仓的 AV 资讯)
- Tab 2 观点库:查询新 schema,过滤字段扩展(加 event_type)
- Tab 3 决策检索:后端接口改为 `GET /api/research/v2/decision_query`

### 8.2 审核区块的三区布局

```
┌─────────────────────────────────────────┐
│ [事实层 - 只读]                          │
│ 来源:Alpha Vantage News                 │
│ 发布时间:2026-04-16                     │
│ 原始摘要:...(灰色背景,禁止编辑)         │
├─────────────────────────────────────────┤
│ [叙事层 - 可追加]                        │
│ thesis: ...(只读)                       │
│ bull_case: ...(只读)                    │
│ bear_case: ...(只读)                    │
│ [追加备注] textarea                      │
├─────────────────────────────────────────┤
│ [判断层 - 必填,LLM 预填 low confidence]  │
│ stance:  ○ bullish ● bearish ○ neutral │
│ ...                                      │
│ [确认] [认可并入库] [仅参考] [丢弃]       │
└─────────────────────────────────────────┘
```

### 8.3 强制确认机制

判断层字段旁边显示"LLM 预填"标签,**只有用户点过 [确认] 按钮,confidence_score 才从 0.3 → 0.6**。UI 上显眼提示"此判断为 AI 预填,请确认后再入库"。

---

## 9. 验收清单(每个 Phase 结束时跑一遍)

### 9.1 Claude Code 自测清单(每个 Phase 都要做)

- [ ] 所有新增 Python 文件能 import 成功
- [ ] 相关的 pytest 单元测试全部通过
- [ ] 现有的 18 个决策测试用例全部通过(P5 之后强制)
- [ ] 没有新增 TODO / FIXME 标记(除非明确记录在 v2.2 backlog)
- [ ] 没有 print 调试语句残留(改用 logging)

### 9.2 用户验收 checkpoint(你手动做的)

**P0 结束**:review `entity_registry.yaml` 草稿,确认/修订。
**P1 结束**:人工审查 1 张 AV 生成的 ViewpointCard,质量可用?
**P2 结束**:通过 API 手动触发一次完整流程,数据库查回数据正确?
**P3 结束**:UI 走一遍上传→审核→入库,体验正常?
**P4 结束**:review 迁移报告,历史数据没有大规模丢失?
**P5 结束**:跑一个决策用例(比如"LI 要不要加仓"),输出和 v1 对比没有明显退化?
**P6 结束**:v2.0 稳定验收 4 条全部打勾。

---

## 10. 测试策略

### 10.1 单元测试

| 模块 | 重点测试 |
|---|---|
| Symbol / Entity | parse / canonical / 跨市场匹配 |
| Adapter | mock API 调用,测试错误处理 |
| Processor | 固定 raw_facts 输入,验证 LLM 输出 schema 合规(不验证 LLM 内容质量) |
| Repository | CRUD + Entity 扩展查询 + 过滤规则 |
| Renderer | 各 source_type 的渲染格式 |
| Router | 不同 symbol 的分发规则 |

### 10.2 集成测试

- **端到端 1**:上传一份研报 → 加工 → 入库 → 审核 → 查询回来,结构完整
- **端到端 2**:触发 AV 拉取一个 `:US` symbol → 三个子 adapter 并行 → 产出多张 card
- **端到端 3**:走 PositionDecision 意图 → DecisionContext.research 非空 → 决策引擎输出合理

### 10.3 迁移测试

- **幂等性**:运行迁移脚本 2 次,数据一致
- **完整性**:v1 所有记录在 v2 有对应(或明确标记 legacy)
- **回滚**:有一个脚本可以清空 v2 表,回到 v1 状态(紧急回退用)

---

## 11. 风险清单(按爆炸概率排序)

> **这是本 PRD 最重要的一节**。Claude Code 实施前必须先看这节。

### 🚨 R0:Claude Code 不能自作主张的事项

**这些事项不允许 Claude Code 在未确认的情况下做**:

1. **修改 `entity_registry.yaml` 内容**:只能生成草稿,不能 commit 到 DB
2. **修改 `llm_engine.py` 的 prompt**:决策 prompt 是隐式合约,Renderer 输出必须兼容现有 prompt,Claude Code **不要去改 prompt 适配 Renderer,要反过来**
3. **删除 v1 表或 v1 代码**:v2.0 阶段 v1 数据和代码全部保留
4. **修 signal_engine 的前缀污染 bug**:明确在 §1.5 说不改
5. **自作主张扩充 event_type 枚举**:除了 §7.3 提的 `fundamental_snapshot` 需要用户点头,其他不许新增

### 🔴 R1:Renderer 输出不兼容决策 prompt 合约(高爆炸概率)

**症状**:v2.0 上线后,决策引擎的"核心依据"段落不再引用数字、不再带链接、或者格式错乱。

**原因**:Renderer 输出的字符串里前缀错了、格式错了、句子太长了。

**防御**:
- P2 阶段 Renderer 实现完成后,必须跑 "字符串格式对比测试":对一批测试 card 渲染,和 v1 的 `_load_research` 输出做人工对比,前缀格式一致
- P5 阶段先不删 v1 路径,并行跑 v1 和 v2,对比决策引擎输出差异,差异超过某阈值不切换

### 🔴 R2:Alpha Vantage 限额在开发阶段被耗尽(高爆炸概率)

**症状**:开发期间,免费 25 次/天的限额被测试脚本吃光,Claude Code 卡住。

**防御**:
- P1 阶段在 Adapter 里加**开发模式 mock**:环境变量 `AV_DEV_MOCK=1` 时,返回本地 fixture 数据,不真调 API
- Fixture 数据提前准备好(你已经测试过的 LI、NVDA、TSLA 的真实返回)
- 真调 API 只在 P1-11 联调测试和 P6 验收时

### 🟡 R3:SQLite JSON 字段查询性能差(中爆炸概率)

**症状**:`affected_symbols` 是 JSON list,查询"包含 LI:US 的卡"时,需要 LIKE '%"LI:US"%',数据量大后慢。

**防御**:
- v2.0 数据量有限(几百张卡),LIKE 可接受
- 如果发现慢,加一个辅助表 `viewpoint_card_symbols_v2(card_id, symbol)`,做 join 查询
- v3 迁移到 Postgres 后用 JSONB + GIN 索引

**不要在 v2.0 做的事**:SQLite 的 JSON 函数(json_each)查询——语法复杂,Claude Code 容易写错

### 🟡 R4:迁移脚本把 v1 `opposing_points` 的错位数据继续错位(中爆炸概率)

**症状**:v1 里 `opposing_points` 是 JSON list 字段,但代码错把 `bear_case` 文本直接塞进去。迁移时如果 naive 地"list → bear_case text",会把错位的"被包装成 list 的 text"再错位回去,出现垃圾字符。

**防御**:
- 迁移脚本里专门处理这种情况:读 `opposing_points` 时,先检查是不是 list-of-one-string,是的话直接拆成 text

### 🟡 R5:LLM 预填的 confidence 不受控(中爆炸概率)

**症状**:明明 prompt 说 `confidence="low"`,LLM 输出 `confidence="medium"`,导致未确认的卡也进决策。

**防御**:
- Processor 在 LLM 返回后,**强制 override**:`card.judgment.confidence = "low"` 和 `card.judgment.confidence_score = 0.3`,不管 LLM 说啥
- 单元测试覆盖:给一个 prompt 故意诱导 LLM 说 high,验证 Processor 最终输出还是 low

### 🟢 R6:Entity 扩展查询误伤(低爆炸概率)

**症状**:用户查询 `LI:US` 的观点,返回了港股 LI 的观点混在一起,用户困惑。

**防御**:
- UI 在观点卡上显示 `primary_symbol` 标签,让用户看得出这条观点来自哪个市场
- 默认按 Entity 扩展,但 UI 给一个"仅本市场"的开关

### 🟢 R7:Perplexity 迁移滞后导致 v2.1 拖期(低爆炸概率但要警惕)

**症状**:v2.0 稳定后,Perplexity 迁移一直没做,两条路径长期并存。

**防御**:
- 业务 PRD 已经写死 T+3/T+7/T+10 硬约束
- 在 `TODO.md` 里写明日期
- v2.1 任务清单已经准备好,届时直接执行

### 🟢 R8:前端 1225 行单文件变成 1500 行(低爆炸概率)

**症状**:Research.tsx 越改越大,维护痛苦。

**防御**:
- 本 PRD 明确不拆文件,但允许 Claude Code 把新增的"三区审核"做成单独的 component(在同一文件内),用 function component 内联定义,不需要新建文件
- 文件大小不是 v2.0 重点,v2.2 再考虑拆

---

## 12. 开发时间估算与节奏

### 12.1 时间估算(以 Claude Code + 你 review 的节奏)

| Phase | Claude Code 编码 | 你 review 和反馈 | 总计 |
|---|---|---|---|
| P0 | 半天 | 半天 | 1 天 |
| P1 | 2 天 | 1 天 | 3 天 |
| P2 | 1.5 天 | 0.5 天 | 2 天 |
| P3 | 1.5 天 | 0.5 天 | 2 天 |
| P4 | 1 天 | 1 天 | 2 天 |
| P5 | 1 天 | 1 天 | 2 天 |
| P6 | - | 1 天 | 1 天 |
| **v2.0 小计** | **~7.5 天** | **~5.5 天** | **~13 天** |
| P7 (v2.1) | 3 天 | 1 天 | 4 天 |

### 12.2 开发节奏建议

- **每个 Phase 做完必须 commit,不要跨 Phase 攒代码**
- **每个 Phase 结束都要跑一遍 P5 前的那套"18 个决策测试用例"**(P5 之后强制),及早发现回归
- **P5 是最关键的转折点**,P5 做完当天必须完整走一遍决策流程,确认合约没破

---

## 13. 与业务 PRD 的对应关系

| 业务 PRD 条目 | 工程 PRD 位置 |
|---|---|
| §1 定位澄清 | 本 PRD §1.1、§6(search_portfolio 保留)|
| §2 三层 schema | §3.1(表设计),§7(prompt)|
| §3 InfoAdapter | §4.3 P1 任务,§2.2 目录布局 |
| §4 Symbol+Entity | §3.2-3.3,§4.2 P0 任务,§4.6 P4 迁移 |
| §5 核心业务流程 | §5 API,§6 决策改造 |
| §6 业务规则 | §4.5 P3 前端,§6 决策改造,§7 prompt |
| §7 分期 v2.0/v2.1 | §4 Phase 切分,§12 时间估算 |
| §8 边界 | §11 风险清单明确说不做 |

---

## 14. 文档版本记录

### v2-engineering-draft-1(2026-04-24)

- 基于业务 PRD final 版 + 决策模块消费梳理 + 用户 3 个确认决策
- 工程级决策 10 条(§1)
- Phase 切分 7 步(P0-P6 v2.0,P7 v2.1)
- 风险清单 9 项(R0-R8,按爆炸概率排序)
- 不改 signal_engine、不拆前端文件、不引入 Alembic、保留 search_portfolio_research

---

## 附录 A:给 Claude Code 的起手 prompt 模板

当你要把这份工程 PRD 交给 Claude Code 时,建议 prompt 是:

```
我将按 Phase 推进 WealthPilot 投研模块 v2 的开发。

先做 P0(基础设施搭建)。

前置阅读:
1. docs/investment_research_module_v2_business_prd_final.md
2. docs/investment_research_module_v2_engineering_prd_draft1.md(本文件)

执行 §4.2 的 P0 任务清单(7 个任务)。

硬约束:
- §11 R0 列出的 5 件事一件都不要做
- 每个任务完成后,按 §9.1 自测
- 不要跨 Phase 做事
- 每个任务 commit 一次,commit message 格式:"[v2/P0-N] 任务描述"

不要自作主张优化现有代码、不要写看起来可以复用的"工具函数"。
只做任务清单里明确列出的事。
做完告诉我,等我 review 后再进 P1。
```

每个 Phase 开始前,都用类似的 prompt 起手,明确 Phase 和硬约束,防止 Claude Code 发散。

---

## 附录 B:v2.0 完成后的下一步

v2.0 稳定 + T+10 完成 Perplexity 迁移后,下列事项进入 v2.2 backlog:

- signal_engine 的前缀污染修复
- 定时调度(APScheduler)
- ViewpointCard 关系的自动识别
- topics 词表扩展
- Research.tsx 拆文件
- SQLite → Postgres 迁移调研

然后是 v3:
- WealthPilot 自身作为 MCP Server 暴露
- 向量检索 / RAG 升级
- HK/A 股专属 adapter

**v2 到此定稿**,进入开发阶段。
