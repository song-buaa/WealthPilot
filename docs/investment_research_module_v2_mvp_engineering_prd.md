# 投研观点模块 v2.0 MVP · 工程 PRD

> **版本**:v2.0-mvp-draft-1
> **日期**:2026-04-24
> **状态**:v2.0 实际执行蓝图,用于交付 Claude Code
> **与完整工程 PRD 的关系**:本文档是 v2.0 的**最小闭环交付计划**。完整工程蓝图见 `investment_research_module_v2_engineering_prd_draft1.md`,其中未在本 MVP 覆盖的内容作为 v2.1 / v2.2 backlog。
> **交付目标**:7-8 天完成 v2.0 MVP,跑通端到端闭环。

---

## 0. MVP 的唯一目标

**一句话目标**:

> Alpha Vantage 进来,生成 v2 ViewpointCard,经人工确认后,PositionDecision 意图能通过 Renderer 消费它,决策输出质量不退化。

**不在 MVP 目标内的事**(全部移到 v2.1+):

- 完整的 v1 数据迁移
- PerplexityAdapter 迁移
- 批量审核
- Annotation 追加
- Relations 关系字段的前端交互
- 自动识别关系
- Entity 表(MVP 用 YAML)
- 完整测试体系
- 前端三区漂亮版
- signal_engine 修复
- 端到端集成测试完整覆盖

---

## 1. MVP 与完整工程 PRD 的差异清单

| 维度 | 完整工程 PRD | MVP |
|---|---|---|
| 新建表 | 3 张 (_v2) | 1 张 (`viewpoint_cards_v2`) |
| 其他表 | 新建 _v2 版 | ALTER 加字段 |
| Entity | 建表 `entities_v2` | YAML 文件加载 |
| v1 数据迁移 | 完整脚本 + 待确认清单 | 标 legacy 保留,不迁 |
| 前端审核页 | 三区完整 + 批量 + annotation | 显示 facts/narrative,只编辑 judgment |
| API 层 | 8 个 v2 endpoints | 4 个核心 endpoints |
| 测试体系 | 完整单测 + 集成测试 | 只测 3 个必守底线(见 §6.2) |
| Perplexity | v2.0 暂保留,v2.1 迁 | 同 |
| Phase 数 | 7 步(P0-P6) | 5 步(合并 P1/P2,压缩 P3/P4/P5) |
| 总工期 | 13 天 | 7-8 天 |

---

## 2. 不能砍的 3 条(violate 这些就不算 v2.0)

MVP 可以砍很多东西,但以下 3 条**绝对不能砍**,砍了等于 v2.0 没做完。

### 2.1 LLM 预填的 confidence 硬编码为 low

**即便 MVP 版本,Processor 在 LLM 返回后必须强制 override**:

```python
card.judgment.confidence = "low"
card.judgment.confidence_score = 0.3
# 不管 LLM 说啥
```

**配套必须有**:
- UI 显示"AI 预填,点击确认"标识
- 点击 confirm 按钮 → confidence_score 提升到 0.6
- `_load_research` 查询时默认过滤 `confidence_score >= 0.5`

**为什么不能砍**:这是数据质量的最后一道阀门。砍了就会让未经你确认的 AI 判断直接污染决策,你甚至追不到是哪条观点污染了结果。

### 2.2 Renderer 输出必须兼容决策 prompt 合约

P5 改造 `_load_research` 之后,必须手动跑一次决策用例,确认输出里有:
- `[用户资料]` / `[联网参考]` 前缀
- `[ref:url]` 标签(有 URL 时)
- 单行句子,不换行
- 长度控制在 80-200 字

**不能砍的验证环节**:对 3-5 张 ViewpointCard 做 Renderer 输出,和 v1 `_distill_research_cards` 的输出做人工字符串对比。格式差异不能大。

### 2.3 18 个决策测试用例 P5 之后必须全绿

这是 v2.0 的最小稳定门槛。不能因为"MVP 简化"就跳过这个测试。

不需要写**新**的测试用例,但**现有的 18 个必须跑过**。如果某个 case fail,要么是 v2 实现有问题,要么是 v1 的 mock 数据和 v2 不一致——两种情况都必须搞清楚,不能带病上线。

---

## 3. MVP 数据库设计

### 3.1 只新建一张表:`viewpoint_cards_v2`

schema 按完整工程 PRD §3.1,字段全保留(三层 schema 是核心价值,不能砍)。

### 3.2 其他改动用 ALTER(风险可控,因为 SQLite 的 ALTER 虽弱但加字段是支持的)

