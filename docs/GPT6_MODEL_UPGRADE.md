# GPT-6 基础模型升级工程记录

2026-10-23；L2 跨模块 API 兼容改造。开发基线 `5437da0ad962ac7dc49c68b589b39a31ee5bc3e4`，分支 `codex/gpt6-model-upgrade`。本记录描述工程候选，不代表生产模型已切换或三标真实业务复验通过。

## 集成与权限

PR #40 按用户授权以 `git merge --ff-only` 集成；local main、origin/main、远端 main 均为上述 SHA，GitHub PR 已 MERGED。合并后的 [main Quality Gates](https://github.com/song-buaa/WealthPilot/actions/runs/39911140134) 四项全部通过。无 squash、rebase、force、Tag 或部署。原项目七项未跟踪内容及旧 4.2 worktree 的 `归档.zip` 保留。

本轮仅模型配置、API 兼容、隔离测试、有限真实模型比较及新 PR。用户明确要求审计/适配 `decision_engine/llm_engine.py` 与全部调用入口；该目录仅修改请求边界、模型引用和重试分类，不改原资产、信号、纪律或投资计算。生产配置、数据、固定视图、目标确认、券商和订单未操作。

## 复用与最小范围

权威配置/请求适配：[app/llm_config.py](../app/llm_config.py)。原 PEER、Skills、Prompt、结构化解析和原客户端获取机制保留；不新增专业分析引擎或外部模型服务。`intent_engine/_llm_client.py`、`app/config.py` 仅导出兼容别名，避免第二套默认值。Executing 无独立模型请求。

正常 profile `gpt6`：主模型 `gpt-6.1-sol`，默认 medium，可通过 `WEALTHPILOT_LLM_MAIN_EFFORT=high` 调整；轻量模型 `gpt-6-luna` 默认 none，Reviewer 与严格交易意图提取明确 low。配置在进程启动时解析；改 profile/model 后须重启获授权组件，本任务未操作生产。

显式回滚：`WEALTHPILOT_LLM_PROFILE=legacy-4.1` 选择旧主/轻量模型。`WEALTHPILOT_LLM_MAIN_MODEL` / `WEALTHPILOT_LLM_LIGHT_MODEL` 如显式提供必须与所选 profile 匹配，否则拒绝启动；旧模型不能由请求失败自动选中。回滚同样需要生产配置授权，不是本任务动作。

## 实际调用矩阵

静态 AST 清单共 28 个业务生成请求点，函数、代码行及模型引用见[机器可核对矩阵](acceptance/gpt6/model_call_matrix.json)。下表为代码请求点，不把通过同一函数调用的多种意图重复计为独立模型引擎。主=Sol，轻=Luna；旧 profile 的对应角色为 GPT-4.1 / GPT-4.1-mini。

| 入口 | 请求点/职责 | 默认角色及 API |
|---|---|---|
| decision_engine/llm_engine.py | reason；_call_generic_llm（review_portfolio、analyze_allocation、analyze_performance 共用） | 主；Chat（2 个实际请求点） |
| 同上 | chat、compare_multi_assets、respond_not_in_portfolio | 轻；Chat（3 个实际请求点，保留原任务分档） |
| intent_engine/intent_recognizer.py | 意图识别，含 Planning 调用 | 轻 none；Chat |
| intent_engine/subtask_runner.py | 子任务解释 | 主；Chat |
| intent_engine/output_renderer.py | 结果整合 | 主；Chat |
| backend/graph/decision_graph.py | 原 Planner 路由 | 轻 none；Chat JSON |
| backend/agents/planning_agent.py | 附加 Skill 选择 | 轻 none；Chat |
| backend/agents/expressing_agent.py | 新建仓解释；其他意图经原 llm_engine | 主；Chat |
| backend/agents/reviewing_agent.py | 硬校验失败后的评分 | 轻 low；Chat |
| backend/services/decision_service.py | 会话标题、历史摘要 | 轻 none；Chat |
| backend/services/profile_service.py | 文本画像提取 | 轻 none；Chat |
| 同上 | 截图提取、画像总结 | 主；Chat |
| decision_engine/data_loader.py | 资产名称语义匹配 | 轻 none；Chat |
| backend/graph/tools.py | _llm_write_rationale：原规则触发/计划的理由解释 | 轻 none；Chat |
| backend/services/action/action_planner.py | 原可编辑行动草稿 | 轻 none；Chat；确认/交易权限不变 |
| backend/services/execution_plan/adjustment_parser.py | 用户计划修改解析 | 轻 none；Chat |
| backend/services/trade_intent/parser.py | 原严格 Pydantic 结构化意图 | 轻 low；Responses.parse；不执行交易 |
| app/ai_advisor.py + app/config.py | 完整配置解释 | 主；Chat |
| 同上 | 研报提取（两处）、告警解释 | 轻 none；Chat JSON/文本 |
| app/bank_screenshot.py | 原截图资产识别（原 GPT-4o） | 主；Chat 视觉输入 |
| research_v2/processor.py | narrative/judgment 生成 | 轻 none；Chat JSON |

