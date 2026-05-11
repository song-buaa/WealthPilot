# WealthPilot v3.2 投资行动模块 PRD

> **文档版本**：v0.7（订单类型简化版）
> **创建日期**：2026-05-10
> **作者**：Songbin
> **状态**：M4 修复中
> **依赖版本**：WealthPilot v3.1（已发布）
>
> **版本号说明**：本次升级在 v3.x Multi-Agent 架构基础上新增功能模块和外部集成（ActionPlanner Skill + BrokerAdapter 抽象层），未改变核心架构范式，故定为 v3.2。产品定位的升级（从决策工作台到决策执行系统）通过文档叙事表达，不通过主版本号反映。
>
> WealthPilot 主版本号约定：1.x = Streamlit 架构；2.x = React + FastAPI 架构；3.x = Multi-Agent 架构；后续 4.x 触发条件预期为底层架构范式切换，例如引入实时事件驱动、多租户改造、Agent 框架替换、长期记忆 / 案例库等。

---

## 修订历史

| 版本 | 日期 | 修改内容 | 作者 |
|------|------|----------|------|
| v0.1 | 2026-05-10 | 初稿，基于产品讨论形成 | Songbin |
| v0.2 | 2026-05-10 | 评审反馈修订：1) 修正条件单触发逻辑为券商托管模式；2) 人工最终确认前移至"提交券商前"；3) 凭证管理改为本地 FastAPI + keyring；4) 明确交易模块仅本地部署边界；5) 策略状态与订单状态拆成两套状态机；6) 数据模型支持 strategy : order = 1 : N；7) Expressing/ActionPlanner 职责拆清；8) MVP 拆两档：v3.2 Mock + v3.3 Tiger 实战；9) MVP 仅限价单；10) Tab 名称简化 | Songbin |
| v0.3 | 2026-05-10 | 执行细节修订：1) 统一"限价单 / 条件限价单 / 券商托管触发"语义；2) Tiger API 调研前置为 v3.3 启动硬性条件；3) v3.2 Mock 模式 UI 文案规范；4) 明确 AllocationIntent 不直接下单产品规则；5) 9.5 里程碑增加子任务索引 | Songbin |
| v0.4 | 2026-05-10 | 定稿修订：1) 统一 v3.1 基线表述；2) 资产配置模块与投资行动 Tab 改为关联关系（仅在用户明确确认时回写长期目标）；3) Strategy 状态机说明 AllocationIntent 复用关系；4) 同步 LIMIT / CONDITIONAL_LIMIT 数据字段语义；5) 11.1 待确认问题转为 v3.2 默认业务决策（含"禁止累计超额"） | Songbin |
| v0.5 | 2026-05-10 | M3 启动前对齐：1) Skill 命名统一为 wp-action-planner（与现有 wp- 前缀一致）；2) actionable 判断 v3.2 改为硬规则（基于 decisionType），v3.3 升级 LLM 判断；3) ActionPlanner 不重新加载持仓，仅消费对话上下文；4) 明确 ActionPlanner 是 PEER 链路外的旁路调用，不进入 LLM Skill Selector；5) ActionDraft.payload 结构与 ActionListDraft 直接对齐（symbol_strategies / allocation_intents / risk_notes / missing_fields）；6) missing_fields 用户编辑页处理规则 | Songbin |
| v0.6 | 2026-05-10 | M4 冒烟测试反馈修订：1) ActionPlanner 改为"积极推算"模式，从对话上下文中提取或推算建议值预填字段，仅推算不出时放入 missing_fields；2) 非 actionable 场景按钮完全不显示（前端 actionable=true 才渲染按钮）；3) missing_fields 结构化为对象数组，每条带 strategy_index / field 标识，支持用户编辑后实时移除对应项；4) ActionDraftCard 视觉规格补充（弹层宽度、字段建议值标注） | Songbin |
| v0.7 | 2026-05-10 | 订单类型简化：v3.2 MVP 仅支持 LIMIT（普通限价单），不暴露 CONDITIONAL_LIMIT；ActionPlanner 推算结果统一为 LIMIT；前端 UI 不渲染 trigger_price 字段；CONDITIONAL_LIMIT 留待 v3.3 视 Tiger API 调研结论决定 | Songbin |

---

## 1. 产品定位与目标

### 1.1 当前定位

WealthPilot v3.1 当前是一个 **AI 驱动的个人投资决策工作台**，基于 v3.x Multi-Agent 架构，通过 PEER Agents（Planning / Executing / Expressing / Reviewing）协助用户完成投资分析、组合诊断、决策思考。

**当前局限**：决策与执行之间存在断层。用户在 WealthPilot 中完成理性分析后，仍需切换到券商 App 手动下单，过程中容易受到盘中情绪影响导致执行变形。

### 1.2 v3.2 升级后的定位

**从「AI 投研工作台」升级为「AI 投资决策与执行系统」**。

通过引入「投资行动」模块和券商 API 集成，将 WealthPilot 的产品边界从"想清楚"延伸到"做到位"，形成完整的决策-执行-复盘闭环。

### 1.3 核心价值主张

1. **理性决策的执行保真度**：通过条件单 + 提前规划，把盘前冷静分析的结果固化为不受盘中情绪影响的执行指令。
2. **释放盯盘时间**：用户不需要全天关注行情，预设条件单到位即触发执行。
3. **决策-行动-成交-复盘的可追溯链路**：每一笔交易都可以追溯到产生它的对话和决策依据，为 Reviewing Agent 提供真实弹药。
4. **人在回路（Human-in-the-Loop）的安全设计**：AI 不直接代客下单，所有交易必须经过人工最终确认才执行。

### 1.4 目标用户

- **核心用户**：有本职工作、不能全天盯盘、希望以系统化方式管理 US/HK/A 股投资的个体投资者。
- **典型画像**：金融科技从业者、互联网/科技公司中高级员工、投资金额 10 万–500 万人民币区间。
- **关键痛点匹配**：
  - 知道应该理性投资但盘中容易动摇 → 条件单解决
  - 没有时间盯盘 → 预设触发解决
  - 决策依据容易遗忘、复盘不易 → 决策-行动追溯链路解决

### 1.5 产品边界（必须明确）

**WealthPilot 不做什么**：

- ❌ 不做代客理财（不接受用户资金托管）
- ❌ 不做完全自动化的"AI 自动下单"（必须人工最终确认）
- ❌ 不做高频/算法交易（条件单触发逻辑由券商原生支持，WealthPilot 不自建撮合）
- ❌ **不自建价格监控与触发机制**（条件单一旦提交，由券商托管和监控触发，WealthPilot 不在本地循环监控价格）
- ❌ 不做投资建议销售（AI 输出是分析与建议，不是投顾产品）

**WealthPilot 是什么**：

- ✅ 用户授权下的工具型下单助手
- ✅ 决策与执行的衔接器
- ✅ 投资纪律的强化工具

### 1.6 部署边界（关键架构红线）

**v3.2 起，交易执行模块仅支持本地部署模式**。

具体含义：
- 真实交易 API 凭证只允许由用户本机的 FastAPI 进程读取
- 凭证不上传云端、不经过任何远程服务器
- 即使未来 WealthPilot 扩展为云端部署模式，**交易模块必须独立保留为本地组件**（本地 Agent / 本地网关）
- 云端服务可以承载分析、对话、记录展示等功能，但**禁止承载凭证存储与下单调用**

这条边界的目的是把"金融凭证泄露"的风险面收敛到用户本机，避免任何云端入侵都可能导致资金损失。

---

## 2. 核心用户故事与端到端流程

### 2.1 关键用户故事

**Story 1：从对话到行动**

> 作为一个用户，当我在投资决策模块和 AI 讨论完一个加仓/减仓决策后，我希望能一键把讨论结果转化为可执行的行动清单，避免我自己手动翻译"AI 说的话"成"我要做的事"。

**Story 2：人工最终确认**

> 作为一个用户，在每一笔交易实际下单到券商之前，我希望系统强制让我做一次最终人工确认，确保我对每一个动作都心里有数。

**Story 3：条件单托管**

> 作为一个用户，我希望我的策略一旦确认，就能以条件单形式托管在券商，不用我自己盯盘等价格。

**Story 4：决策追溯**

> 作为一个用户，我希望每一笔成交都能回溯到当初产生它的那次 AI 对话和决策依据，让复盘有事实依据。

**Story 5：资产配置驱动**

> 作为一个用户，当我需要做大类资产再平衡时，我希望系统帮我把"权益降 5 个点"这样的宏观意图，拆解为具体的标的卖出动作。

### 2.2 端到端流程图（文字描述）

```
[投资决策模块]
   用户与 PEER Agents 对话
   ↓
   AI 输出可执行结论时，自动高亮"生成行动清单"按钮
   ↓
[行动清单确认卡片]
   AI 预填行动项（资产配置调整 / 标的策略 / 单条订单）
   用户审阅、编辑、补充
   ↓
   点击"加入投资行动"
   ↓
[投资行动模块 - 待确认草稿]
   ActionDraft 状态 = draft
   ↓
   用户在投资行动页面对该项做"策略确认"
   ↓
[Strategy 激活]
   Strategy 状态 = active（已激活，待下单）
   ↓
   用户点击"提交至券商"或"立即下单"
   ↓
[最终下单确认弹窗]（关键安全节点，强制弹窗，5分钟超时）
   ↓
   用户点击"确认下单"
   ↓
[本地风控校验]
   通过 → 继续；失败 → 提示用户调整
   ↓
[Order 创建并提交券商]
   Order 状态 = created → submitted_to_broker
   调用 BrokerAdapter.place_order()
   ↓
[券商接收并托管条件单]
   Order 状态 = broker_pending
   订单托管在券商系统，由券商监控价格
   （WealthPilot 不在本地监控价格，只定时同步状态）
   ↓
[券商系统侧的状态变迁]
   市场达到触发价 → 券商执行 → 成交回报
   或：用户在 WealthPilot 撤单 → 调用 BrokerAdapter.cancel_order()
   或：超过订单有效期 → 券商自动作废
   ↓
[订单状态同步]
   Order 状态 = filled / partially_filled / cancelled / rejected / expired
   ↓
[投资行动模块 - 行动记录]
   订单结果回写关联 Strategy
   一个 Strategy 可对应多笔 Order（分批执行 / 撤单重挂）
   Strategy 在累计成交达到目标后状态 = completed
   ↓
   用户可基于成交结果与原始决策依据进行对比复盘
```