```sql
-- research_documents 加两个字段
ALTER TABLE research_documents ADD COLUMN raw_content_hash TEXT;
ALTER TABLE research_documents ADD COLUMN parsed_primary_symbol TEXT;

-- positions 加一个字段(symbol 标准化配套)
ALTER TABLE positions ADD COLUMN symbol_v2 TEXT;
```

**不做的**:
- 不建 `research_documents_v2`(ALTER 够用)
- 不建 `entities_v2`(YAML 够用)
- 不动 `research_cards`(作为 legacy 保留,不迁移)
- 不动 `research_viewpoints`(作为 legacy 保留,不迁移)

**legacy 保留策略**:v1 的 `research_cards` / `research_viewpoints` 表保留,查询接口保留,但决策模块 v2 改造后不再使用它们。它们只供"查历史观点"用途,新数据不再写入。

### 3.3 Entity 用 YAML

```yaml
# data/entity_registry.yaml
entities:
  - entity_id: li_auto
    display_name_cn: 理想汽车
    display_name_en: Li Auto Inc.
    symbols: [LI:US, 2015:HK]

  - entity_id: tencent_holdings
    display_name_cn: 腾讯控股
    display_name_en: Tencent Holdings
    symbols: [0700:HK, TCEHY:US]

  # ... 其他 10-20 家
```

启动时加载到内存,查询时从内存字典读。10-20 条数据,全内存毫秒级。

**v2.2 或更晚再考虑建表**。YAML 的天然好处是:你随时可以手编辑、Git 可见、不需要 DB migration、加/改条目只需要重启 backend。

---

## 4. MVP 目录布局(精简版)

```
wealthpilot_backend/
├── app/
│   └── models.py                # 在这里加 ViewpointCardV2 ORM(不新建 models_v2.py)
├── research_v2/                  # 新目录,v2 核心代码集中
│   ├── __init__.py
│   ├── schemas.py               # Pydantic models
│   ├── symbol.py                # Symbol + Entity + YAML 加载
│   ├── adapters/
│   │   ├── base.py
│   │   ├── user_upload.py
│   │   ├── alpha_vantage.py     # 3 个子 adapter 合一个文件(省目录)
│   │   └── perplexity_stub.py   # 占位,v2.1 实现
│   ├── router.py
│   ├── processor.py
│   ├── repository.py
│   ├── renderer.py
│   └── prompts/
│       ├── user_upload.txt
│       └── alpha_vantage.txt    # 3 个 av 子 adapter 共享一个 prompt 模板,按 source_type 分支
├── data/
│   └── entity_registry.yaml
├── backend/
│   ├── api/research.py          # 在现有文件加 v2 endpoints,不新建文件
│   └── services/research_service.py
└── decision_engine/
    └── data_loader.py           # 改造 _load_research,删 _distill_research_cards
```

**和完整工程 PRD 的差异**:
- Adapter 不拆 3 个文件,合 1 个
- 不新建 `macro_context.py`,`search_portfolio_research` 原地保留在 data_loader.py(加注释说明"保留,不迁")
- 不新建 `models_v2.py`,在 models.py 里加
- 不新建 `migrations/` 目录,迁移脚本临时放 `scripts/v2_init.py`

---

## 5. Phase 切分(5 步,7-8 天)

### 5.1 Phase 总览

| Phase | 内容 | 工期 | 备注 |
|---|---|---|---|
| **M0** | schema + Symbol/Entity YAML + Renderer 骨架 | 1 天 | 最先定好数据结构 |
| **M1** | Alpha Vantage Adapter + Processor(最小链路能端到端) | 2 天 | 这是最吃功夫的部分 |
| **M2** | Repository + API(写入 + 查询 + Renderer 调用) | 1.5 天 | 合并原 P2 和 P5 的部分 |
| **M3** | 决策模块改造:`_load_research` 接入 Repository,保留 fallback | 1 天 | 最关键的合约验证点 |
| **M4** | 前端最小审核页 + Tab 3 修正 | 1.5-2 天 | 够用就行,不追求美观 |
| **M5** | 回归测试 + bug 修复 + 稳定性 | 1 天 | 18 个决策用例全绿 |

**合计 7-8 天**,含你 review 时间。不含面试占用(如果面试密集,按现实 2-3 周)。

### 5.2 M0:基础设施(1 天)