特殊工具审计：`decision_engine/data_loader.py` 两处公开联网搜索仍是既有 Perplexity sonar-pro / 专用 `gpt-4o-search-preview` Provider 链，不是 GPT-4.1 业务推理 fallback；本轮不重建搜索 Provider/API，不为升级实际调用它们。该专用旧搜索型号的可用性仍是原来源风险，不能借基础模型升级宣称已恢复。`backend/knowledge/store.py` 的 Embedding 不属于生成模型升级，现有索引/模型保持原样。`scripts/m3_v1_baseline.py` 为显式历史基线，不是业务运行路由，不重写历史对照。M5 使用冻结 Provider，不能作为真实模型质量证据。

离线 AST 回归禁止业务代码保留 GPT-4.1 常量（显式回滚 owner 除外），并检查生成调用均经共享适配；专用搜索例外独立列明，不隐藏入口。

## API、错误与依赖

- GPT-6 Chat 请求使用 `max_completion_tokens`；非 none 推理移除 temperature/top_p/logprobs/top_logprobs，校验努力档位及 tool/endpoint 组合。保留原 JSON response_format 和严格 Responses Pydantic schema；不把解析通过当数据资格。
- 输出预算含隐式推理：Sol 至少 8192，Luna low 至少 4096；none 保留原短输出预算。Sol 请求超时下限 90 秒，Luna 20 秒；原 MCP 总分析超时 240 秒不变。
- SDK 统一至多一次传输重试；原 Intent/Subtask 不再额外重放已分类的 API 失败，原 JSON 格式重试仍保留。未触发旧模型 fallback。
- 鉴权、模型权限、配额/限流、网络/超时、参数、Provider、拒答、空输出、截断、返回模型不匹配分别分类。日志仅型号、endpoint、状态及耗时，不记录 prompt、完整资产或原始响应；请求 `store=false`。
- `requirements.txt` 与原 ARM business lock 同步为 SDK 2.29.0；没有升级生产依赖。真实 SDK + httpx MockTransport 已验证 Chat 请求、Responses.parse 严格 schema，非单纯 Mock 函数参数断言。
- ARM 清单原有 `openai-2.29.0-py3-none-any.whl` SHA256 `b7c5de513c3286d17c5e29b92c4c98ceaf0d775244ac8159aeb1bddf840eb42a`；使用 ARM/CPython 3.11 wheel 选择条件下载后哈希一致。本机 Python 为 x86_64；没有冒充原生 ARM 进程/生产安装验收。

