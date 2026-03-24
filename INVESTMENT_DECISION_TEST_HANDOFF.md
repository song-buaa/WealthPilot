# WealthPilot — 投资决策模块测试交接文档

> **文档版本**：v1.0 · 2026-03-23
> **对应代码版本**：commit `0ec4adf`（branch: `feature/manus-handoff-v2`）
> **交接对象**：测试同学 / 外部 AI 代理（Manus）

---

## 1. 模块名称与目标

**模块名称**：投资决策引擎（Investment Decision Engine）

**解决的问题**：用户用自然语言表达投资想法（如"我想加仓理想汽车，发布会前合适吗？"），系统自动解析意图、调取持仓与投研数据、执行纪律规则校验、生成多维信号，最终由 AI 给出结构化投资建议（加仓 / 观望 / 减仓）。

**主要适用场景**：
- 针对具体标的的加仓 / 减仓 / 买入 / 卖出决策评估
- 有触发事件（发布会、财报、市场下跌）时的决策辅助
- 持仓纪律合规检查（仓位是否超限）

---

## 2. 测试范围

### ✅ 本次重点测试
| 功能 | 位置 |
|------|------|
| 自然语言意图解析 | 输入框 → AI 解析结果展示 |
| 数据加载与持仓匹配 | 持仓摘要区块 |
| 纪律规则校验（超限 / 接近上限 / 正常） | 规则校验区块 |
| 四维信号生成 | 信号层区块 |
| AI 推理结论（BUY / HOLD / SELL） | 最终结论区块 |
| 流程中断处理（API Key 缺失 / 置信度不足 / 校验失败） | 错误提示区 |
| 快捷示例按钮（3 个） | 输入区上方 |

### ❌ 不属于本次测试重点
- 策略设定 Tab（已有功能，未改动）
- 投研观点录入流程（另一模块）
- 投资纪律评分页面（discipline 模块）
- 持仓总览页面（overview 模块）

---

## 3. 页面入口与使用路径

```
启动应用
  → 左侧导航栏点击「投资决策」
  → 默认打开「🧠 决策引擎」Tab
  → 在文本框输入决策需求（或点击示例按钮）
  → 点击「🔍 开始分析」
  → 等待 AI 分析（约 3~10 秒）
  → 查看逐步展开的分析结果
```

**直接访问地址**：`http://localhost:8501`（启动后左侧菜单 → 投资决策）

---

## 4. 核心功能说明

### 4.1 意图解析

| 项目 | 说明 |
|------|------|
| **输入** | 用户自然语言文本 |
| **处理** | Claude Sonnet 4（`claude-sonnet-4-20250514`）解析为结构化 JSON |
| **输出** | 标的名称、操作类型、时间维度、触发事件、置信度（0~1） |
| **依赖** | Anthropic API（`ANTHROPIC_API_KEY` 环境变量） |
| **特殊规则** | 置信度 < 0.6 → 流程中断，调用 Claude Haiku 生成澄清问题 |

### 4.2 数据加载

| 项目 | 说明 |
|------|------|
| **输入** | 标的名称（可为 None）、portfolio_id |
| **处理** | 从 SQLite 读取持仓（Position 表）和投资纪律规则（Portfolio 表）；从 ResearchViewpoint 表查投研观点，无数据时 fallback 到内置 mock |
| **输出** | 用户画像、持仓列表、目标持仓、规则参数、投研观点、总资产 |
| **标的匹配** | 模糊名称匹配（双向包含）+ ticker 匹配（至少 2 字符，防空串误匹配） |

### 4.3 前置校验（Pre-Check）

| 校验项 | 通过条件 |
|--------|--------|
| 用户画像 | 不为 None |
| 持仓数据 | 列表非空（至少有 1 条持仓） |
| 纪律规则 | Portfolio 记录存在 |
| 总资产 | > 0 |

任一项未通过 → 流程中断，显示 `⛔ 前置校验未通过` 错误。

### 4.4 规则校验（Rule Engine）

| 场景 | 结论 |
|------|------|
| `当前仓位 / 单标的上限 ≥ 1.0` | `violation=True`，显示「超限 ⛔」 |
| `比值 ≥ 0.8 且 < 1.0` | `warning="接近上限"`，显示「警告 ⚠️」 |
| `比值 < 0.8` | 正常，显示「正常 ✅」 |
| 操作为减仓/卖出且持仓为 0 | `violation=True`，显示「未持有该标的」 |
| 操作为加仓/买入且标的不在持仓中 | 不中断，提示「新建仓操作」 |

> ⚠️ **注意**：规则违规（violation=True）**不会硬性中断**流程，而是传入后续信号层和 LLM，由 AI 在结论中体现（通常给出 HOLD 或 SELL）。

### 4.5 信号生成（Signal Engine）