**关键设计原则**：

1. **券商托管触发**：条件单一旦提交，监控和触发由券商负责，WealthPilot 不在本地循环监控价格
2. **人工最终确认前置**：确认动作发生在"提交券商之前"，不是"触发成交之前"——这样用户确认完即可离场，无需盯盘
3. **策略与订单解耦**：一个策略可对应多笔订单（分批、撤单重挂），订单成交不直接等于策略完成

### 2.3 三层状态机设计

把原本混在一起的状态拆成三层独立状态机，各自管理不同生命周期对象。

#### 2.3.1 ActionDraft 状态机（行动清单草稿）

```
            [生成行动清单]
                  ↓
                draft
              /       \
        [确认]        [取消/超时]
            /             \
       confirmed       discarded
```

| 状态 | 说明 | 触发条件 |
|------|------|----------|
| `draft` | 草稿状态，AI 已生成等待用户处理 | 点击"生成行动清单"按钮 |
| `confirmed` | 已确认，转化为 Strategy 入库 | 用户在卡片上点击"加入投资行动"并完成审阅 |
| `discarded` | 已丢弃 | 用户取消，或 7 天未处理（MVP 不自动清理） |

`confirmed` 后，ActionDraft 不再活跃，所有后续状态变化由 Strategy 接管。

#### 2.3.2 Strategy 状态机（SymbolStrategy 主用；AllocationIntent 复用同套状态值）

```
       [ActionDraft.confirmed]
                ↓
              active
              /  |  \
       [暂停] [完成] [作废]
           /    |    \
       paused  completed  discarded
```

| 状态 | 说明 | 触发条件 |
|------|------|----------|
| `active` | 已激活，可被下单 | ActionDraft 确认后默认进入 |
| `paused` | 暂停，不可下单 | 用户主动暂停（市场异常时使用） |
| `completed` | 已完成 | 累计成交量达到策略目标（如计划减仓 1000 股，已累计成交 1000 股） |
| `discarded` | 已作废 | 用户主动作废（如改变投资判断） |

**关键设计**：
- `active` 状态下，可以触发 0 次或多次下单
- 下单失败、订单被拒、订单撤销，**Strategy 状态保持 active**，用户可重新挂单
- 只有累计成交达到目标，Strategy 才进入 `completed`

**SymbolStrategy 与 AllocationIntent 的状态使用差异**（重要）：

| 维度 | SymbolStrategy | AllocationIntent |
|------|---------------|------------------|
| 状态值 | active / paused / completed / discarded | 复用同套状态值 |
| 是否可创建 Order | ✅ 可以，是订单流程的入口 | ❌ 不可以，**AllocationIntent 不直接下单** |
| `completed` 触发条件 | 累计成交量达到 target_quantity | 关联的所有 SymbolStrategy 状态均为 completed |
| 调用 `place_order` API | ✅ 允许 | ❌ 禁止（API 层面校验拒绝） |

简而言之：AllocationIntent 表达"目标和方向"，SymbolStrategy 表达"具体动作"，只有后者能进入订单流程。详见 4.2 节"AllocationIntent 不直接下单"产品规则。

#### 2.3.3 Order 状态机（券商订单）

```
         [用户最终确认下单]
                ↓
             created
                ↓
        [调用券商 API]
                ↓
        submitted_to_broker
            /        \
   [API 失败]      [券商接收]
       /                \
    rejected      broker_pending
                       /  |  \  \
                  [触发] [撤单] [过期] [部分成交]
                     |      |      |        |
                  filled cancelled expired partially_filled
                                              |
                                       [继续/最终]
                                              ↓
                                       filled / cancelled

       [任意状态] → [API 不可达] → unknown
```

| 状态 | 说明 |
|------|------|
| `created` | 本地已创建，未提交券商（极短瞬态） |
| `submitted_to_broker` | 已调用券商 API，等待回执 |
| `broker_pending` | 券商已接收并托管，等待触发或市场到价 |
| `partially_filled` | 部分成交（港股/A 股流动性差时常见） |
| `filled` | 全部成交 |
| `cancelled` | 已取消（用户主动 或 券商主动） |
| `rejected` | 被券商拒单（资金不足、合规校验失败等） |
| `expired` | 超过订单有效期，券商自动作废 |
| `unknown` | 网络/API 故障，本地无法确认券商侧状态 |

**关于 `unknown` 状态**（重要）：

当出现以下情况时，订单进入 `unknown`：
- 调用券商 API 后未收到回执（超时）
- 状态轮询失败超过 N 次
- 券商系统维护期间

`unknown` 不是错误，是诚实表达——**WealthPilot 不假装知道结果**。
进入 `unknown` 后，UI 强提示用户：建议直接登录券商 App 核实，并提供"手动同步"按钮。

#### 2.3.4 三层状态机的关系

```
ActionDraft (1) ─→ confirmed ─→ Strategy (1)
                                     │
                                     ├─→ Order (N)  ← 一个 Strategy 可对应多笔 Order
                                     │     ├─ 第1笔：filled
                                     │     ├─ 第2笔：cancelled
                                     │     └─ 第3笔：partially_filled
                                     │
                                     └─→ 累计成交达标 → completed
```

这种拆分能正确表达以下场景：
- 计划减仓 1000 股，第一笔挂 500 股全部成交，第二笔挂 500 股部分成交 300 股，剩余 200 股撤单后再挂——Strategy 始终 active，直到累计 1000 股成交才 completed
- 挂单后市场变化撤单，Strategy 不需要重新创建，可直接重新触发下单
- 网络故障导致订单状态 unknown，Strategy 状态保持 active 不受影响

---

## 3. 信息架构与页面设计

### 3.1 整体导航变化

v3.2 只做**最小侵入**：将"投资记录"重命名为"投资行动"，其余导航完全不动。

实际导航结构（基于 v3.1 现状，v3.2 仅改动标注处）：

```
投资规划
├── 用户画像和投资目标         （不变）
├── 资产配置                  （不变）
├── 投资账户总览               （不变）
├── 投资纪律                  （不变）
├── 投研观点                  （不变）
├── 投资决策                  （不变，页面内新增"生成行动清单"按钮）
├── 投资记录  ──►  投资行动    ← v3.2 唯一导航改动：重命名 + 功能全面升级
│   ├── 资产配置（Tab 1）
│   ├── 标的策略（Tab 2）
│   └── 行动记录（Tab 3）
└── 收益分析                  （不变）

财务规划                      （不变）
资产负债总览                   （不变）
```

**改动范围**：导航只动一处——"投资记录"→"投资行动"。投资决策的导航入口不动，仅页面内增加按钮。其余所有导航项、分组、顺序完全保持现状，本 PRD 不涉及其他导航的调整。


### 3.2 投资决策模块改动

**最小改动**：在 AI 回复消息卡片底部新增「生成行动清单」按钮。

#### 3.2.1 按钮交互逻辑（v0.6 修订：仅 actionable 场景显示）

| 状态 | 视觉 | 提示文案 | 触发条件 |
|------|------|----------|----------|
| **不显示** | 无按钮 | — | Expressing Agent 输出 `actionable: false`（v0.6 修订） |
| 高亮 | 主色高亮 + 角标 | "AI 检测到 N 项可执行行动，点击查看"（优先用 actionable_hint） | Expressing Agent 输出 `actionable: true` |
| 已生成 | 完成态 | "已加入投资行动 →" | 用户已点击并确认（草稿入库后） |

**关键变更（v0.6）**：之前版本设计为"按钮始终显示，actionable 控制是否高亮"，但冒烟测试发现：在非 actionable 场景（如用户问"现在市场怎么样"，被识别为 Education 意图）下出现按钮会让用户困惑（"为什么市场咨询也能生成行动清单？"）。

**v0.6 修订**：前端 AiMessage 组件改为 `{actionable && <ActionListGenerateButton />}`，actionable=false 时按钮不渲染。

#### 3.2.2 actionable 判断逻辑（轻量信号）

为了避免 Expressing Agent 和 ActionPlanner 职责重复，采用"轻量信号 + 按需触发"模式：

**Expressing Agent 在每次回复时附加轻量信号**（不生成完整行动清单）：

```python
@dataclass
class ExpressingOutput:
    answer: str
    actionable: bool                    # 本次回复是否包含可执行决策
    actionable_hint: Optional[str]      # 可读提示，如"3 项可执行行动"
```

**判断标准（v3.2 用硬规则，v3.3 升级 LLM 判断）**：

v3.2 阶段采用**硬规则判断**，基于 Expressing Agent 已输出的结构化字段 `decisionType`：

```python
# v3.2 硬规则示例
ACTIONABLE_DECISION_TYPES = {
    "buy_init",     # 初次建仓
    "buy_more",     # 加仓
    "trim",         # 减仓
    "exit",         # 清仓
}

def is_actionable(expressing_output) -> bool:
    return expressing_output.decisionType in ACTIONABLE_DECISION_TYPES
```

判断标准（语义上）：
- 对话中包含明确的买入/卖出/调仓建议
- 建议带有具体标的、价格区间或仓位变化
- 用户在对话中表达了"决定执行"的意图（如"那就这么做"、"按你说的来"等）

**v3.2 选择硬规则的理由**：
- 可靠性高：基于已有结构化字段，零误判风险
- 成本零：不增加 LLM 调用
- 延迟零：不影响对话流畅性
- 可审计：判断逻辑显式可读，便于调试

**v3.3+ 升级 LLM 判断**：v3.2 上线后观察实际"硬规则 vs 用户实际意图"的偏差率。如果发现硬规则覆盖不全（如用户口语化表达"那就这样吧"未被识别），再切换到 LLM 判断。届时切换只需修改 `is_actionable` 函数实现，调用方不变。