参考当前官方 [Sol 合同](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Luna 合同](https://developers.openai.com/api/docs/models/gpt-6-luna)、[迁移参数](https://developers.openai.com/api/docs/guides/latest-model)。保留用户指定型号，未替换为其他档位。

## 开发 Key 排查与安全接入

原目录 `/Users/songbin/Documents/GitHub/WealthPilot/.env`、`.env.local` 均存在非空 `WEALTHPILOT_OPENAI_API_KEY`，值不同；新旧独立 worktree 没有这两个 ignored 文件。原 `backend/main.py` 经 `backend/core/env.py` 显式读取项目根 `.env`。Codex 进程未继承 Key；隔离测试进一步使用 env 白名单和 `PYTHON_DOTENV_DISABLED=1`。此前“未发现进程 Key”不等于 MacBook 没有配置。

真实比较仅按原后端路径读取 `.env` 中这一个变量，进程级传给 SDK，未加载数据库/券商变量，未复制私有配置到新 worktree。已在调用前向用户说明根因；实际返回型号确认两个新模型均可访问。未读取其他项目 Key、创建 Key 或修改 Mini。

## 有限真实 A/B

脚本：[gpt6_model_ab.py](../scripts/gpt6_model_ab.py)；原始冻结来源清单：[manifest](../tests/fixtures/v42_real_research/manifest.json)；脱敏结果：[model_ab_20261023.json](acceptance/gpt6/model_ab_20261023.json)。仅同一批公开技术证据和无证据对照，无私人持仓、正式目标、券商调用或数据库写入。

执行 24/24 次 HTTP 请求，无追加调用；最大预算 $3。请求前对所有实际尝试（含 SDK 重试）计数/预留文本输入上界及输出上限；初次标准价预留 $0.496215；加入缓存写入保守余量后上界 $0.620269（脚本已在发送前使用该余量），实际 usage 按标准非缓存价估算 $0.056344。仅官方固定 endpoint、四个指定对照型号、无工具、n=1、`store=false`。评估是组件 Prompt/最终投影对照，不是完整 PEER 端到端或生产验收。

| 型号 | 次数 | 输入/输出 Token（输出含推理） | 调用耗时中位数 | 估算合计 USD |
|---|---:|---:|---:|---:|
| GPT-4.1 | 4 | 2747 / 497 | 1.751 s | 0.009470 |
| Sol medium | 4 | 2743 / 3305 | 19.421 s | 0.038536 |
| GPT-4.1-mini | 8 | 13615 / 647 | 1.529 s | 0.006482 |
| Luna none/low | 8 | 13607 / 990 | 2.702 s | 0.001856 |

结果及可评估边界：

- Planning 完整合同通过：旧 1/4、新 4/4；新 4/4 路由符合预期。初版 runner 将 JSON 与语义合同失败合并记录，旧失败样本未留模型原文，因此无法再区分独立 JSON 解析率和路由准确率；结果文件明确 null/测量限制，脚本已分离指标，没有为补数突破 24 次限制。
- LI/PDD 技术解释：旧各 1 个候选、均因定性安全过滤丢弃；Sol 各 4 个候选、各保留 3 段（合计 6/8）。保留内容讨论日/周趋势分化、修复失败风险、条件式减仓理由和反证，引用均可映射到原合格技术证据，并明确不能推断经营恶化。增加价值依据是可核对推论/引用与保留情况，不是长度。
- 美团已有缺失交易日，原证据仍 unknown；美团与 LI 空证据对照均 0 个专业解释，新旧都没有补造事实；L2/L3/L4 全部保持禁止。
- Reviewer 对明确危险样本均给 0 分；对简短安全降级句，旧给 0.6、新给 0.3。该句不是校准过的完整专业报告，不能据此计算误判率或宣称提升。原 4.2 合格输出通过确定性硬校验直接 pass，本身不会走该评分旁路；评分不能覆盖专业解释合同失败。需要自然质量样本继续校准。
- AV 冻结来源缺正式披露时间，不能伪造“真实经营证据齐全”来测试商业逻辑深度；该单元格明确未验收。合格经营证据的原链路仅做隔离合成合同回归，不代替真实经营解释验收。
- Sol 技术段落有引用价值，但主模型成本/时延增加，缺证时也有额外输出消耗；Luna 成本下降而中位时延增加。未证明所有任务全面提升；未测完整链路 P95、Reviewer 自然误判率及生产三标业务。

## 安全、回归与交付

新增专项：[test_gpt6_models.py](../tests/test_gpt6_models.py)，覆盖配置/回滚、真实 SDK 请求序列化、推理预算、拒答/截断、错误分类/重试、结构化提取、预算与冻结来源资格。原 PR #40、专业解释、目标、issuer/snapshot/守恒及 Web/MCP/Explain 最终过滤测试继续保留。

本地已完成：首轮定向 178 passed；全量 pytest（首轮实现）1987 passed、7 skipped；M5 18/18、公网尝试 0；frontend lint/build；Chromium 35 passed；两个 MCP HTTP 11/9 passed、协议 19 passed。原部署 guards 144 passed、2 skipped，同步/持仓修正 61 passed。新增测量/错误边界补充测试及独立审查见 PR 最新验证记录；最终以候选 exact HEAD 四项 CI 为准，不用阶段测试冒充最终 Gate。

合并/发布分别授权。本分支结束在新 PR 待评审，不自动合并、Tag 或部署。发布前还需审查真实模型时延/成本取舍、原 ARM runtime/Key 权限、自然 Reviewer 校准；获部署授权后与数据修复统一做 LI/PDD/美团真实业务复验，检查原来源缺口、专业解释实际保留、Web/MCP/Explain 一致及超时降级。不激活阶段目标、不重同步券商、不制造订单。

## 独立审查收口

独立只读 AI 工程审查定位 P2：共享适配把 SDK 错误转为 LLMCallError 后，原单标/组合的 SDK 专用 except 未覆盖，落到“未知错误”fallback。已在原 reason/chat/通用组合入口恢复中文安全降级，保留 HOLD/空 payload 及错误状态；新增超时/权限/配额/参数/截断回归，不暴露 Provider 原文或修改资格门槛。另补 A/B 预算缓存写入余量；24 次原始比较不重跑，结果文件保留测量历史。最终修复提交、复审与 CI 证据由 PR #41 最新记录提供。

后续独立复审确认业务 fallback 修复有效，并发现评估 CLI 在低预算耗尽时可能将部分运行标为完成。已增加预算耗尽状态透传、立即停止及 INCOMPLETE 标记；Provider 部分失败也不再标比较完成，新增 CLI/适配联动隔离测试。本次已执行的 24 条均成功返回，原比较数据不变，没有新增付费调用。

## MCP 模型配置穿透收口

最终核验发现原 BusinessClient/worker 只转发 Provider/目标相关白名单，新 profile/effort 不会进入业务进程，且先读财富再分析时可能提前缓存默认模型。已仅扩展五个非交易模型配置键：父进程按原白名单透传、analysis_env_file 在任何业务导入前加载模型策略；Provider Key 仍按原分析范围加载。策略变更须重启获授权 worker，运行中不把新的 profile/effort 与已缓存模型 ID 混用。没有把 Key/券商凭证从父进程直接继承，也没有放开 JSON 中的交易开关。真实子进程先读后分析验证 legacy 回滚与 high/low；独立 MCP SDK 验证父子白名单及交易开关仍 false。生产配置及 worker 未操作。

MCP 配置优先级经独立审查补齐：operator 文件一旦声明任一模型策略键，就作为整套模型策略的唯一来源，清除父进程同组遗留值后按 profile 默认补齐；只含 Provider 配置的旧文件继续沿用父策略。模型字段类型非法显式拒绝，不能被过滤后误当默认。先读后分析子进程回归同时注入冲突父配置，确认 legacy/high 配置不混用。


## 发布前最小观测收口（2026-10-23，待合并候选）

PR #41 已按授权 ff-only 合并，main 为 `529fc4db0dbd4b97217c82c1d9cf46592b16601e`，合并后 [四项 CI](https://github.com/song-buaa/WealthPilot/actions/runs/39922300579) 成功。本节分支 `codex/gpt6-safe-metadata` 从该最新 main 开始；只解决观测缺口，不更改模型选择或生产环境。

这是 L2 跨模块返回契约扩展。原 owner：`llm_config` 负责请求，原 PEER 负责编排，MCP/Web 负责隔离传输，Explain 负责最终证据投影。新增 [llm_observation](../app/llm_observation.py) 仅做无 I/O、无 SDK 依赖的有界安全元数据收集/投影；不建数据库表、后台服务、日志系统、Provider 或第二套分析引擎。

### 字段与语义

结果可选 `llm_observation`，版本 `wealthpilot.llm-observation.v1`，scope 为 unified_generation_adapter_only。仅汇总当前 PEER 上下文经统一生成适配器发起的调用；独立后台标题/记忆任务、Embedding、专用公开搜索 Provider 及其他外部数据费用不包含在该合计中，不作为全账户费用账本。内容为：

| 范围 | 字段 | 依据/未知语义 |
|---|---|---|
| 分析 | analysis_run_id、status、collection_status、elapsed_ms、call_count、records_truncated | 服务端随机 UUID；不使用账户、conversation、snapshot 或请求文本生成 ID。elapsed_ms 是服务端分析边界耗时，不冒充浏览器/Tunnel 端到端时延 |
| 单次调用 | call_id、peer_stage、role、endpoint | 原 Planning/Executing/Expressing/Reviewing 装饰器与 ContextVar；线程继承上下文，多标共用同一分析 ID；role 为 main/light，未知为 UNKNOWN |
| 模型 | requested_model、returned_model、reasoning_effort、returned_reasoning_effort | 前者来自实际适配后参数，后者仅取 Provider 对应字段；Chat 未回传 effort 时返回 UNKNOWN，不把请求配置当 Provider 确认 |
| Usage | usage_status、input_tokens、output_tokens、reasoning_tokens | 仅 SDK Usage 数值；缺失用 null/UNKNOWN，不填零。推理 Token 已在 output_tokens 内，不重复收费 |
| 传输 | sdk_retry_count、transport_attempt_count、elapsed_ms、status、error_category | SDK 2.29 raw-response retries_taken / 最终错误请求的 x-stainless-retry-count；一个调用可以有多次传输；JSON 业务重试是新的调用 |
| 费用 | cost、totals.cost_status、estimated_cost_usd、price_version、price_source、basis、actual_bill | 版本化标准短上下文文本参考估算，实际账单始终 UNKNOWN；价格条件/Usage 不可确认则 UNKNOWN |

计数区分：`call_count` 为适配器调用次数；参数拒绝记录 rejected、传输为 0；未观察到调用时，计数为 0，但 Usage/费用未知。进程失联时 call_count=null、collection_status=UNAVAILABLE，不能声称没有请求。正在运行的线程在分析超时后不再改写已返回记录；in_flight、超出 128 条有界记录等使汇总保持不完整/UNKNOWN。

错误继续沿原 LLMCallError/安全降级：权限、额度、限流、网络、超时、参数、拒答、空输出、截断、型号不符可识别；原已知 MCP 业务/权限错误保持 ToolError，不转换成成功分析。元数据自身采集失败标 observation_unavailable，不替换专业结果或打开交易边界。

### 采集和跨进程链路

1. 原 `run_chat_stream_v3` 外层建立分析上下文；原四 Agent 仅增加阶段标记。`asyncio.to_thread` 继续沿现有路径继承上下文，多次调用共享同一 Collector，不更改 Prompt、Skill、数字/资格或报告。
2. 原 `app/llm_config.py` 在该上下文内使用 SDK 的 raw-response API，保留原解析及至多一次 SDK 重试。采集只读 Usage、型号与计数；不安装 HTTP 日志 hook，不改共享传输、不修改请求体或把资产发到遥测服务。
3. MCP 父进程生成 ID，放入**内部** JSON envelope，worker 传入原 analyze；模型策略仍走原五键和 operator 文件，交易开关仍强制只读。AnalysisRequest 不增加调用方 ID、遥测选项或授权字段。父进程核对成功/失败记录 ID，拒绝错配。最终 AnalysisResult 顶层包含安全元数据，独立 SDK 环境不需要 OpenAI 包。
4. Web snapshot 父进程生成 ID，经原 stdin JSON 到隔离 worker。原私有 stdout/stderr 抑制保留；结果只增加安全字段，原协议仍是一份 JSON。最终 SSE done、Explain 与既有 assistant_metadata 通道复用同一投影。正常 Web 的 done/error 与原 Explain store 也包含该独立字段；历史证据失效时保留安全运行元数据，但原报告仍经过既有历史降级。
5. 任何原始模型 payload 中的同名字段都不采信。接收边缘仅投影封闭枚举、已知型号格式、UUID 与有界数值，不返回 prompt、原文、Provider ID、异常 body、账户、资产、交易或 Key；未扩展 stderr/stdout 原始日志。

验收方可将返回的 `llm_observation` 单独存为私有 0600 JSON；无需保存完整分析上下文。该候选不自动建立新的持久化日志目录或改变生产日志策略。MCP 调用方可直接读结构化结果，Web 可通过现有 SSE/Explain API 获取；不新增 UI 监控面板。

### 价格版本与限制

冻结版本 `openai-standard-text-reference-2026-10-23-v1`，[官方定价](https://developers.openai.com/api/docs/pricing)：Sol 标准短上下文输入/输出为每百万 Token $2/$10，Luna 为 $0.10/$0.50。只对已确认官方默认 endpoint、default service tier、已知返回型号及不超过 272000 输入 Token 的文本调用提供参考估算。工具/functions、图像/音频、非标准 endpoint/tier、未知 Usage 或型号不符均 UNKNOWN。

参考值明确排除缓存折扣/写入、区域附加及其他计费调整，不能用作发票/实际账户扣费。价格版本不可自动覆盖；将来价格变更需重新核验版本。SDK 重试可能产生无法取得的先前用量：单条记录只覆盖最终尝试，汇总费用在存在重试时为 UNKNOWN，保留 `retry_usage_coverage=final_attempt_only`。未知模型/旧 profile 不套用新模型单价。

[官方推理 Usage](https://developers.openai.com/api/docs/guides/reasoning) 与 [Python SDK 重试](https://developers.openai.com/api/reference/python) 为字段/重试口径参考；实际 raw-response、截断 Usage 和错误请求计数通过本仓库 SDK 2.29 MockTransport 验证。没有新增付费请求或重跑此前 24 次 A/B。

### 回归与审查

[观测专项](../tests/test_llm_observation.py)：Sol medium/Luna none/low、Chat/Responses、跨线程阶段、并发分析隔离、SDK 真实重试/错误、截断/空输出/型号不符、未知 Usage/价格、超时后迟到线程、无敏感信息及不改变专业结果。[Web 隔离回归](../tests/test_v42_snapshot_chat.py) 覆盖固定输入、关联 ID、最终过滤、Explain/历史元数据及超时 UNKNOWN；[独立 MCP 协议](../pocs/chatgpt_skills/test_v42_protocol.py) 覆盖真实子进程 ID、模型 Key/交易隔离、返回 Token 字段、错误合同与客户端伪造字段拒绝。

独立只读审查使用 Codex 审查专用入口；发现并修复：零调用误报零成本、已知 MCP 错误语义、错误记录 ID 错配、原交易意图解析的 Planning 归属和 legacy functions 计价。后续复审补齐了接收端费用重算（拒绝伪造金额）和 SDK raw Responses 格式异常分类，并为跨 yield/跨任务关闭增加上下文隔离回归。首轮 M5 的构造器替换夹具兼容问题已补回归，修复后 18/18、公网尝试 0。最终全量/HTTP/浏览器/四项 CI 与复审结果以本候选交付记录为准。

### Mac mini SDK 部署交接（不执行安装）

以下环境事实来自 2026-10-23 已完成的 Mini **只读预检**，本候选没有重新连接或修改生产：实际业务仍为 b6b5a17，完整 Backend/专业分析环境 OpenAI 3.31.0，只读 business 为 2.29.0；Python 3.11.17 arm64。二者 pip check 通过，不等于可以互换。

| 项目 | 最小要求 |
|---|---|
| 完整 Backend / 专业分析环境 | 包含 uvicorn、FastAPI、ib_async、原知识/PEER/市场/调度等完整依赖；现 3.31.0 环境不原地降级、不覆盖、不改 symlink 伪装新环境 |
| 只读 business 环境 | 原 ARM 2.29 导入与 pip check 已通过，但缺 uvicorn/ib_async 等完整运行依赖；不能拿它替换完整 Backend。只读数据库/进程权限与包安装完整性分别核验 |
| SDK 约束 | requirements、business/web 哈希锁继续 openai==2.29.0；wheel SHA256 b7c5de513c3286d17c5e29b92c4c98ceaf0d775244ac8159aeb1bddf840eb42a。httpx、pydantic/jiter 等按原对应角色锁验证，不在线解析最新版本替代 |
| 完整依赖闭包 | 根 requirements 覆盖业务包但包含范围约束，并不是新增的完整哈希锁。新完整环境还须沿原完整 Backend 构建机制留存精确包/ARM wheel 清单并核对 SDK 约束；不能拿只读 business 锁宣称完整依赖已闭环，不能本轮改锁绕过 |
| 原安装器 | install_release/install-github 目前只构建 mcp/business/可选 web，**不会创建 full-backend**。观测代码不要求修改该安装器，本轮也不改。正式部署方案须补充或复用已存在的独立 full-backend 构建步骤；若只能沿用旧共享环境，则发布继续阻断，需单独评审部署工具扩展，而非临时 pip/换锁 |
| 新隔离 ARM release 验证 | 精确源码 SHA+四 CI、原生 Python/二进制架构、SDK 2.29/raw-response Chat/Responses 禁网探针、pip check、完整 PEER/知识依赖被动导入（不执行 backend lifespan）、协议字段/私有 stdout 护栏、临时合成库+原回归；生产权限/模型访问及真实 E2E 单独授权后验收 |
| 组件引用 | Backend、MCP 父脚本及其 analysis_business_python/工作目录/常驻子进程须协调到同一候选；前端 tree 未改，不需要模型观测 UI 切换；不改 global current 而误切 Tunnel，也不动 Gateway/国金/WAL/目标/交易配置 |
| 恢复 | 留存旧代码/入口/解释器，确认数据库兼容后只恢复这些组件；不恢复/覆盖活动数据库或 WAL，不隐式转旧模型，不同步或创建订单 |

生产 GO/NO-GO：本候选通过工程 Gate 后仅为**观测代码可评审**；尚未合并、未部署，不能宣布生产阻断已解除。仍需完成授权后的独立完整 ARM 2.29 环境与安全切换、Mini Key 新模型访问和 240/270 秒真实端到端验收。LI/PDD/美团仍按原 PR #40 来源冲突/缺失和 L1–L4 条件逐标验收，不激活目标、不要求全部 L3、L4/交易始终禁止。

最终本地候选：定向169、全量2036 passed/7 skipped、M5 18/18（公网0）、独立MCP协议23、HTTP 11/9、浏览器35、frontend lint/build、部署guards144 passed/2 skipped、文档检查及diff检查全部通过。独立最终限定复审无明确P0/P1/P2。机器记录见[观测验收矩阵](acceptance/gpt6/observation_validation_20261023.json)，准确SHA与四项远端CI由本PR绑定；这些是隔离工程证据，不是真实三标业务验收。