| 信号维度 | 计算逻辑 |
|---------|---------|
| **仓位信号** | ratio ≥ 0.8 → 偏高；0.4~0.8 → 合理；< 0.4 → 偏低 |
| **事件信号** | 有触发事件 → 不确定性=高, 方向=中性；无 → 不确定性=低 |
| **基本面信号** | 投研观点关键词匹配（正/负面词计数多的一侧胜出），无观点 → N/A |
| **情绪信号** | MVP 阶段固定为「中性」，不做真实计算 |

### 4.6 AI 推理（LLM Engine）

| 项目 | 说明 |
|------|------|
| **模型** | `claude-sonnet-4-20250514` |
| **输入** | 用户原始问题 + 意图 + 持仓上下文 + 规则结果 + 信号层 + 投研观点 + 用户画像 |
| **输出** | `decision`（BUY/HOLD/SELL）+ `reasoning`（推理依据）+ `risk`（风险提示）+ `strategy`（操作建议） |
| **降级处理** | API 超时 / 调用失败 / JSON 解析失败 → 返回默认 HOLD，展示降级提示 |
| **UI 映射** | BUY → 加仓 📈，HOLD → 观望 🔍，SELL → 减仓 📉 |

---

## 5. 核心逻辑 / 规则

### 流程控制（FlowStage 状态机）

```
INTENT → LOADED → PRE_CHECK → RULE_CHECK → SIGNAL → LLM → DONE
                                                           ↑任意阶段异常
                                                        ABORTED
```

**中断条件**（任一触发即返回 ABORTED）：
1. 无 `ANTHROPIC_API_KEY` 环境变量
2. 意图解析置信度 < 0.6
3. 数据加载异常
4. 前置校验未通过

**不中断但有标记**：
- 规则 violation（传入 LLM 参考）
- LLM 返回降级结果（`is_fallback=True`）

### 最容易出错的地方

| 风险点 | 描述 |
|--------|------|
| API Key 未设置 | 页面直接停止并提示，无法进入任何分析 |
| 标的名称模糊 | "理想"可能匹配到"理想汽车 (LI)"，"苹果"可能匹配到"苹果 (AAPL)"；极短名称（1 字）可能误匹配 |
| 空 ticker 误匹配 | 已修复：ticker 匹配要求至少 2 字符（`len(ticker) >= 2`） |
| LLM 输出非标准 JSON | 有 3 层提取兜底（直接解析 → 代码块提取 → 正则 `{...}` 提取），失败时降级为 HOLD |
| ResearchViewpoint 表为空 | 自动 fallback 到内置 mock 数据（理想汽车/腾讯/英伟达），其余标的返回"暂无该标的的投研观点" |
| 百分比字段存储格式不一致 | `_safe_pct()` 自动处理 0~100 与 0~1 两种格式，`> 1` 时自动除以 100 |

---

## 6. 数据依赖

### 数据库表（SQLite: `wealthpilot.db`）

| 表名 | 用途 | 测试关注点 |
|------|------|-----------|
| `portfolio` | 投资纪律参数（单标持仓上限、权益上限等） | `max_single_stock_pct` 字段决定规则校验结果 |
| `position` | 持仓数据（名称、ticker、市值、成本价等） | `segment='投资'` 筛选；`market_value_cny` 计算权重 |
| `research_viewpoint` | 投研观点（thesis、supporting_points） | 可为空，系统自动 fallback |

### Mock 数据（代码内置，无需准备）

- **用户画像**：风险偏好=中高，目标=长期增值，年限=5 年（`data_loader.py:UserProfile`）
- **投研观点 Mock**：理想汽车、腾讯、英伟达 3 个标的（`data_loader.py:_MOCK_RESEARCH`）
- **Portfolio Mock**：单标上限=25%、权益上限=80%（`data_loader.py:_MockPortfolio`）

### 配置文件

| 文件 | 用途 |
|------|------|
| `app/discipline/config.py` | 流动性规则（`min_cash_pct`） |
| `.env` 或环境变量 | `ANTHROPIC_API_KEY`、`DATABASE_URL` |

### 前置数据准备清单

| 项目 | 要求 | 备注 |
|------|------|------|
| `ANTHROPIC_API_KEY` | **必须** | 不设置则页面直接停止 |
| `wealthpilot.db` | 推荐已有持仓数据 | 无数据时规则校验可能显示"校验失败" |
| ResearchViewpoint 表 | 可为空 | 空时自动 fallback，不影响流程 |

---

## 7. 启动方式

### 环境要求

- Python **3.11**（通过 `conda activate wealthpilot`）
- conda 环境：`/Users/songbin/opt/anaconda3/envs/wealthpilot`

### 安装依赖

```bash
conda activate wealthpilot
pip install -r requirements.txt
```

### 关键依赖版本

```
streamlit>=1.32.0
sqlalchemy>=2.0.0
anthropic>=0.86.0
plotly>=5.20.0
```

### 启动命令

