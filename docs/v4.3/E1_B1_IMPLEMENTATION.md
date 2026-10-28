# WealthPilot 4.3｜E1 投资假设与人工复核 MVP

日期：2026-10-28。原始已集成基线（fetch实际结果）：`b386bab10a72ee09253bfa5bf746cb89d130354b`；独立分支 `codex/e1-manual-mvp`。

E1-B0/B1/B2/B3 是 **4.3 的内部实施阶段**，不是独立产品版本。B1 为独立分支上的本地开发；未合并、未部署，不代表 ChatGPT 线上已能自动保存研究。S1/S2 不重开。

本文保留B1交付时点。后续B3-1本地新增默认关闭的研究预览/确认写桥接，当前配置、日常用法与未接生产的边界以 [B3-1说明](E1_B31_RESEARCH_SAVE.md) 为准；原CLI继续兼容。

## 实现与权威

| 对象 | 实现 / 复用 | 边界 |
|---|---|---|
| 假设、证据、版本、Review | [contracts](../../backend/services/investment_thesis/contracts.py)、[store](../../backend/services/investment_thesis/store.py)、[service](../../backend/services/investment_thesis/service.py) | 新研究域，不是第二套账户/交易/市场数据平台。 |
| 原专业分析 | [AnalysisRequest/Result](../../pocs/chatgpt_skills/analysis.py)、[原 start/get](../../pocs/chatgpt_skills/server.py)、[PositionJobs](../../pocs/chatgpt_skills/position_jobs.py) | 不改原契约/PEER，不新增模型。完整原结果与人机判断分别保存。 |
| 直接敞口 | [E1 pure function](../../backend/services/investment_thesis/exposure.py)、[U1](../../app/asset_views.py)、[issuer owner](../../backend/services/instruments/issuers.py)、[既有目标配置](../../backend/services/stage_targets.py) | 同视图全组合分母、发行人市值；不加总ADR与普通股份数，无ETF穿透，不扩并行issuer映射。 |
| 用户入口 | [CLI](../../backend/services/investment_thesis/cli.py)、[只读 MCP](../../pocs/chatgpt_skills/investment_thesis.py) | CLI显式写、MCP只读查；无后台任务、同步或交易。 |

独立 SQLite 路径由 `--store` / `WEALTHPILOT_E1_DB_PATH` 显式提供，必须 init，不自动创建/迁移现有库；目录0700、文件0600、拒绝符号/硬链接及与业务DB同路径。每条记录绑定配置的 portfolio scope。`theses` 仅存头版本指针；`thesis_versions` 不可覆盖；`decision_reviews` 追加执行占位和完成事件，原事件也不可覆盖。SQLite触发器拒绝版本/Review更新与删除，payload附SHA-256；这不是对有文件写权限用户的防篡改保险库。

`expected_revision` CAS；同scope request_id+相同内容重试返回原记录，换内容或跨操作复用拒绝。先保存Review执行占位再调用分析，失败/进程中断不自动重放；RUNNING可能表示中断后状态未知，须人工检查，不自动重跑。

证据通过claim_id及supports/opposes/contextual关联。FACT标签和手工核验参考是输入者的声明，不等于系统独立核实。published/known/fetched/time_precision分别保留；未知不填当前时间。原决策时点及复核时点各有资格投影；晚于复核或时点未知的来源正文不送原PEER。用户原话不可改写，新增反思另存；一年预期到期只标overdue及用户反馈，不自动改为三年或卖出。

## 可复制的合成闭环

在本分支根目录、项目Python 3.11环境运行。示例全部是**合成文本与伪来源**，不是美团/NVIDIA/ORCL真实研究、持仓、成交或风险偏好。无需Key、原业务库、后端启动或联网。

```bash
E1_DEMO_DIR="$(mktemp -d)"
E1_DEMO_DIR="$(cd "$E1_DEMO_DIR" && pwd -P)"
export PYTHON_DOTENV_DISABLED=1
export WEALTHPILOT_E1_DB_PATH="$E1_DEMO_DIR/research.db"
python -m backend.services.investment_thesis.cli --portfolio-id 7 init
python -m backend.services.investment_thesis.cli --portfolio-id 7 save --input docs/v4.3/examples/e1/meituan-v1.json --expected-revision 0 --request-id demo-v1
python -m backend.services.investment_thesis.cli --portfolio-id 7 save --input docs/v4.3/examples/e1/meituan-v2.json --expected-revision 1 --request-id demo-v2
python -m backend.services.investment_thesis.cli --portfolio-id 7 review --input docs/v4.3/examples/e1/meituan-review.json --analysis-file docs/v4.3/examples/e1/peer-result.json --request-id demo-review
python -m backend.services.investment_thesis.cli --portfolio-id 7 history --thesis-id synthetic-meituan
python -m backend.services.investment_thesis.cli --portfolio-id 7 diff --thesis-id synthetic-meituan --from-revision 1 --to-revision 2
python -m backend.services.investment_thesis.cli --portfolio-id 7 reviews --thesis-id synthetic-meituan
```