| # | 任务 | 文件 | 完成判据 |
|---|---|---|---|
| M0-1 | Symbol / Entity 类 + YAML 加载器 | `research_v2/symbol.py` | `Symbol.parse()` / `EntityRegistry.lookup()` 可用,3 个 assertEqual 测试通过 |
| M0-2 | `entity_registry.yaml` 草稿 | `data/entity_registry.yaml` | 10-15 家,**草稿状态**(你 review 前不算数) |
| M0-3 | ViewpointCard Pydantic schemas | `research_v2/schemas.py` | 三层结构,可序列化反序列化 |
| M0-4 | ORM 模型 | `app/models.py` 内追加 | `viewpoint_cards_v2` 表建好 |
| M0-5 | ALTER 旧表 | `scripts/v2_init.py` | 幂等,重跑不报错 |
| M0-6 | Renderer 骨架 + 单测 | `research_v2/renderer.py` + `tests/test_renderer.py` | 输入 mock card,输出带前缀字符串,5 个 case 覆盖 4 种 source_type |

**M0 你需要做的**:review `entity_registry.yaml`,确认/修订。这一步不能省。

### 5.3 M1:Alpha Vantage 最小链路(2 天)

目标:一条命令从 Alpha Vantage 拉 LI:US 数据,端到端产出一张 ViewpointCard 实例(不入库,只是能跑出来)。

| # | 任务 | 文件 | 完成判据 |
|---|---|---|---|
| M1-1 | `InfoAdapter` 抽象基类 + `RawFact` | `research_v2/adapters/base.py` | 接口清晰,含 `AdapterQuotaError` |
| M1-2 | AlphaVantageAdapter 3 子 | `research_v2/adapters/alpha_vantage.py` | news/fundamental/earnings 三个方法,共用一个 class |
| M1-3 | dev mock 模式 | 环境变量 `AV_DEV_MOCK=1` | 读本地 fixtures,不真调 API |
| M1-4 | fixtures 准备 | `tests/fixtures/av_*.json` | LI / NVDA / TSLA 的真实返回存下来 |
| M1-5 | UserUploadAdapter | `research_v2/adapters/user_upload.py` | 包装现有 parse 逻辑 |
| M1-6 | `InfoRouter` | `research_v2/router.py` | 按 symbol 后缀分发,降级逻辑 |
| M1-7 | `ViewpointProcessor` | `research_v2/processor.py` | LLM 调用 + confidence 强制 override |
| M1-8 | Processor prompt | `research_v2/prompts/*.txt` | 2 个文件:upload 和 av |
| M1-9 | **端到端手动测试** | `scripts/m1_smoke.py` | `python scripts/m1_smoke.py LI:US` 能打印一张完整 ViewpointCard |

**M1 不能砍的**:
- `AV_DEV_MOCK` 开发模式(R2 风险:AV 限额吃光)
- Processor 的 confidence override(不能砍条款 1)

**M1 你需要做的**:M1-9 结束后,人工审 1 张 card 的质量,判断 thesis/bull_case/bear_case 是否通顺,decision_signal 是否合理。不通顺就调 prompt 再跑。

### 5.4 M2:Repository + API(1.5 天)

| # | 任务 | 文件 | 完成判据 |
|---|---|---|---|
| M2-1 | `ViewpointRepository` 基本 CRUD | `research_v2/repository.py` | insert / get_by_id / update_judgment / delete |
| M2-2 | 查询方法 | `research_v2/repository.py` | `query_cards(symbol, since, validity, min_conf)` |
| M2-3 | Entity 扩展查询 | `research_v2/repository.py` | 传入 `LI:US`,返回同 Entity 的所有卡 |
| M2-4 | `query_for_decision` | `research_v2/repository.py` | 返回 `list[str]`,调用 Renderer |
| M2-5 | API endpoints(4 个核心) | `backend/api/research.py` | 见下 |
| M2-6 | Service 编排 | `backend/services/research_service.py` | 调 Router / Processor / Repository |

**MVP 的 4 个核心 endpoint**:

```
POST /api/research/v2/ingest/upload          # 用户上传
POST /api/research/v2/ingest/alpha_vantage   # 触发 AV 拉取
POST /api/research/v2/cards/{id}/judgment    # 更新判断层(含 confirm)
GET  /api/research/v2/cards                  # 列表查询(过滤 symbol / status)
```

**移到 v2.1 的 endpoints**:
- `/cards/bulk_approve`(批量审核)
- `/cards/{id}/annotate`(annotation)
- `/decision_query`(Tab 3 用,MVP 前端可以直接用 `GET /cards?render=true` 兜底)

### 5.5 M3:决策模块改造(1 天)

**这是 MVP 最关键的一步**,R1 风险最集中。

