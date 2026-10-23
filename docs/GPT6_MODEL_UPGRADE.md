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

下表为代码请求点，不把通过同一函数调用的多种意图重复计为独立模型引擎。主=Sol，轻=Luna；旧 profile 的对应角色为 GPT-4.1 / GPT-4.1-mini。

| 入口 | 请求点/职责 | 默认角色及 API |
|---|---|---|
| decision_engine/llm_engine.py | reason、review_portfolio | 主；Chat |
| 同上 | chat、analyze_allocation、analyze_performance | 轻；Chat（保留原任务分档） |
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
| backend/graph/tools.py | 原组合调整字段提取 | 轻 none；Chat |
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
