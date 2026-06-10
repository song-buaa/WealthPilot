# C 范围待接通 Skill 备忘（给未来迭代看的待办，非面试材料）

> **用途**：记录"4 Agent + 12 Skill"架构里，哪些 Skill 已真接通、哪些还没、各自的坑。
> 有空就按"同范式"续接，没空不影响产品功能（系统走老路径照常跑）。
> **当前进度**：C 范围已做 C0/C1/C2（tag v3.8.5/.6/.7）+ flag 默认翻开（commit 1e1cdc0），
> 按"接 2-3 个代表性样板就收"的策略告一段落。
> **日期**: 2026-06-02

---

## 0. 一句话现状

架构图是"4 个 PEER Agent + 12 个 Skill"。当前**生产环境默认真正经 invoke_skill 跑的有 5 个**
（原生 3 个 + C1 的 output-validator + C2 的 retrieve-principles，后两个的 flag 已默认翻开、生产生效）。
其余 Skill 要么绕过 Skill 机制走写死代码（功能正常，只是没走"正门"），要么是不接入主流程的独立能力。
**双轨仍保留**：C1/C2 的 flag 显式设 =0 可随时切回老直连路径作回退。删旧路 + 退役 LEGACY 表是最后一步（C7，未做）。

---

## 1. 12 个 Skill 现状一览

| # | Skill | 状态 | 说明 |
|---|---|---|---|
| 1 | wp-load-context | ✅ 原生达标 | 一直经 invoke_skill |
| 2 | wp-check-discipline | ✅ 原生达标 | 一直经 invoke_skill |
| 3 | wp-generate-signals | ✅ 原生达标 | 一直经 invoke_skill |
| 4 | wp-output-validator | ✅ C1 接通（双轨+flag，**默认开、生产生效**） | flag=WP_USE_SKILL_OUTPUT_VALIDATOR；设 =0 切回老路 |
| 5 | wp-retrieve-principles | ◐ C2 接通**仅 general 路由**（双轨+flag，**默认开、生产生效**） | flag=WP_USE_SKILL_RETRIEVE_PRINCIPLES；设 =0 切回老路；position/portfolio 路径仍未接（见 §2） |
| 6 | wp-fetch-holdings | ✗ 未接 | 在 data_loader.load() 内部，被 load-context 吞掉 |
| 7 | wp-fetch-research | ✗ 未接 | 同上（部分），portfolio·Review 处另有直连 |
| 8 | wp-reasoning | ◐ 机制就绪仅 chat | C0 补了 llm_dispatch 但只通 chat；reason 类未接（见 §2） |
| 9 | wp-citation-rules | ⊘ 已裁决不接 | 归并为 reasoning 的 prompt 规范，不作独立 Skill |
| 10 | wp-calc-allocation-deviation | ✗ 未接（幽灵） | tool 实现已存在，无人调；要让 portfolio 真 invoke |
| 11 | wp-propose-allocation | ✗ 未接（幽灵） | 同上 |
| 12 | wp-action-planner | ⊘ 已裁决不并入 | 前端按钮直触的独立能力，不进 PEER 主流程 |

图例：✅ 真接通 · ◐ 部分接通 · ✗ 未接（功能仍正常，走老路） · ⊘ 已决定不接入主流程

**主流程接通范围 = 10 个（12 减去 #9 #12）。已真接通 5 个，部分 1 个，剩 4 个待接。**

---

## 2. 待接通 Skill + 各自的坑（按建议优先级）

### 2.1 wp-retrieve-principles 的 position/portfolio 路径（C2 只接了 general）

- **现状**：position/portfolio 的 retrieve 在 `data_loader.load()` 内部 Step 7b（约 L752-766），
  被 wp-load-context 吞掉、直连 KnowledgeStore，不单独 invoke。
- **坑**：接通要改 `data_loader.load()` 或 `execute_load_decision_context`，影响面比 general 大得多
  （load-context 是所有 position/portfolio 请求的入口，动它风险高）。
- **返回契约**：同 general，直连返回 list[RetrievedChunk]、Skill 版返回 dict，需 `_adapt_retrieve_result`
  适配（C2 已写好这个函数，可复用）。

### 2.2 wp-fetch-holdings / wp-fetch-research

- **现状**：都在 `data_loader.load()` 内部直连 service（holdings 走 position_aggregator，
  research 走 Perplexity / 盈米 MCP），被 load-context 吞掉。
- **坑**：同 2.1——要解耦 data_loader 内部步骤、改 load-context，影响面大。
- **设计悬而未决**：这三个 fetch（holdings/research/principles）到底是"建模成 load-context 的展开子 Skill"
  还是"从清单移除、承认是 load-context 内部实现"——这个决策当时留给了未做的 manifest 驱动阶段。

### 2.3 wp-reasoning（reason 类，C0 只通了 chat）