| # | 任务 | 文件 | 完成判据 |
|---|---|---|---|
| M3-1 | `_load_research` 改造 | `decision_engine/data_loader.py` | 调 `ViewpointRepository.query_for_decision` |
| M3-2 | `_resolve_symbol` 实现 | `decision_engine/data_loader.py` | 持仓 → Entity → None 三层 fallback |
| M3-3 | 删除 `_distill_research_cards` | `decision_engine/data_loader.py` | 函数和 cache 都删 |
| M3-4 | 保留 `_search_research_online` | - | 保留,作为 v2.0 期间 fallback |
| M3-5 | 保留 `search_portfolio_research` | - | 保留,写注释"v2 不迁,它是宏观上下文不是投研观点" |
| M3-6 | **格式合约对比** | 手动 | 抽 3-5 张 card,Renderer 输出对比 v1 `_distill_research_cards` 输出,格式一致 |

**M3 不能砍的**:
- M3-6 的格式合约对比(不能砍条款 2)
- M3-5 的注释(避免 v2.1 时 Claude Code 误删)

### 5.6 M4:前端最小审核页(1.5-2 天)

**MVP 的 UI 原则:能用、丑也行**。不追求美观,只追求功能覆盖。

| # | 任务 | 文件 | 完成判据 |
|---|---|---|---|
| M4-1 | Tab 1 审核页:只读事实层 + 只读叙事层 | `frontend/src/pages/Research.tsx` | 三个区块区分显示 |
| M4-2 | Tab 1 审核页:可编辑判断层 | `frontend/src/pages/Research.tsx` | stance / confidence / action_type 等字段可选/填 |
| M4-3 | **Confirm 按钮** | `frontend/src/pages/Research.tsx` | 点击后 confidence_score 0.3 → 0.6 |
| M4-4 | Tab 1:触发 AV 拉取按钮 | `frontend/src/pages/Research.tsx` | 手动触发 + 显示进度 |
| M4-5 | Tab 3 修正 | `frontend/src/pages/Research.tsx` | 调用 `GET /cards?symbol=&render=true`,显示决策会看到的内容 |
| M4-6 | 类型定义 | `frontend/src/lib/api.ts` | ViewpointCardV2 type |

**不做的 UI 项**:
- 批量勾选
- Annotation 输入框
- 复杂过滤(event_type / topics 等)
- 三区美观排版
- Relations 关系展示

**全部移到 v2.1 或更晚**。

### 5.7 M5:回归 + bug(1 天)

| # | 任务 | 判据 |
|---|---|---|
| M5-1 | 18 个决策测试用例全绿 | 不能砍条款 3 |
| M5-2 | 端到端走一遍:上传一份研报 → 审核 → 入库 → Tab 3 能查到 | 人工走一遍 |
| M5-3 | 端到端走一遍:触发 AV 拉 NVDA → 生成 card → confirm → 跑决策,能消费到 | 人工走一遍 |
| M5-4 | bug 修复 | - |

---

## 6. MVP 的测试策略(3 条必守底线)

### 6.1 不写的测试

- 不写完整 Adapter / Processor / Repository 的单元测试覆盖
- 不写前端测试
- 不写迁移测试(因为不做迁移)

### 6.2 必守的 3 条

1. **Renderer 单测**:5-8 个 case,覆盖 4 种 source_type + 有无 URL
2. **Processor 的 confidence override 单测**:给一个故意说 "high" 的 mock LLM,验证 Processor 输出还是 "low"
3. **18 个决策回归用例**:全绿,在 M3 和 M5 各跑一次

这 3 条是**数据质量和架构合约的最小保证**,删了 MVP 就不算完成。

---

## 7. 风险提示(精简版)

完整风险清单见完整工程 PRD §11。MVP 阶段要特别盯住:

### 🚨 R0 (保留):Claude Code 不能自作主张的事

- 不能改 `entity_registry.yaml` 内容(只能生成草稿,你人工确认)
- 不能改 `llm_engine.py` 的 prompt
- 不能删 v1 表和代码(标 legacy 保留)
- 不能顺手修 signal_engine bug
- 不能扩充 event_type 枚举(除非你点头)

### 🔴 R1:Renderer 输出不兼容决策 prompt 合约

M3-6 是唯一防御。跳过就爆炸。

### 🔴 R2:Alpha Vantage 限额被开发耗尽

`AV_DEV_MOCK=1` 环境变量,M1-3 必须做。

### 🟡 R5:LLM 预填 confidence 不受控

Processor 的硬 override,M1-7 和不能砍条款 1 约束。

---

## 8. MVP 外的事项清单(v2.1 / v2.2 backlog)