示例结果：v1/v2保留同一原始回忆；v2新增counter反证；Review原请求SUPPORT被限制为UNKNOWN，原因包括原分析partial、复制来源未证、仓位/个人上限/估值未知；原AnalysisResult原样保留。即使“股价更便宜、原想法未变”，也不能自动升级为加仓支持。没有专业分析时可省略analysis-file，必须在Review输入中写明not_run_reason。

需要情景计算时，Review命令显式提供 `--asset-view <已授权U1视图JSON副本>` 与 `--scenario <情景JSON>`。示例见 [合成情景](examples/e1/scenario.json)。服务不查询真实持仓，不调用prepare；输入副本只作条件性资料。合格视图中内部现金投入分母为D-C，外部注资为D+F-C；发行人分子为E+X。未知来源、全分母/账户覆盖、身份、价格/FX或时效缺口时CANNOT_QUANTIFY；ETF间接敞口始终UNKNOWN。无真实上限仍可展示合格直接比率，但confirmed_limit_status=UNKNOWN；测试输入hypothetical_limit不变成个人风险政策。仅既有本地StageTargetStore有效确认的同scope发行人目标可给阶段目标比较，仍不覆盖正式纪律或授权交易。

输入契约详见 [types](../../backend/services/investment_thesis/contracts.py)；价格/等待成本写证据或未知，不允许伪概率、目标价或买入股数字段。原持仓时间缺证时不能用Position.created_at冒充首次购买/恢复时间。

## 只读 MCP 与原分析

原server显式设置 `--e1-db-path <私人研究库>` 或 `WEALTHPILOT_E1_DB_PATH` 时，按现server portfolio_id注册两项；不配置则原工具集合、参数、注解与行为不变：

```json
{"name":"get_investment_thesis","arguments":{"thesis_id":"synthetic-meituan","revision":2}}
```

```json
{"name":"get_investment_reviews","arguments":{"thesis_id":"synthetic-meituan"}}
```

省略revision返回全部版本及各版diff；也可仅用symbol查询最新版本（thesis_id/symbol恰选一）。查询只读独立研究库，不执行模型/Provider/业务Worker，不写任何数据库；非法/不可读库显式失败，不伪装为空数据。不能由工具参数选择库路径或portfolio。

自然语言买入/加仓/持有讨论先读已有假设与Review，再走原 `start_position_analysis`→相同job_id的`get_position_analysis_result`，保存仍需本地显式CLI。复制的AnalysisResult标USER_SUPPLIED_ANALYSIS_COPY，schema/hash不能证明其来源；业务状态、evidence、quality、limitations、queried_at完整保留，partial/unavailable不升级为确定性支持。E1保存后不受原job的两小时TTL/重启影响；历史记录不因此取得当前资格。

`review --run-analysis --business-python <原business Python> --mock-database <已标记的合成业务库>` 是B1的可选本地桥接，复用原BusinessClient/Worker，需要在既有独立MCP Python环境运行CLI；不接纳real_config。真实研究继续沿既有已授权MCP。注入executor测试标INJECTED_FIXTURE_RESULT；直接Worker标ORIGINAL_WORKER_RESULT但保留synthetic=true，不能称真实业务验收。失败记录不自动重跑，不用同步analyze_position兜底。

## 离线验证与后续集成

定向测试：[研究记录](../../tests/test_investment_thesis.py)、[纯暴露](../../tests/test_thesis_exposure.py)、[独立MCP协议闭环](../../pocs/chatgpt_skills/test_e1_protocol.py)。后者实际运行CLI子进程、MCP Client读v2、保存原结果副本、取Review/diff并重启读取；socket guard及Worker哨兵断言零调用。它作为既有MCP CI suite的附加用例，不新增Gate。

```bash
python -m pytest tests/test_investment_thesis.py tests/test_thesis_exposure.py
WP_BUSINESS_PYTHON="<business-python>" "<independent-mcp-python>" -m pytest pocs/chatgpt_skills/test_e1_protocol.py
```

完整代码Gate及环境沿 [dev_setup](../dev_setup.md) / [治理](../PROJECT_GOVERNANCE.md#6-质量门与当前基线)。原读取/start-get/交易边界另跑原MCP协议回归；不以合成通过替代生产或案例研究。执行结果、精确提交及私有日志见本次4.3本地交付RESULT，未通过项不得写成PASS。

4.3后续计划：等待4.2依赖稳定，再从最新集成main定点对齐MCP注册和文档、处理实际冲突、执行集成回归，按4.3发布流程取得推送/合并/部署授权。本轮不自动集成，不依赖未合并4.2旁支。B2仅建议以美团→NVIDIA→未参与设计的新真实案例，核对原件/known_at、恢复假设/情绪退出及明确授权快照；自然语言一体化保存做最小交互验证。B3触发/通知未实现、未启用。Redstone与公司Tovest不变。

本轮实测：全量pytest 2360 passed / 7 skipped；此后仅E1新域做定点修正和补用例，最终定向35 passed；最终独立MCP闭环2 passed，原MCP协议46 passed；Offline M5 18/18、公网尝试0；frontend lint/build与文档检查通过。未发现需与clean main对照的失败；未执行远端CI、真实Provider或生产验收。