**ActionPlanner Skill 仅在用户点击按钮时被调用**（重活在这里做）：

- 输入：完整对话上下文 + Expressing Agent 输出
- 输出：结构化 `ActionListDraft`（详见 7.2 节）
- 关键设计：**按需触发**，不在每轮对话中运行，节省 token 与延迟

这种设计的好处：
- Expressing 只做判断（"值不值得点按钮"），延迟极低
- ActionPlanner 只做翻译（"对话→结构化行动"），用户主动触发才运行
- 两者职责清晰，调试与优化路径独立

### 3.3 行动清单确认卡片（弹层）

#### 3.3.1 卡片结构

```
┌─────────────────────────────────────┐
│ 📋 行动清单（基于本次对话）            │
├─────────────────────────────────────┤
│ ▼ 资产配置调整                       │
│   [可编辑] 权益类：45% → 40%        │
│   [可编辑] 固收类：30% → 35%        │
├─────────────────────────────────────┤
│ ▼ 标的策略                          │
│   [可编辑] LI: 减仓 50%              │
│     触发：股价 ≥ $32                │
│     订单类型：限价卖出               │
│   [+ 添加标的]                      │
├─────────────────────────────────────┤
│ 💡 决策依据                          │
│   [自动填充自本次对话摘要]           │
├─────────────────────────────────────┤
│   [取消]              [加入投资行动] │
└─────────────────────────────────────┘
```

#### 3.3.2 卡片字段说明

| 字段 | 是否必填 | 编辑权限 | 说明 |
|------|----------|----------|------|
| 资产配置调整列表 | 否 | 可编辑/可删除/可添加 | 大类资产比例变化 |
| 标的策略列表 | 否 | 可编辑/可删除/可添加 | 具体买卖动作 |
| 关联意图 | 否 | 自动 | 如果标的策略来自资产配置调整，自动建立 parent_intent_id 关联 |
| 决策依据 | 是 | 可编辑 | 自动从对话生成摘要，用户可补充 |
| 关联对话 ID | 是 | 自动 | 系统自动记录，用于追溯 |

#### 3.3.3 视觉规格（v0.6 补充）

| 项 | 规格 |
|---|------|
| 弹层宽度 | **800-900px**（PRD 默认 600px 太窄，决策依据 textarea 显示不全） |
| 弹层最大高度 | viewport height 的 80%，超出滚动 |
| 决策依据 textarea | 至少 4 行高度 + 自动扩展 |
| AI 推算字段标注 | 字段右侧或下方显示 "💡 AI 建议：基于 [value_source 内容]"，灰色小字 |
| 缺失字段视觉 | 输入框红色边框（border: 1px solid #ef4444）+ 字段下方红色提示文案 |
| 已补齐字段视觉 | 输入框恢复正常边框 + 提示文案消失（实时反馈） |
| 确认按钮置灰逻辑 | `missing_fields.length === 0` 时可点；否则置灰 + hover 提示"请先补齐 N 项缺失字段" |

#### 3.3.4 missing_fields 实时校验逻辑（v0.6 新增）

前端通过 MissingField 的 `target_type + target_index + field` 三元组定位 UI 输入框：

```typescript
// 用户编辑某个字段时
function onFieldChange(targetType, targetIndex, fieldName, newValue) {
  // 1. 更新策略数据
  updateStrategy(targetType, targetIndex, fieldName, newValue);
  
  // 2. 如果新值非空且非零，从 missing_fields 中移除对应项
  if (newValue && newValue !== 0) {
    setMissingFields(prev => prev.filter(mf => 
      !(mf.target_type === targetType && 
        mf.target_index === targetIndex && 
        mf.field === fieldName)
    ));
  }
}

// 确认按钮可点判断
const canConfirm = missingFields.length === 0;
```

**后端兜底校验**：即使前端校验通过，POST `/api/action/drafts/{id}/confirm` 在后端再校验一次 `payload.missing_fields` 是否为空，非空则返回 HTTP 422。这是双层防护，避免前端校验被绕过。

### 3.4 投资行动模块（新建主页面）

#### 3.4.1 顶层结构

页面采用**三 Tab 横向切换**结构：

```
┌──────────────────────────────────────────────┐
│  [资产配置]    [标的策略]    [行动记录]       │
├──────────────────────────────────────────────┤
│                                              │
│         （根据当前 Tab 显示对应内容）         │
│                                              │
└──────────────────────────────────────────────┘
```

页面顶部固定一个**"待处理草稿"**提示条（如果存在 `draft` 状态的 ActionDraft）：

```
⚠️ 你有 2 项待确认的行动清单 [立即查看]
```

#### 3.4.2 Tab 1：资产配置

**页面分区**：

```
┌─ 当前配置 vs 目标配置 ─────────────────────┐
│  权益类  ████████░░ 45%（目标 40%，超配 5%）│
│  固收类  ██████░░░░ 30%（目标 35%，欠配 5%）│
│  ...                                        │
├─ 待执行调整意图 ───────────────────────────┤
│  📌 [意图1] 权益类降至 40%                  │
│      创建时间：2026-05-10                  │
│      关联对话：[财报后调仓讨论]            │
│      关联标的策略：3 项（已展开 →）        │
│      [编辑] [作废]                         │
├─ 历史调整记录 ─────────────────────────────┤
│  ✅ 2026-04-12 完成：权益从 50% 降至 45%   │
│  ...                                        │
└────────────────────────────────────────────┘
```

#### 3.4.3 Tab 2：标的策略

**页面分区**：

```
┌─ 持仓标的策略 ────────────────────────────┐
│  📊 LI（理想汽车）当前持仓 1000 股         │
│     ▸ 减仓策略：股价 ≥$32 时卖 500 股      │
│       状态：active | 订单未挂              │
│       [立即下单] [取消]                   │
│     ▸ 加仓策略：股价 ≤$25 时买 200 股      │
│       状态：active | 订单未挂              │
│  📊 MEITUAN（美团）...                    │
├─ 观察标的策略 ────────────────────────────┤
│  👀 NVDA: 等待财报后判断                  │
│     ▸ 暂未设置具体策略                    │
├─ 已挂单 ──────────────────────────────────┤
│  🟡 LI 限价卖 500 股 @$32（已挂老虎）     │
│     [查看券商订单 →] [本地取消]           │
└──────────────────────────────────────────┘
```

#### 3.4.4 Tab 3：行动记录

**页面分区**：

```
┌─ 时间轴视图 ──────────────────────────────┐
│  2026-05-10                               │
│   📝 09:30 创建意图：权益类降至 40%        │
│   📝 09:45 创建标的策略：LI 减仓 500 股    │
│   ✅ 14:23 成交：LI 卖出 500 股 @$32.05    │
│           ⤷ 关联意图：[权益类降至 40%]    │
│           ⤷ 关联决策：[与AI讨论财报]      │
│  2026-05-09                               │
│   ✅ ...                                  │
├─ 复盘标记 ────────────────────────────────┤
│  本周成交：3 笔 | 总金额：¥18,250         │
│  [生成本周复盘报告 →]                      │
└──────────────────────────────────────────┘
```

行动记录 Tab 的核心价值在于**追溯**：每一笔成交都能向上追溯到决策意图和原始对话，为复盘提供完整证据链。

---

## 4. 功能模块拆解

### 4.1 模块 A：行动清单生成

**输入**：投资决策模块的对话上下文 + Expressing Agent 输出
**输出**：结构化行动清单草稿

**子功能**：

| 子功能 | 描述 |
|--------|------|
| 4.1.1 actionable 判断 | Expressing Agent 输出 actionable 标记 |
| 4.1.2 草稿预填 | AI 自动填充行动项字段 |
| 4.1.3 卡片编辑 | 用户审阅、编辑、添加、删除行动项 |
| 4.1.4 决策依据摘要 | 自动从对话生成 200 字以内摘要 |
| 4.1.5 草稿入库 | 点击"加入投资行动"写入数据库 |

### 4.2 模块 B：资产配置行动管理

**子功能**：

| 子功能 | 描述 |
|--------|------|
| 4.2.1 当前 vs 目标对比 | 计算偏离度，可视化展示 |
| 4.2.2 调整意图 CRUD | 创建、编辑、作废资产配置调整意图 |
| 4.2.3 标的策略关联 | 显示关联到该意图的所有标的策略 |
| 4.2.4 完成度计算 | 已成交标的策略 / 计划标的策略 |
| 4.2.5 自动拆解（可选，v3.3+） | AI 根据资产配置调整意图，建议拆解为哪些标的动作 |

**关键产品规则：AllocationIntent 不直接下单**

> AllocationIntent 只表达**资产配置层面的目标意图**（例如"权益类降至 40%"），它本身不直接生成 Order，也不直接出现在订单流程中。
> 
> 资产配置调整意图必须经由用户拆解或 AI 辅助拆解，转化为一个或多个 SymbolStrategy 后，才能进入"提交券商"的订单创建流程。
> 
> AllocationIntent 的完成度由其关联的所有 SymbolStrategy 聚合计算：当所有关联 SymbolStrategy 状态均为 `completed` 时，AllocationIntent 自动转为 `completed`。
> 
> 这条规则的作用是把资产配置层和交易执行层解耦——资产配置层管"目标和方向"，标的策略层管"具体动作"，订单层管"实际执行"，三层职责清晰，不互相越位。

### 4.3 模块 C：标的策略管理

**子功能**：

| 子功能 | 描述 |
|--------|------|
| 4.3.1 策略 CRUD | 创建、编辑、作废标的策略 |
| 4.3.2 触发条件配置 | v3.2 MVP：仅支持价格触发（限价条件单）；时间触发、组合触发留待 v3.4+ |
| 4.3.3 立即下单 | 用户手动点击"立即下单"，跳过价格触发直接挂单 |
| 4.3.4 与持仓联动 | 仓位百分比自动转换为股数 |
| 4.3.5 风险提示 | 单笔金额超过总资产 X% 时强提示 |