- **现状**：C0 补的 llm_dispatch 机制只放行了 `general_chat → chat`。reason / review_portfolio /
  analyze_allocation / analyze_performance 四个"厚函数"未接，碰到会抛"待 C6"的 NotImplementedError。
- **坑（最硬）**：这四个函数吃 LoadedData 这种大 dataclass（reason 还要 IntentResult/RuleResult/SignalResult），
  直接 `**params` 传会让 Skill 边界很"厚"。**接法预案**：定义一个 ReasoningContext 容器打包这些上下文，
  invoke 时传 `template_id + ctx`，由 _invoke_llm_dispatch 内部拆包分发（见 C0 PRD §3.3）。
- 这是 C 范围里最复杂的一步，留到最后。

### 2.4 wp-calc-allocation-deviation / wp-propose-allocation（两个幽灵）

- **现状**：tool 实现（execute_calc_deviation / execute_propose_increment）已存在于 backend/graph/tools.py，
  但生产代码无人 invoke。它们在 LEGACY 表的 portfolio bundle 里、在 LLM Selector 候选集里。
- **坑**：接通 = 让 portfolio 路径在该用时真 invoke 它们（涉及 LLM Selector 增补真生效——这正是
  v3.8.1 当初发现"Selector 是装饰品"的解药）。**注意**：当初 v3.8.3 差点把它们当垃圾删掉，
  方向是错的——它们不是要删、是要接通。
- **返回契约**：function_call 类，同样要 check 直连/Skill 返回是否一致（参考 retrieve 的教训）。

---

## 3. 通用的坑（接任何 Skill 都要注意）

1. **返回契约不一致（最常见）**：很多 Skill 的"直连版（近路）"和"tool 实现（正路）"返回类型不一样
   （retrieve 就是：直连返回对象、Skill 返回 dict）。接通时必须 check，不一致就加适配层
   （`_adapt_retrieve_result` 是个可复用范本）。否则下游 `getattr(c,"content")` 会拿到 dict、语义坏掉。
2. **查询/调用参数对齐**：直连可能用了和 Skill 默认不同的参数（retrieve general 直连只查
   allocation_principles/top_k=3，Skill 默认全三类/top_k=5）。接通时要对齐，否则结果变、双轨不等价。
3. **对账层只看 Executing 阶段**：Expressing/Reviewing 阶段的 Skill 接通后对账看不到
   （validator 就是），"行为等价"要靠"该 Skill 影响的可观测 SSE 输出 flag on/off 对拍"来验，不能靠对账。
4. **data_loader.load() 内部的 Skill**（holdings/research/principles 的 position/portfolio 路径）
   影响面大，动它要格外小心——它是所有决策请求的数据入口。

---

## 4. 接通范式（同范式可续接）

每个 Skill 按这套来，和 C1/C2 一致：
1. 一个 flag（WP_USE_SKILL_XXX），双轨：flag on 走 invoke_skill、flag off 走老路径。
   （接通并验证等价后，把默认翻开让其生产生效，显式设 =0 仍可回退——C1/C2 已是此状态。）
2. 若返回契约不一致 → 加适配层让两轨产出同类型同结构。
3. 验收：flag on/off 对拍（对账看得到的看对账、看不到的对拍 SSE 可观测输出），确认等价。
4. 旧路径保留，最后统一删（见 §5）。

---

## 5. 收尾阶段（C7，未做，想彻底完成时再做）

把所有 Skill 接通后：
1. 把所有 WP_USE_SKILL_* flag 默认翻开（生产正式走 Skill 机制）。
   注：C1/C2 的两个 flag 已于 commit 1e1cdc0 默认翻开生效，后续新接通的 Skill 验证等价后照此翻开。
2. 删除所有旧直连 fallback 路径。
3. LEGACY_SELECTED_SKILLS_BY_ROUTE 退役；解除 SKILL_MANIFEST 的"不许读"限制，让它正式驱动。
4. **删幽灵 Skill 的连锁账**（若届时选择不接通而是删除）：从 LEGACY 表删 → selected_skills 变 →
   对账基线变 → 同步更新 V381_BUNDLE 黄金快照 + 重跑对账基线（详见 v3.8.2 commit 文档 B.2）。

---

## 6. 一句话给未来的自己

产品现在能正常跑：已接通的 5 个 Skill（含 C1/C2 两个）默认经 Skill 机制跑、生产生效；
未接的几个走写死代码、功能照常。**不接剩下这些 Skill 不影响功能**。继续接通的价值是"架构图与代码
进一步一致"（求职 portfolio 成色 / 工程完整度），不是功能提升。有整块时间且想把架构做实时再按 §4
范式逐个接，按 §2 优先级（先 retrieve 补全 → fetch 类 → 幽灵 → 最后 reasoning）。没时间就保持现状，完全 OK。