```bash
# 方式一：使用已有 API Key（推荐）
export ANTHROPIC_API_KEY='sk-ant-your-key-here'
conda activate wealthpilot
cd /path/to/WealthPilot
python -m streamlit run streamlit_app.py

# 方式二：单行启动
ANTHROPIC_API_KEY='sk-ant-xxx' conda run -n wealthpilot python -m streamlit run streamlit_app.py
```

### 验证启动成功

浏览器访问 `http://localhost:8501`，左侧导航出现「投资决策」菜单项即为成功。

---

## 8. 已知限制 / 暂未实现项

| 限制 | 说明 | 影响 |
|------|------|------|
| 情绪信号固定为"中性" | MVP 简化，未接入真实情绪数据 | AI 推理时情绪维度固定，测试时属正常行为 |
| 用户画像为 mock | 风险偏好、投资目标为硬编码，不读数据库 | 画像信息仅供 LLM 参考，不影响规则校验 |
| 无多轮对话 | 用户无法在同一次分析中追问 | 需要重新输入并分析 |
| 无历史记录 | 每次分析结果不持久化存储 | 刷新页面后结果消失，属正常行为 |
| 无 Web 实时行情 | 价格数据来自数据库静态字段，非实时 | 仅影响"当前价格"展示，规则校验用市值权重计算 |
| LLM 推理延迟 | 受 Anthropic API 响应速度影响，约 3~10 秒 | 有 Spinner 提示，属正常行为 |
| 标的匹配无排歧义 | 多个结果时取第一个匹配项 | 同名标的（如港股/A 股均有"腾讯"）可能匹配到非预期持仓 |
| 无自动交易 | 系统仅输出建议，不执行任何交易 | PRD 明确排除，非缺陷 |

---

## 9. 建议测试重点

按风险等级排序：

| 优先级 | 测试场景 | 验证目标 |
|--------|---------|---------|
| P0 | 无 `ANTHROPIC_API_KEY` 时访问页面 | 应显示配置警告，不崩溃 |
| P0 | 输入持仓中已存在的标的（如"理想汽车"）且仓位超限 | 规则校验显示「超限 ⛔」，LLM 倾向给出 HOLD/SELL |
| P0 | LLM 返回格式异常时（可 mock 错误） | 应降级为 HOLD，展示降级提示，不崩溃 |
| P1 | 点击 3 个示例按钮，全部流程跑通 | 意图解析 → 持仓匹配 → 规则 → 信号 → AI 结论均正常 |
| P1 | 输入模糊/无效文本（如"买啥好"、"   "） | 置信度低，返回澄清问题，流程中断提示友好 |
| P1 | 输入不在持仓中的标的（如"贵州茅台"） | target_position=None，规则显示「新建仓操作」 |
| P2 | 输入"减仓苹果"但苹果持仓为 0 | 规则 violation，显示「未持有该标的，无法减仓」 |
| P2 | ResearchViewpoint 表为空 / 该标的无观点 | 基本面信号显示 N/A，不影响流程继续 |

---

## 10. 项目目录说明（仅投资决策模块相关）

```
WealthPilot/
│
├── decision_engine/                 # 决策引擎核心包
│   ├── __init__.py                  # 包入口，导出 run() 和 DecisionResult
│   ├── intent_parser.py             # Step 1：自然语言 → 结构化意图（Claude Sonnet）
│   ├── data_loader.py               # Step 2：多源数据加载（SQLite + mock fallback）
│   ├── pre_check.py                 # Step 3：三要素前置校验
│   ├── rule_engine.py               # Step 4：仓位纪律规则校验
│   ├── signal_engine.py             # Step 5：四维信号生成
│   ├── llm_engine.py                # Step 6：Claude Sonnet 综合推理
│   └── decision_flow.py             # 全链路编排 + FlowStage 状态机
│
├── app_pages/strategy.py            # Streamlit UI 层（决策引擎 Tab + 策略设定 Tab）
│
├── app/
│   ├── models.py                    # ORM 模型（Portfolio / Position / ResearchViewpoint）
│   ├── database.py                  # SQLite 连接（get_session / get_engine）
│   ├── state.py                     # 全局状态（portfolio_id）
│   └── discipline/config.py         # 流动性规则常量（min_cash_pct）
│
├── wealthpilot.db                   # SQLite 数据库（含真实持仓数据）
├── requirements.txt                 # Python 依赖
└── streamlit_app.py                 # 应用入口
```

---

## 附录：测试数据模板

参见 [`TEST_DATA_TEMPLATE.md`](./TEST_DATA_TEMPLATE.md)，包含：
- 3 组典型测试输入（覆盖加仓 / 减仓 / 新建仓场景）
- 预期输出说明
- 边界用例输入集合

---

## 合规声明

> 本模块输出内容仅供参考，不构成任何投资建议。投资决策应由用户自主判断，系统不承担任何投资损失责任。