### 4.4 模块 D：人工最终确认（关键安全模块）

**核心逻辑**：在每一次实际向券商提交订单前，强制执行一次确认流程。

**关键时机说明**：人工最终确认发生在 **"用户主动发起下单 → 调用券商 API"** 之间，**不是**"市场价格触发 → 成交"之间。

这意味着：
- 用户确认完成的是"是否提交这张条件单到券商"，不是"是否成交"
- 一旦提交完成，后续的价格监控和触发由券商完成，用户无需盯盘
- 这正是产品价值的核心：**确认即放手**

#### 4.4.1 确认流程

```
[Strategy.status = active] → [用户点击"提交至券商"或"立即下单"]
                                          ↓
                         弹出"最终下单确认"对话框（不可跳过）
                                          ↓
              ┌──────────────────────────────────────┐
              │ ⚠️ 最终下单确认                        │
              │                                      │
              │ 标的：LI（理想汽车）                  │
              │ 方向：卖出                           │
              │ 数量：500 股                         │
              │ 订单类型：限价                        │
              │ 限价：$32.00（条件单：≥$32 触发）    │
              │ 预计金额：$16,000.00                 │
              │ 有效期：GTC（直到取消）              │
              │ 券商：老虎证券（账户尾号 ****1234）   │
              │                                      │
              │ 决策依据：                           │
              │ [本次卖出来自2026-05-08财报讨论...]  │
              │                                      │
              │ ⚠️ 提交后由券商托管。市场达到触发    │
              │    价时由券商自动执行，无需您盯盘。    │
              │                                      │
              │ [取消]            [确认提交]         │
              └──────────────────────────────────────┘
                                          ↓
                              用户点击"确认提交"
                                          ↓
                              [本地风控校验]
                                          ↓
                              通过 → 调用 BrokerAdapter.place_order()
                              失败 → 提示用户调整参数
                                          ↓
                              Order 状态：created → submitted_to_broker
                                          ↓
                              收到券商回执
                                          ↓
                              Order 状态：→ broker_pending
```

#### 4.4.2 确认机制约束

- **不可跳过**：即使用户配置了"快速下单"偏好，最终弹窗仍强制显示
- **超时自动取消**：弹窗显示 5 分钟内未操作，自动取消该次下单尝试，Strategy 状态保持 active
- **二次防护**：弹窗按钮"确认提交"需要用户点击，不接受 Enter 键直接确认（避免误触）
- **行为审计**：所有确认/取消动作记录到审计日志，包含完整订单参数快照

### 4.5 模块 E：券商 API 集成（详见第 5 节）

### 4.6 模块 F：行动记录与复盘

**子功能**：

| 子功能 | 描述 |
|--------|------|
| 4.6.1 时间轴展示 | 按时间倒序展示所有行动事件 |
| 4.6.2 状态同步 | 定时轮询券商订单状态，更新本地状态 |
| 4.6.3 决策追溯 | 每条记录可点击跳转到原始对话 |
| 4.6.4 复盘报告（v3.3+） | Reviewing Agent 基于成交数据生成复盘 |
| 4.6.5 数据导出 | 导出 CSV / Excel 用于个人税务整理 |

---

## 5. 券商 API 集成方案

### 5.1 v3.2 MVP 阶段：Mock BrokerAdapter

**v3.2 不接入真实交易 API**。先用 Mock Adapter 跑通端到端流程，让前端交互、数据模型、状态机先稳定。

**Mock Adapter 行为**：
- 实现完整的 BrokerAdapter 接口
- 模拟订单状态变迁（submitted_to_broker → broker_pending → filled / partially_filled）
- 模拟各种异常场景（rejected / unknown / 网络超时）
- 用本地 SQLite 存储 mock 订单状态
- UI 上以醒目标记区分 Mock 模式和真实交易

**v3.2 用 Mock 的价值**：
- 前端可以演示完整的产品形态（面试时可直接 demo）
- 数据模型和状态机可在不依赖外部系统的情况下充分验证
- 风控、审计逻辑可以在受控环境下测试到位
- v3.3 接入 Tiger 时，只需替换 Adapter，业务层逻辑无需改动

**v3.2 Mock 模式 UI 文案规范**（避免误导）：

| 位置 | v3.2 Mock 模式文案 | v3.3 真实模式文案 |
|------|-------------------|-------------------|
| 全局顶部横幅 | 🟡 当前为 Mock 模式，所有交易均为模拟，不会产生真实订单 | （无横幅） |
| 提交订单按钮 | 模拟提交 / 提交至 Mock 券商 | 提交至券商 / 立即下单 |
| 最终确认弹窗标题 | ⚠️ 模拟下单确认（Mock 模式） | ⚠️ 最终下单确认 |
| 弹窗确认按钮 | 确认模拟提交 | 确认提交 |
| 订单状态展示 | 标签前加 `[Mock]` 前缀，配色与真实订单区分 | 正常展示 |
| 行动记录中的成交 | "模拟成交"，背景色与真实成交不同 | "成交" |
| 券商账户绑定页 | 显示"Mock 券商（演示用）"作为唯一可用项 | 显示已绑定的真实券商列表 |

**关键原则**：v3.2 模式下，任何按钮、弹窗、状态标签都不应让用户（或面试演示对象）误以为正在进行真实交易。即使是 demo 演示场景，也以"诚实表达"为先——这本身就是产品的安全设计能力体现。

### 5.2 v3.3 阶段：老虎证券（TigerOpen API）

**选择理由**：
- 官方 API 文档完善，社区支持成熟
- 个人开户即可申请使用
- 不需要本地常驻进程（区别于富途 OpenD）
- Python SDK 成熟（`tigeropen`）

**v3.3 的范围**：
- 实现 TigerBrokerAdapter
- 仅支持限价单（LIMIT）一种订单类型
- 接入老虎沙盒环境联调通过后，进入小额真实订单测试
- 通过测试后才发布到 release

**为什么 MVP 仅限价单？**

止损单和止盈单虽然在金融意义上有价值，但：
- 老虎对不同市场（美股/港股/A 股）的止损单支持参数差异较大，每多一种类型就要做一轮适配
- 限价单是最常用的订单类型，能覆盖 80% 以上场景
- MVP 阶段先把"决策→限价条件单"这条主路径走顺，比铺开订单类型更重要

### 5.3 BrokerAdapter 抽象层

为了未来扩展富途、国金，从 v3.2 起就建立统一抽象层。

#### 5.3.1 接口定义

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional
from decimal import Decimal

@dataclass
class OrderRequest:
    """统一下单请求"""
    symbol: str          # 标的代码（统一格式：US.LI / HK.03690 / CN.601318）
    side: str            # "BUY" | "SELL"
    quantity: int
    order_type: str      # v3.2 Mock：LIMIT / CONDITIONAL_LIMIT；v3.3 Tiger：以调研结论为准；v3.3+：扩展 STOP_LOSS / STOP_PROFIT / MARKET
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None  # MVP 不使用
    time_in_force: str = "GTC"  # "DAY" | "GTC"
    
@dataclass
class OrderStatus:
    """统一订单状态"""
    broker_order_id: Optional[str]
    local_order_id: str
    status: str          # 对应 Order 状态机的状态值
    filled_quantity: int
    avg_filled_price: Optional[Decimal]
    timestamp: int
    raw_response: dict   # 保留券商原始返回，便于调试

class BrokerAdapter(ABC):
    """券商适配器抽象基类"""
    
    @abstractmethod
    def authenticate(self, credentials: dict) -> bool: ...
    
    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderStatus: ...
    
    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool: ...
    
    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> OrderStatus: ...
    
    @abstractmethod
    def list_open_orders(self) -> List[OrderStatus]: ...
    
    @abstractmethod
    def get_positions(self) -> List[dict]: ...
    
    @abstractmethod
    def get_account_info(self) -> dict: ...
```

#### 5.3.2 Mock Adapter 实现要点（v3.2）

```python
class MockBrokerAdapter(BrokerAdapter):
    def __init__(self):
        self.orders = {}  # 本地 SQLite 持久化
        self.simulation_config = {
            "fill_delay_seconds": 5,           # 模拟成交延迟
            "partial_fill_probability": 0.1,   # 10% 概率部分成交
            "rejection_probability": 0.02,     # 2% 概率被拒
            "network_failure_probability": 0.01,  # 1% 概率进入 unknown
        }
    
    def place_order(self, request: OrderRequest) -> OrderStatus:
        # 模拟券商接收订单 → 进入 broker_pending
        # 异步模拟价格触发 → 状态变迁
        ...
```

#### 5.3.3 老虎证券 Adapter 实现要点（v3.3）

```python
class TigerBrokerAdapter(BrokerAdapter):
    def __init__(self, credential_provider):
        self.credential_provider = credential_provider
        self.client = None  # tigeropen.trade.trade_client，按需创建
    
    def _get_client(self):
        # 每次操作时从 CredentialProvider 取凭证，用完即丢
        creds = self.credential_provider.load()
        return TradeClient(...)
    
    def place_order(self, request: OrderRequest) -> OrderStatus:
        # 注意：老虎的 OrderType 与统一接口的映射
        # MVP 仅处理 LIMIT
        ...
```

### 5.4 凭证管理（关键安全设计）

#### 5.4.1 架构

```
React 前端
   │ HTTPS（仅本机环回，127.0.0.1）
   ↓
本地 FastAPI 后端
   │ Python keyring 库
   ↓
macOS Keychain / Windows Credential Manager / Linux Secret Service
   │
   ↓