以下事项**不在 MVP 范围**,完成 MVP 后逐条处理:

### v2.1(MVP 稳定后 T+3 天启动,T+10 天完成)

- PerplexityAdapter 迁移 + `_search_research_online` 删除
- v1 数据迁移(如果你觉得需要;如果觉得 legacy 保留够用,可以永久不迁)
- 批量审核 UI
- Annotation 输入
- `decision_query` 专用 endpoint
- Entity 表化(如果 YAML 不够用了)

### v2.2(v2.1 稳定后)

- Relations 自动识别
- signal_engine 前缀污染修复
- 前端三区美化 + 拆组件
- 定时调度
- event_type topics 词表扩展
- 完整测试覆盖

### v3(不在 v2 范围)

- WealthPilot MCP Server 暴露
- RAG 升级
- HK/A 股专属 Adapter

---

## 9. 给 Claude Code 的起手 prompt(MVP 版)

**每个 Phase 一次**,不要混。示例:

### M0 起手

```
我将按 MVP 计划推进 WealthPilot 投研模块 v2.0 开发。现在开始 M0。

前置阅读:
1. docs/investment_research_module_v2_business_prd_final.md
2. docs/investment_research_module_v2_mvp_engineering_prd.md(本文件)

目标:完成 §5.2 的 M0 任务清单(6 个任务)。

硬约束:
- §7 R0 列出的 5 件事一件都不要做
- 每个任务完成后 commit 一次,message: "[v2-mvp/M0-N] 描述"
- 不要跨 Phase 做事(不要提前做 M1 的代码)
- 不要"顺手优化"现有代码
- 不要写"可能会用到"的工具函数
- entity_registry.yaml 只生成草稿,等我确认后再用

做完 M0-6 后告诉我,等我 review YAML 草稿和 Renderer 单测,通过后再进 M1。
```

### M1-M5 同理,每次只给一个 Phase 的 scope。

---

## 10. 时间节奏与 checkpoint

```
Day 1:     M0 完成。你 review YAML 草稿。
Day 2-3:   M1 完成。你审 1 张真实 AV 生成的 card 质量。
Day 4-5:   M2 完成。你走一遍 API。
Day 5-6:   M3 完成。你看格式合约对比结果。
Day 6-7:   M4 完成。你 UI 走一遍。
Day 8:     M5 完成。18 个决策用例全绿 → v2.0 MVP 达成。
Day 8+3:   启动 v2.1(Perplexity 迁移)。
Day 8+10:  v2.1 完成。v2 定稿。
```

**节奏提醒**:
- 每个 Phase 结束必须 commit,不要跨 Phase 攒代码
- 每个 Phase 的 checkpoint 你必须真的做,不能偷懒
- 发现 M1 或 M3 不对劲,立刻停下来调整,不要硬往后走

---

## 11. 本 MVP PRD 与 GPT 反馈的对应

本文档基于 GPT "scope 太大,砍到 6-8 天" 的反馈重写。相对完整工程 PRD:

**接受的 GPT 简化(5 条)**:
1. 只新建 `viewpoint_cards_v2` 一张表
2. Entity 用 YAML
3. 前端只做最小审核
4. v1 数据标 legacy,不迁
5. 合并 Phase 做最小闭环

**Claude 补充的 3 条"不能砍"**:
1. Processor 的 confidence 硬 override
2. Renderer 输出的格式合约对比(M3-6)
3. 18 个决策回归用例

**保留原工程 PRD 的内容**:
- §1 的 10 条关键决策(只是实施方式简化,决策不变)
- §11 的 R0-R5 风险(精简为本文 §7)
- Adapter 薄 + Processor 厚的架构思想
- Renderer 的前缀合约

---

## 12. 一个提醒(自我反思)

完整工程 PRD 的确写过重了,原因是我在"工程严谨"和"工期现实"之间偏向了前者。GPT 的反馈点到了关键:**你不是在做中型团队的系统重构,你是在做一个带着面试压力的个人产品迭代**。

MVP 思维:**先跑通闭环,再加厚**。v2.0 MVP 只需要证明"新架构能跑、合约没破、决策没退化",其他都是 v2.1+ 的事。

7-8 天走完 MVP,之后有数据可以面试讲(真实数据跑过、真实架构有过迭代、有 T+10 的硬约束时间表),比一个做了 3 周还没跑完的"完美架构"强 10 倍。

v2 的核心不是架构多漂亮,而是**两条路径统一、ViewpointCard 经过你手里**。这两件事在 MVP 里都有——MVP 就够了。

开干吧。