[BrokerAdapter] 使用凭证调用券商 API
```

#### 5.4.2 关键约束

- **前端不接触凭证**：浏览器前端不能直接访问 macOS Keychain；凭证只在本地后端进程内存中短暂出现
- **CredentialProvider 单点封装**：所有 BrokerAdapter 通过 `CredentialProvider.load(broker_name)` 获取凭证，不允许各 Adapter 自行处理
- **用完即清理**：单次 API 调用结束后立即清理内存中的凭证副本
- **首次绑定流程**：用户在本地 UI 中输入凭证 → 前端 POST 到本地后端 `/api/v1/credentials/bind` → 后端立即写入 keyring，不持久化到任何文件 / 数据库 / 日志
- **不上传云端**：即使未来 WealthPilot 的对话和分析功能云端化，交易模块必须保留为本地组件

### 5.5 富途和国金证券（v3.3+ 接入）

| 券商 | 接入方式 | 主要难点 |
|------|----------|----------|
| 富途 OpenD | 本地 OpenD 网关 + futu-api SDK | 需要常驻 OpenD 进程，对用户使用门槛要求略高 |
| 国金证券 QMT | miniQMT 本地客户端 | Mac 远程连接需向迅投确认（联系迅投支持 QQ 810315303） |

### 5.6 订单语义说明

WealthPilot 在金融语义上区分两类订单：

| 概念 | 行为 | 何时成交 |
|------|------|----------|
| **普通限价单（Plain Limit Order）** | 提交后直接挂单 | 市场价格满足限价条件即可成交，与提交时的市价对比无前置条件 |
| **条件限价单（Conditional Limit Order）** | 提交后等待触发条件 | 触发条件满足后，才转为活跃限价单挂出，再等待成交 |

**WealthPilot 的"券商托管触发"原则**：
- 无论是普通限价单还是条件限价单，触发与撮合**全部由券商完成**
- WealthPilot 不在本地循环监控价格，不在本地判断"何时该挂单"

**v3.2 MVP 范围（v0.7 简化）**：

> **v3.2 仅支持 LIMIT（普通限价单），不暴露 CONDITIONAL_LIMIT。**
> 
> 理由：
> 1. v3.2 是 Mock 阶段，先把核心闭环（决策→草稿→确认→入库）跑通，订单类型细节先不暴露
> 2. 普通限价单完全覆盖个人投资者主要场景：用户场景"反弹到 20.5 卖出"，挂个 20.5 限价单等成交即可，无需先等价格反弹再挂单
> 3. CONDITIONAL_LIMIT 在产品上对普通用户认知成本较高（"条件单 vs 限价单"区分需要金融知识）
> 4. v3.3 Tiger API 调研后再决定是否暴露给用户：如果 Tiger 真支持券商侧条件触发且对用户有明显价值，再加 UI；否则保持 LIMIT 一种类型
> 
> **具体实现要求**：
> - ActionPlanner 推算结果统一为 `order_type: "LIMIT"`，prompt 中不输出 CONDITIONAL_LIMIT
> - SymbolStrategyDraft 的 `trigger_price` 字段在 v3.2 始终为 None（保留字段但不使用）
> - 前端 ActionDraftCard 不渲染 trigger_price 输入框，UI 只显示数量、限价两个字段
> - 前端订单类型标签固定显示"限价单"

**v3.3 启动硬性前置条件**：

> **在 v3.3 开发启动前，必须完成 TigerOpen 订单能力调研**，明确以下问题的答案：
> 1. TigerOpen 在美股 / 港股 / A 股下，普通 LIMIT 订单和条件触发型 LIMIT 订单的实际支持情况
> 2. GTC（Good-Till-Cancelled）有效期在各市场的支持情况
> 3. 条件触发型订单的触发参数（触发价、触发方向）支持范围
> 
> 调研结论将决定 v3.3 是否引入 CONDITIONAL_LIMIT。如果 TigerOpen 不支持或对用户价值不足，CONDITIONAL_LIMIT 可能延后到 v3.4+ 或永久不实现。

### 5.7 订单类型映射表

| WealthPilot 订单类型 | 老虎证券对应 | MVP 是否实现 |
|-----------------------|-------------|--------------|
| LIMIT（普通限价） | LMT | ✅ v3.2（Mock）/ v3.3（Tiger，前提是调研确认） |
| CONDITIONAL_LIMIT（条件限价） | 待 Tiger API 调研确认 | ❌ v3.2 不实现；v3.3 视调研结论决定（v0.7 修订） |
| STOP_LOSS | STP | ❌ v3.3+ |
| STOP_PROFIT | LIT | ❌ v3.3+ |
| MARKET | MKT | ❌ 不计划支持（个人投资者场景下风险大于价值） |

**MVP 阶段不实现**的订单类型：

- ❌ AI 监控触发型（如"跌破 MA20 时下单"）— 由 WealthPilot 自己监控价格再调用 API，技术风险高，且违背"券商托管触发"原则
- ❌ 算法单（TWAP / VWAP）— 个人用户场景下价值有限
- ❌ 期权 / 期货单 — 范围之外

---

## 6. 安全与合规设计

### 6.1 凭证安全

| 措施 | 描述 |
|------|------|
| 仅本地部署 | 交易模块只在用户本机运行，凭证不上云端 |
| 本地后端访问 | 仅本地 FastAPI 进程通过 keyring 读取 macOS Keychain |
| 前端不接触 | 浏览器前端不直接访问 Keychain，只通过 127.0.0.1 调用本地后端 |
| 不入库 | 数据库不存储任何券商凭证字段 |
| 不入日志 | 所有日志记录脱敏，不记录密钥、token、签名材料 |
| 内存最小化 | 后端处理完订单后立即清理凭证 |
| 强制 HTTPS | 前后端通信全程 HTTPS（本机环回也用 self-signed cert） |

### 6.2 人工把关

| 控制点 | 描述 |
|--------|------|
| 行动清单确认 | 决策→行动入库需要用户主动点击 |
| 最终下单确认 | 每笔订单提交券商前必须人工确认 |
| 不可跳过 | 即使用户在配置里勾选"自动模式"，本环节仍不能跳过 |
| 超时取消 | 确认弹窗 5 分钟无响应自动取消 |

### 6.3 风控规则（本地）

WealthPilot 在向券商提交前做一层本地风控校验：

| 规则 | 阈值（默认） | 用户可配置 |
|------|-------------|------------|
| 单笔订单金额上限 | 总资产 × 20% | ✅ |
| 单日累计下单金额上限 | 总资产 × 30% | ✅ |
| 异常价格保护 | 限价偏离当前市价 > 5% 时强提示 | ✅ |
| 重复订单检测 | 5 分钟内相同标的相同方向重复下单时提示 | ❌（强制） |
| 黑名单标的 | 用户可配置不允许通过 WealthPilot 下单的标的 | ✅ |

### 6.4 审计日志

所有关键事件入审计日志（独立于业务日志）：

- 用户认证事件
- 行动清单生成、确认、取消
- 订单提交、确认、取消
- 凭证加载、清理（不记录凭证内容）
- 风控规则触发

审计日志要求：
- 不可篡改（append-only）
- 保留至少 12 个月
- 用户可在用户中心查看自己的审计记录

### 6.5 合规边界声明

WealthPilot 在用户中心和重要操作页面需明示：

> **WealthPilot 是用户授权下的辅助工具。所有交易决策由用户独立做出并最终确认。WealthPilot 不提供投资建议，不承诺收益，不承担因用户决策产生的投资损失。**

---

## 7. 与 v3.0 PEER Agents 的集成

### 7.1 架构层面

v3.2 在 v3.0 PEER 架构基础上，新增两类组件：

```
┌─────────────────────────────────────────────────────┐
│                    PEER Agents (v3.0)                │
│   Planning → Executing → Expressing → Reviewing      │
└─────────────────────────────────────────────────────┘
                          ↓ (新增 actionable 元数据)
┌─────────────────────────────────────────────────────┐
│             ActionPlanner（新增 Agent / Skill）      │
│      把对话翻译成结构化的 ActionItemDraft           │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│              OrderManager（新增模块）                │
│      管理行动项 / 订单生命周期 / 状态同步           │
└─────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────┐
│           BrokerAdapter（新增抽象层）                │
│       Tiger / Futu / GJZQ / Mock 实现               │
└─────────────────────────────────────────────────────┘
```

### 7.2 ActionPlanner Skill（按需触发）

**SKILL.md 路径**：`skills/wp-action-planner/SKILL.md`（统一 `wp-` 前缀，与现有 10 个 Skill 命名规范对齐）

**实际代码位置**：`backend/services/action/action_planner.py`（与 OrderManager 同模块，便于维护）

**职责**：把对话上下文翻译为结构化的行动清单草稿。

**调用时机**：**仅当用户点击"生成行动清单"按钮时**被调用，不在每轮对话中运行。

**输入**：
- `conversation_id`：当前对话 ID
- `conversation_context`：最近 N 轮对话历史
- `expressing_output`：Expressing Agent 最近一次输出（含 actionable_hint）

**输出**：

```python
@dataclass
class ActionListDraft:
    conversation_id: str
    decision_summary: str                          # 200 字内的决策依据摘要
    allocation_intents: List[AllocationIntentDraft] # 资产配置调整意图（可为空）
    symbol_strategies: List[SymbolStrategyDraft]   # 标的策略（可为空）
    risk_notes: List[str]                          # AI 识别的风险提示（如"超出单笔限额"）
    missing_fields: List[MissingField]             # 结构化缺失字段（v0.6 改造：对象数组）

@dataclass
class MissingField:
    """结构化缺失字段（v0.6 新增）"""
    target_type: str                        # "symbol_strategy" / "allocation_intent"
    target_index: int                       # 在 symbol_strategies / allocation_intents 数组中的索引
    field: str                              # 字段名，如 "quantity" / "limit_price" / "trigger_price"
    description: str                        # 给用户看的文案，如 "减仓数量未明确"

@dataclass
class AllocationIntentDraft:
    title: str                              # "权益类降至 40%"
    target_allocation: Dict[str, Decimal]   # {"equity": 0.40, ...}
    
@dataclass
class SymbolStrategyDraft:
    symbol: str
    side: str                               # BUY / SELL
    quantity: Optional[int]
    quantity_pct: Optional[Decimal]
    order_type: str                         # LIMIT / CONDITIONAL_LIMIT（v3.2 Mock 支持两类，v3.3 视 Tiger API 调研结论）
    trigger_price: Optional[Decimal]        # CONDITIONAL_LIMIT 必填；LIMIT 可为空
    limit_price: Optional[Decimal]          # 最终挂单限价
    parent_intent_index: Optional[int]      # 关联到 allocation_intents 的索引
    value_sources: Optional[Dict[str, str]] # v0.6 新增：每个被推算填充字段的依据
                                            # 如 {"quantity": "基于目标仓位 15% 推算", "limit_price": "对话中提到的反弹目标价 20-21 美元中位数"}
```

**MissingField 设计示例**：

```json
{
  "missing_fields": [
    {
      "target_type": "symbol_strategy",
      "target_index": 0,
      "field": "trigger_price",
      "description": "条件单触发价未明确，请补充"
    }
  ]
}
```

前端通过 `target_type + target_index + field` 三元组定位到具体的 UI 输入框，用户填写该字段后立即从 missing_fields 列表移除该条。

**关键字段说明**：
- `risk_notes`：让用户在确认前看到 AI 识别的潜在风险，但不阻断流程
- `missing_fields`：仅当 AI **从对话上下文中实在推算不出**某字段时，才放进此列表（详见下方"积极推算"原则）

**关键设计原则**：

1. **积极推算模式（v0.6 修订）**：ActionPlanner 应当 **从对话上下文中积极提取或推算建议值**，预填到 `quantity` / `limit_price` / `trigger_price` 等字段。**不允许**因为对话没有逐字明说"减 500 股"就把 quantity 留空——只要决策依据中有"目标仓位降至 15%"、"分 2-3 批"、"价格反弹接近 20-21 美元"等信息，AI 就应该基于这些信息推算合理建议值。

   **推算原则**：
   - **优先级 1**：对话中明说的具体值（如"减 500 股"、"限价 32 美元"）→ 直接使用
   - **优先级 2**：可推算的值（如"目标仓位 15%" + 当前持仓 → 推算具体股数；"反弹接近 20-21 美元" → 取中位数 20.5 作为限价）→ 推算并使用
   - **优先级 3**：仅当上述两类都无法获得时（如对话完全没提目标价、没提仓位变化）→ 放进 `missing_fields`

   **诚实表达**：每个被推算填充的字段都附带"AI 建议依据"（即下方 `value_source` 字段），让用户知道这个数字是从哪里推算出来的，不是凭空捏造。

2. **PEER 链路外的旁路调用**：ActionPlanner 不在 Planning → Executing → Expressing → Reviewing 链路中，而是用户点击按钮后的独立调用。它**不进入 LLM Skill Selector 的可选列表**（`_LLM_SELECTABLE_EXTRA_SKILLS`），也不进入任何路由的静态 bundle（`_SKILL_BUNDLES_BY_ROUTE`），不会被 PlanningAgent 误触。

3. **不重新加载持仓数据**：ActionPlanner 的输入是 `conversation_context`，所有持仓、纪律、投研信息已经在 PEER 链路中被 Expressing Agent 消费过、并以分析结论形式出现在对话中。ActionPlanner 只需要从对话中**提取或推算"用户决定做什么"**，不需要重新调 wp-load-context。这样保证 ActionPlanner 是轻量的（< 3 秒响应）。

4. **独立 LLM Prompt 模板**：ActionPlanner 不复用 wp-reasoning，使用独立的 prompt 模板。理由：wp-reasoning 输出面向用户的 Markdown（chat_answer），ActionPlanner 输出面向 OrderManager 的 JSON（ActionListDraft），输出格式根本不同；独立实现便于后续对 ActionPlanner 的 prompt 做专门调优。

5. **SKILL.md 描述的措辞要求**：在 description 中明确写 `trigger: manual_button_only`，并在 tags 中加 `manual-trigger`，让未来维护者清楚这不是 Selector 可选的 Skill。

### 7.3 Expressing Agent 改动（最小侵入）

仅在现有 Expressing Agent 输出中增加两个字段：

```python
@dataclass
class ExpressingOutput:
    answer: str
    actionable: bool = False                  # 新增
    actionable_hint: Optional[str] = None     # 新增，如"3 项可执行行动"
```

**关键约束**：
- Expressing Agent **不生成** action_items 详细内容
- 详细生成的工作交给 ActionPlanner Skill（按需触发）
- 这样设计的目的是把每轮对话的 token 成本和延迟降到最低

### 7.4 责任边界总结

| 组件 | 触发时机 | 职责 |
|------|----------|------|
| Expressing Agent | 每次 AI 回复时 | 生成 answer + 判断 actionable + 给出轻量提示 |
| ActionPlanner Skill | 用户点击"生成行动清单"按钮 | 把对话翻译为结构化 ActionListDraft |
| OrderManager | 持续运行 | 管理 ActionDraft / Strategy / Order 三层状态机、风控、订单同步 |
| BrokerAdapter | 下单/查询时 | 屏蔽不同券商 API 差异 |
| CredentialProvider | 下单/查询时 | 从本地 keyring 读取交易凭证（仅本地后端可访问） |
| AuditLogger | 关键事件时 | 记录脱敏后的审计日志 |

### 7.5 Reviewing Agent 增强（v3.3+）

v3.0 的 Reviewing Agent 基于"分析 vs 市场表现"做泛化复盘。v3.2 接入实际成交数据后，Reviewing Agent 可以做更精准的复盘：

- **执行保真度**：决策 vs 实际成交是否一致？
- **触发判断**：触发条件设置是否合理？
- **决策质量**：成交后的标的表现是否验证了原决策？

这部分改动留待 v3.3+，MVP 不强求。

---

## 8. 技术架构与数据模型

### 8.1 后端模块新增

```
wealthpilot/
├── agents/                    # 现有 PEER Agents
│   └── ...                    # Expressing Agent 增加 actionable / actionable_hint 字段
├── skills/                    # 现有 Skills
│   └── wp-action-planner/     # 新增：wp-action-planner Skill（SKILL.md 描述文档）
│       └── SKILL.md
├── brokers/                   # 新增：券商适配层
│   ├── base.py                # BrokerAdapter ABC + 数据契约
│   ├── mock.py                # Mock 适配器（v3.2 默认）
│   ├── tiger.py               # 老虎证券实现（v3.3）
│   ├── futu.py                # 富途（v3.3+）
│   └── gjzq.py                # 国金证券（v3.3+）
├── orders/                    # 新增：订单管理
│   ├── manager.py             # OrderManager（管理三层状态机）
│   ├── risk.py                # 本地风控规则引擎
│   └── sync.py                # 订单状态轮询同步
├── credentials/               # 新增：凭证管理（仅本地）
│   ├── provider.py            # CredentialProvider（封装 keyring）
│   └── README.md              # 凭证安全设计说明
├── audit/                     # 新增：审计日志
│   └── logger.py
└── ...
```

### 8.2 数据模型（核心表）

数据模型与三层状态机对齐：`action_draft`（草稿）/ `allocation_intent` + `symbol_strategy`（策略）/ `order_record`（订单）。

#### 8.2.1 `action_draft`（行动清单草稿）

```sql
CREATE TABLE action_draft (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL,                 -- 关联的对话 ID
    decision_summary TEXT NOT NULL,                -- AI 生成的决策依据摘要
    payload JSONB NOT NULL,                        -- 完整的 ActionListDraft 结构（见下方说明）
    status TEXT NOT NULL,                          -- draft / confirmed / discarded
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    discarded_at TIMESTAMPTZ
);

CREATE INDEX idx_action_draft_user_status ON action_draft(user_id, status);
```

**`payload` 字段结构**（v0.5 明确，与 ActionListDraft 直接对齐）：

```json
{
  "symbol_strategies": [
    {
      "symbol": "US.LI",
      "side": "SELL",
      "quantity": 500,
      "quantity_pct": null,
      "order_type": "CONDITIONAL_LIMIT",
      "trigger_price": "32.00",
      "limit_price": "32.00",
      "parent_intent_index": 0
    }
  ],
  "allocation_intents": [
    {
      "title": "权益类降至 40%",
      "target_allocation": {"equity": 0.40, "fixed_income": 0.35, "..."}
    }
  ],
  "risk_notes": [
    "本次卖出占总资产 8%，超过单笔 5% 风控建议线"
  ],
  "missing_fields": [
    {
      "target_type": "symbol_strategy",
      "target_index": 0,
      "field": "trigger_price",
      "description": "条件单触发价未明确，请补充"
    }
  ]
}
```

**说明**：
- `payload` 直接采用 ActionListDraft 的字段命名，**不使用通用的 `actions[{type:...}]` 结构**
- 用户确认后（`status: draft → confirmed`），OrderManager 解析 payload，把 `symbol_strategies` 写入 `symbol_strategy` 表、`allocation_intents` 写入 `allocation_intent` 表
- `risk_notes` 和 `missing_fields` 不写入下游表，仅保留在 ActionDraft.payload 中作为审阅依据
- v0.6 起 `missing_fields` 是结构化对象数组（含 target_type / target_index / field / description），便于前端实时移除对应项

**missing_fields 的前后端校验规则**：
- 前端：编辑卡片时，`missing_fields` 列表中的字段以红色边框 + 提示文案标记；未补齐时"加入投资行动"按钮置灰
- 后端：`POST /api/action/drafts/{id}/confirm` 接到请求时二次校验 missing_fields 是否清空，未清空则拒绝确认（HTTP 422）

#### 8.2.2 `allocation_intent`（资产配置调整意图）

```sql
CREATE TABLE allocation_intent (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    source_draft_id UUID REFERENCES action_draft(id),  -- 来自哪个草稿
    title TEXT NOT NULL,                    -- "权益类降至 40%"
    target_allocation JSONB NOT NULL,       -- {"equity": 0.40, "fixed_income": 0.35, ...}
    current_allocation_snapshot JSONB,      -- 创建时的当前配置快照
    status TEXT NOT NULL,                   -- active / paused / completed / discarded
    related_conversation_id UUID,           -- 关联的对话 ID（冗余字段，便于查询）
    decision_basis TEXT,                    -- 决策依据摘要
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);
```

#### 8.2.3 `symbol_strategy`（标的策略）

```sql
CREATE TABLE symbol_strategy (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    source_draft_id UUID REFERENCES action_draft(id),         -- 来自哪个草稿
    parent_intent_id UUID REFERENCES allocation_intent(id),   -- 可选，关联到资产配置意图
    symbol TEXT NOT NULL,                   -- "US.LI"
    side TEXT NOT NULL,                     -- BUY / SELL
    target_quantity INTEGER,                -- 计划成交量（绝对股数）
    target_quantity_pct DECIMAL,            -- 或目标仓位百分比（与 target_quantity 二选一）
    cumulative_filled_quantity INTEGER DEFAULT 0,  -- 累计已成交量（来自所有关联订单的累加）
    order_type TEXT NOT NULL,               -- LIMIT / CONDITIONAL_LIMIT；v3.2 Mock 支持两类，v3.3 视 Tiger API 调研结论
    trigger_price DECIMAL,                  -- CONDITIONAL_LIMIT 必填；LIMIT 可为空
    limit_price DECIMAL,                    -- 最终挂单限价
    status TEXT NOT NULL,                   -- active / paused / completed / discarded
    related_conversation_id UUID,
    decision_basis TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_strategy_user_status ON symbol_strategy(user_id, status);
CREATE INDEX idx_strategy_parent_intent ON symbol_strategy(parent_intent_id);
```

**关键设计**：
- `cumulative_filled_quantity` 是从关联的所有 `order_record.filled_quantity` 聚合计算得出
- 当 `cumulative_filled_quantity >= target_quantity` 时，OrderManager 自动把 `status` 改为 `completed`
- 一个 Strategy 在生命周期内可对应多笔 Order（分批、撤单重挂）

#### 8.2.4 `order_record`（券商订单）

```sql
CREATE TABLE order_record (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    strategy_id UUID NOT NULL REFERENCES symbol_strategy(id),  -- 1 strategy : N orders
    broker_name TEXT NOT NULL,              -- "mock" / "tiger" / "futu" / "gjzq"
    broker_order_id TEXT,                   -- 券商订单 ID（可能为 NULL，如 created/rejected 时）
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,              -- 本笔订单的下单量
    filled_quantity INTEGER DEFAULT 0,      -- 本笔订单已成交量
    order_type TEXT NOT NULL,               -- LIMIT / CONDITIONAL_LIMIT
    limit_price DECIMAL,                    -- 最终挂单限价
    stop_price DECIMAL,                     -- v3.4+ STOP_LOSS / STOP_PROFIT 使用
    avg_filled_price DECIMAL,
    status TEXT NOT NULL,                   -- created / submitted_to_broker / broker_pending / partially_filled / filled / cancelled / rejected / expired / unknown
    submitted_at TIMESTAMPTZ,
    filled_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    last_synced_at TIMESTAMPTZ,             -- 最后一次状态同步时间
    raw_broker_response JSONB,              -- 原始券商返回，便于排查
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_order_strategy ON order_record(strategy_id);
CREATE INDEX idx_order_user_status ON order_record(user_id, status);
CREATE INDEX idx_order_broker_id ON order_record(broker_name, broker_order_id);
```

**1 : N 关系示例**：

```
symbol_strategy(LI 减仓 1000 股)
   ├── order_record #1：挂 500 股 → filled（500 股成交）
   ├── order_record #2：挂 500 股 → cancelled（用户撤单）
   ├── order_record #3：挂 300 股 → partially_filled（200 股成交）
   └── order_record #4：挂 300 股 → filled（300 股成交）
   
cumulative_filled_quantity = 500 + 0 + 200 + 300 = 1000
status = completed ✓
```

#### 8.2.5 `audit_log`（审计日志）

```sql
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    event_type TEXT NOT NULL,               -- order.confirmed / order.submitted / risk.triggered / ...
    payload JSONB NOT NULL,                 -- 脱敏后的事件详情
    ip_address TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 8.3 关键 API 端点（新增）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/action_drafts` | POST | 创建行动清单草稿（点击"生成行动清单"） |
| `/api/v1/action_drafts/{id}` | GET / PATCH | 查看 / 编辑草稿 |
| `/api/v1/action_drafts/{id}/confirm` | POST | 确认草稿，写入 strategy 表，状态转 confirmed |
| `/api/v1/action_drafts/{id}` | DELETE | 丢弃草稿（软删除，状态转 discarded） |
| `/api/v1/strategies` | GET / POST | 标的策略列表 / 直接创建（非草稿来源） |
| `/api/v1/strategies/{id}` | GET / PATCH | 策略详情 / 编辑 |
| `/api/v1/strategies/{id}/pause` | POST | 暂停策略 |
| `/api/v1/strategies/{id}/resume` | POST | 恢复策略 |
| `/api/v1/strategies/{id}/discard` | POST | 作废策略 |
| `/api/v1/strategies/{id}/place_order` | POST | 基于策略创建并提交一笔订单（含人工确认 + 风控） |
| `/api/v1/orders` | GET | 订单列表 |
| `/api/v1/orders/{id}` | GET | 订单详情 |
| `/api/v1/orders/{id}/cancel` | POST | 取消订单 |
| `/api/v1/orders/{id}/sync` | POST | 手动同步订单状态（unknown 状态时使用） |
| `/api/v1/allocation_intents` | GET / POST | 资产配置调整意图列表 / 创建 |
| `/api/v1/credentials/bind` | POST | 绑定券商凭证（仅本机调用，写入 keyring） |
| `/api/v1/credentials/unbind` | POST | 解绑凭证 |
| `/api/v1/brokers/{name}/account` | GET | 获取券商账户信息 |

### 8.4 前端组件新增

| 组件 | 路径 | 说明 |
|------|------|------|
| ActionListGenerateButton | components/decision/ | 投资决策页面新增按钮 |
| ActionDraftCard | components/decision/ | 行动清单确认卡片弹层 |
| InvestmentActionPage | pages/action/ | 投资行动主页面 |
| AllocationActionTab | pages/action/tabs/ | Tab 1：资产配置 |
| SymbolStrategyTab | pages/action/tabs/ | Tab 2：标的策略 |
| ActionRecordTab | pages/action/tabs/ | Tab 3：行动记录 |
| FinalConfirmDialog | components/order/ | 最终下单确认对话框 |
| BrokerConnectPage | pages/settings/ | 券商账户绑定 |

---

## 9. MVP 范围与迭代规划

### 9.1 v3.2（MVP，Mock 闭环）

**目标**：跑通端到端完整 UI 流程，但不接入真实交易 API。验证产品形态、数据模型、状态机、交互流程。

**必做**：
- ✅ Expressing Agent 增加 `actionable` / `actionable_hint` 字段
- ✅ 投资决策页面"生成行动清单"按钮 + 高亮交互
- ✅ ActionPlanner Skill（按需触发，生成 ActionListDraft）
- ✅ 行动清单确认卡片 UI
- ✅ 投资行动模块三 Tab 页面：资产配置 / 标的策略 / 行动记录
- ✅ 三层状态机后端实现（ActionDraft / Strategy / Order）
- ✅ 数据模型完整落地（5 张核心表）
- ✅ Mock BrokerAdapter（覆盖各种异常场景：rejected / partially_filled / unknown）
- ✅ 人工最终确认弹窗（不可跳过，5 分钟超时）
- ✅ 基础审计日志
- ✅ 行动记录时间轴视图

**v3.2 不做**：
- ❌ 真实交易 API 接入
- ❌ 真实凭证管理（暂用环境变量或 Mock）
- ❌ 完整本地风控规则（仅做"单笔金额上限"一条，其他留 v3.3）
- ❌ 富途、国金接入
- ❌ Reviewing Agent 复盘报告增强

**v3.2 验收标准**：
1. 用户在投资决策页面与 AI 完成一次完整对话
2. 点击"生成行动清单" → 弹出预填卡片 → 编辑 → 确认入库
3. 投资行动 Tab 中能看到对应的策略，状态正确
4. 触发 Mock 下单 → 弹出最终确认 → 提交 → Mock 模拟成交回报
5. 行动记录中能看到完整链路追溯：对话 → 草稿 → 策略 → 订单 → 成交
6. 演示给第三人能讲清楚产品价值（面试演示能力）

### 9.2 v3.3（Tiger 实战）

**目标**：接入真实老虎证券 API，从沙盒走到小额真实订单。

**必做**：
- ✅ Tiger BrokerAdapter 实现（仅 LIMIT 订单类型）
- ✅ CredentialProvider 完整实现（keyring 集成）
- ✅ 凭证绑定 UI（用户中心新增"券商账户"页面）
- ✅ 老虎沙盒环境联调通过
- ✅ 完整本地风控规则（4 条）
- ✅ 订单状态轮询同步（含 unknown 状态恢复）
- ✅ 异常场景处理（网络中断、API 限流、回执丢失对账）
- ✅ 小额真实订单测试通过

**v3.3 不做**：
- ❌ 富途、国金接入
- ❌ STOP_LOSS / STOP_PROFIT 订单类型
- ❌ 多账户管理
- ❌ AI 监控触发型订单

### 9.3 v3.4+（增强与扩展）

- 富途 OpenD 接入
- STOP_LOSS / STOP_PROFIT 订单类型
- 资产配置自动拆解为标的动作 Skill
- Reviewing Agent 基于成交数据复盘
- 行动记录数据导出（CSV / Excel）
- 触发条件配置增强（时间触发、组合触发）

### 9.4 v3.5+（探索）

- 国金证券 QMT 接入（依赖 Mac 远程连接确认）
- 多账户管理
- 移动端

### 9.5 里程碑（v3.2 MVP 拆解，预估）

| 里程碑 | 内容 | 子任务索引 | 预估周期 |
|--------|------|-----------|----------|
| M1 | 数据模型 + 三层状态机后端 + 基础 API 框架 | M1.1 5 张核心表 migration<br>M1.2 ActionDraft / Strategy / Order 三层状态机后端实现<br>M1.3 OrderManager 骨架 + 基础 CRUD API | 1 周 |
| M2 | BrokerAdapter 抽象 + Mock 实现 + 单元测试 | M2.1 BrokerAdapter ABC + 数据契约<br>M2.2 MockBrokerAdapter 实现（覆盖 partially_filled / rejected / unknown 异常）<br>M2.3 状态变迁单元测试 | 1 周 |
| M3 | ActionPlanner Skill + Expressing 改造 | M3.1 Expressing Agent 输出新增 actionable / actionable_hint<br>M3.2 ActionPlanner Skill 实现 + SKILL.md<br>M3.3 ActionListDraft 数据契约对齐 | 0.5 周 |
| M4 | 前端：投资决策页改造 + 行动清单卡片 | M4.1 ActionListGenerateButton 组件（含高亮状态）<br>M4.2 ActionDraftCard 弹层（编辑/确认/取消）<br>M4.3 与 ActionPlanner API 对接 | 1 周 |
| M5 | 前端：投资行动模块三 Tab | M5.1 资产配置 Tab（当前 vs 目标 + 意图列表）<br>M5.2 标的策略 Tab（持仓策略 / 观察策略 / 已挂单分区）<br>M5.3 行动记录 Tab（时间轴 + 追溯链路）<br>M5.4 顶部"待处理草稿"提示条 | 1.5 周 |
| M6 | 人工确认弹窗 + 基础风控 + 审计 | M6.1 FinalConfirmDialog（不可跳过 + 5 分钟超时 + Mock 文案）<br>M6.2 单笔金额上限风控规则<br>M6.3 审计日志基础事件埋点 | 0.5 周 |
| M7 | 端到端联调 + Mock 异常场景测试 + 文档 | M7.1 完整 demo flow 走通<br>M7.2 异常场景覆盖测试（unknown / rejected / partially_filled）<br>M7.3 Mock 模式 UI 文案规范全量校验<br>M7.4 README + AGENTS.md 更新 | 1 周 |
| **v3.2 合计** |  |  | **6.5 周** |

**子任务索引说明**：

子任务粒度是"写 Claude Code 提示词时的索引"，不是完整工程方案。每个子任务推进时，单独写贴近当下代码状态的提示词，避免预先写死的工程方案在开发过程中变成废文档或束缚。

v3.3（Tiger 接入）预估额外 **3-4 周**，包括 Tiger API 调研（v3.3 启动硬性前置条件）、沙盒联调、安全审查、小额真实订单测试。

---

## 10. 风险评估与缓解措施

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 凭证泄露导致资金损失 | 🔴 高 | 凭证仅本地 keyring 存储；交易模块仅本地部署；后端不入库不入日志；强制 HTTPS；审计日志监控异常调用 |
| 误下单（参数错误） | 🔴 高 | 强制人工确认弹窗；本地风控规则；订单偏离市价超 5% 强提示 |
| 网络中断时订单已提交但回执丢失 | 🔴 高 | 进入 unknown 状态；本地订单先入库再调用 API；启动时检查"submitted_to_broker 但无 broker_order_id"的订单做对账；UI 强提示用户登录券商 App 核实 |
| 状态机理解错误导致重复下单 | 🟠 中 | 三层状态机严格分离；OrderManager 串行处理；每次下单前校验是否已有 active 订单 |
| 部分成交场景处理错误 | 🟠 中 | 数据模型支持 1 strategy : N orders；显式 partially_filled 状态；累计成交追踪 |
| 用户在 PRD 之外触发了 WealthPilot 自建价格监控 | 🟠 中 | 架构红线明确写入：MVP 不实现本地价格监控；即使 v3.4+ 引入也作为独立可关闭模块 |
| 券商 API 故障导致状态长期 unknown | 🟠 中 | 提供手动同步按钮；超过 X 小时仍 unknown 触发用户告警；定期对账 |
| 用户对"AI 下单"产生过度依赖 | 🟡 低 | 产品文案明示工具属性；强制人工确认；定期向用户展示"决策最终由您确认"提示 |
| 监管口径变化（个人 API 交易） | 🟠 中 | 严格遵守"用户授权工具"边界；不做代客理财；保留合规审计能力 |
| 老虎证券 API 限流 | 🟡 低 | 实现指数退避；订单状态查询使用增量同步而非全量轮询 |
| 行动清单与对话上下文丢失关联 | 🟡 低 | conversation_id 强制关联；持久化对话历史；草稿表 payload 完整保留 |
| Mac Keychain 在多设备同步引发问题 | 🟡 低 | 凭证标记为"仅本机"；切换设备需重新绑定 |
| Mock 模式被误用于真实交易决策 | 🟡 低 | UI 醒目标识 Mock 模式；行动记录中 Mock 订单与真实订单分区展示 |

---

## 11. 默认业务决策与待确认问题

本节包含两类内容：11.1 是 v3.2 阶段已确认的默认业务决策（开发直接遵循）；11.2/11.3 是仍需评审或后续讨论确认的开放问题。

### 11.1 v3.2 默认业务决策（已确认，开发遵循）

以下 4 个问题在 v0.4 阶段已形成默认决策，开发时直接遵循，不再作为待确认项：

| # | 问题 | v3.2 默认决策 | 设计理由 |
|---|------|--------------|---------|
| 1 | 行动清单内多个 SymbolStrategy 的确认方式 | **允许部分确认**：每条策略可独立勾选；勾选项写入 `symbol_strategy` 表，未勾选项保留在 ActionDraft.payload 内或转为 `discarded` | 尊重用户控制权；用户可能对清单内某些项已认可、某些项还需观察 |
| 2 | 草稿过期处理 | **7 天后顶部提示但不自动清理**；v3.2 不做批量清理按钮，由用户手动逐条处理 | MVP 阶段简化；自动清理可能误删，宁可留尾巴让用户主动整理 |
| 3 | 累计成交超过 target_quantity 的处理 | **禁止超额下单**：OrderManager 在创建 Order 前校验 `cumulative_filled + 本次下单量 ≤ target_quantity`；超出则拒绝，提示用户调整数量或先编辑 Strategy 提高 target | 在 MVP 阶段宁可严格——让"超额"显式暴露为校验失败，避免掩盖系统 bug；这与 unknown 状态的"诚实表达"哲学一致 |
| 4 | Strategy 在有 `broker_pending` Order 时的编辑限制 | **禁止编辑核心交易参数**（symbol / side / target_quantity / order_type / trigger_price / limit_price）；如需修改，需先撤单再改；非核心参数（decision_basis / 备注）可编辑 | 防止"挂着的单"和"用户记录的策略"语义不一致，避免事后追溯混乱 |

### 11.2 v3.3 Tiger 接入前必须确认的问题

5. **老虎 API 实际能力**：TigerOpen 对美股 / 港股 / A 股的限价条件单支持是否一致？是否所有市场都支持 GTC？需要在 v3.3 启动前完成 API 调研。
6. **条件单提交后 WealthPilot 端撤单**：用户在 WealthPilot 撤单需调用券商 API，但若调用失败如何处理？建议保留"已请求撤单但未确认"中间态。
7. **Tiger API 限流**：免费用户的 API QPS 限制是多少？订单状态轮询频率如何与限流匹配？
8. **凭证绑定的多设备处理**：用户在 Mac A 绑定后，切换到 Mac B 是否需要重新绑定？建议明确"凭证不跨设备同步"。

### 11.3 业务逻辑问题（v3.2 / v3.3 都涉及）

9. **港股 / A 股的 T+1 限制**：用户当天买入的标的不能当天卖出，前端是否需要在卡片上提示？
10. **行动清单确认卡片是否支持"分批执行"**：例如 AI 建议减仓 50%，用户想拆成"先减 20%，剩下的 30% 看行情"，是否在 v3.2 中支持？建议支持，因为新数据模型（1 strategy : N orders）天然支持这个场景。
11. **多账户场景**：用户在老虎有 A 股、港股两个子账户，MVP 是否支持？建议 v3.2/v3.3 都不支持，v3.4+ 处理。
12. **盘前盘后挂单**（美股）是否需要在 UI 中区分？建议 v3.2 不区分，挂单默认仅常规交易时段。
13. **审计日志的用户可见范围**：用户中心是否提供"我的审计记录"查看入口？建议 v3.2 提供基础查看，详细分析 v3.4+。
14. **unknown 状态的 UI 表达**：如何让用户清楚理解"我们不知道券商侧到底成交没"？是否需要一个专用图标和说明？

---

## 附录 A：术语表

| 术语 | 英文 | 说明 |
|------|------|------|
| 行动清单草稿 | Action Draft | AI 从对话中提取的待确认行动集合，状态机独立 |
| 资产配置意图 | Allocation Intent | 大类资产比例调整的目标声明（已确认状态） |
| 标的策略 | Symbol Strategy | 具体标的的买卖动作 + 触发条件（已确认状态） |
| 订单 | Order | 实际向券商提交的下单记录，一个 Strategy 可对应多笔 Order |
| 决策依据 | Decision Basis | 行动项的产生原因，关联到原对话 |
| 人在回路 | Human-in-the-Loop | 关键决策点必须有人工介入的安全设计 |
| 券商托管触发 | Broker-Side Triggering | 条件单提交后由券商监控价格并触发，WealthPilot 不本地监控 |
| 券商适配器 | Broker Adapter | 屏蔽不同券商 API 差异的统一抽象层 |
| 凭证提供者 | Credential Provider | 封装 keyring 访问的本地组件，仅本地后端可用 |
| Mock 适配器 | Mock Adapter | v3.2 阶段不接真实 API，模拟券商行为的本地实现 |
| PEER | Planning / Executing / Expressing / Reviewing | v3.0 引入的多 Agent 协作模式 |
| ActionPlanner | ActionPlanner Skill | 按需触发的 Skill，把对话翻译为结构化 ActionListDraft |

## 附录 B：参考文档

- WealthPilot v3.0 架构文档（AGENTS.md）
- TigerOpen API 官方文档
- v3.0 投资纪律规则文档（13 条）
- 资产配置五分类框架（货币/固收/权益/另类/衍生）

---

**文档结束**
