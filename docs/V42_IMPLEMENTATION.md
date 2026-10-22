# WealthPilot 4.2 开发与验收记录

## Step 0：基线与最小架构勘探（2026-10-22）

- fetch 后 local main = origin/main = `2dfc0c43bfb0716e2d90a6a548cf9eb4729cf90b`。
- 4.1 RELEASED 的业务 SHA 为 `e0df7211f462f07ec0147f40e84f65321d0f6b50`，是 main 祖先；两者差异仅 Markdown。原结项明确不创建 Tag，远端没有 v4.1 Tag，符合发布约定。
- 原工作区位于旧 PoC 分支，有用户未跟踪文件，未更改。4.2 在独立 worktree 从最新 origin/main 建 `feat/wealthpilot-4.2-single-position`。
- 生产只读 MCP 证实固定 `asset-view:v1:64` 存在，partial、过期、来源时间未知等告警仍在，`can_propose_rebalance=false`。不把它或历史 v1:58/v1:61 当实时可信资产输入，不创建新视图、不同步券商。
- 生产 SSH 连接被远端关闭，当前组件启动 SHA 未独立复验，标为 UNKNOWN；发布记录的生产 SHA 不能冒充本轮运行观察。未发现已证实的代码基线冲突。生产部署/真实验收另行授权。
- 私有 PRD 原文在用户指定目录，不复制个人资产/目标到公共仓库。测试使用公开描述的合成回归数值，绝不代表真实账户。

| PRD / 能力 | 现有 owner / 调用链 | 判断与最小修改 | 依赖 / 风险 |
|---|---|---|---|
| analyze_position / review_portfolio | MCP analysis → decision_service_v3 → PEER / invoke_skill | 复用原专业分析；补确定性资格与结构化结果 | 兼容原 schema；L2 跨模块变更 |
| P0-A 研究/基本面 | market_data schema / av_fundamentals_service、原 research / RAG | 查询日冒充数据日；seed 仍可进入推理。扩展统一证据、冲突和消费过滤 | 不新建研究引擎；真实源可不可用分别报告 |
| P0-B 技术 | tiger_kline_service、execution_plan/kline_provider、Pattern Evidence | 原日线指标未保留序列、日期使用抓取日；复用 registry 和指标计算补周线/来源/参数 | Provider optional；seed 不获真实资格；Pattern sidecar 保留原治理 |
| P0-C issuer | instruments/issuers、U1 固定视图 | 已有 LI/PDD/美团映射与分母；复用成员并保持港股通双行 | 禁止跨快照混用、CNY估值不充当HKD报价 |
| 阶段目标 / 纪律 | AllocationService defaults、LoadedData.rules、RiskEngine | 三种权威分开；分析请求携带版本化确认目标，不落正式纪律 | 不改 RiskEngine 交易行为/真实纪律 |
| P0-D Planner | graph/decision_graph → PlanningOutput | 原直接 json.loads 异常只写 rationale；修格式解析、显式 fallback trace | fallback 不等于证据缺失；L1–L4分别门控 |
| P1-B 情景 | AllocationService + U1 issuer/wealth | 原 owner 内增加纯计算，显式账户/币种/费用/贷款约束 | 资产守恒、分母随还贷变化；无数据库写入 |
| P1-A/C ExitPlan / 报告 | 原 PEER / Expressing / MCP adapter | 分析域输出建议对象与七层报告；逐级限制价格/数量 | 不调用 ActionDraft/Batch/订单或交易链接 |
| 外部来源 | Tiger/AV/Futu、原研究/RAG | 本次不调用付费模型或外部行情；真实可用性 runtime-pending | LI/PDD/HK真实日周线与新财报必须发布后另验 |

切片：Step 1 证据/技术/Planner/资格；Step 2 固定输入的跨账户目标与情景；Step 3 ExitPlan/报告/兼容回归。全程复用 PEER、原行情 Registry、issuer、Portfolio/Wealth、原交易人工确认。零生产迁移、零新订单/同步/部署权限。

## Step 1：证据与资格

- 原市场 Schema 扩展 provenance；AV 不再用抓取日冒充估值日；Futu 使用源 update_time（市场本地时区），缺失保持未知。
- 原 KlineProvider Registry → 原 MA/RSI/MACD → 完成日/周线 OHLCV、来源、参数、观测支撑压力；未完成周、无效 OHLCV、缺交易日、Seed 与过期不获真实技术资格。
- 原 PEER 在 Expressing 前通过新增内部 Skill 评估证据，原模型只消费已核验证据；确定性输出过滤移除不合资格的模型价格、数量、估值及伪精度，保留原意图标签/新建仓标记。
- Planner 接受 JSON / 单 fenced JSON，拒绝非法 route；fallback trace 不升级研究证据。L4 恒为未授权，L3 不能绕过 can_propose_rebalance=false。
- 隔离测试：证据新用例 15 passed；原分析/D1/U1 73 passed；合计 88 个用例已通过（最后一次只重跑修复影响的15项）。Offline M5 18/18，公网尝试0。
- 初轮测试暴露的 parser 作用域、港股开市期间已收盘日线语义及旧断言已修复；不把初轮 FAIL 隐去。完整质量 Gate 在最终候选统一执行。

## Step 2：公司敞口与资金去向

- 原 AllocationService 提供只读情景入口，引用原U1 issuer成员/投资分母与Wealth总资产负债；同公司跨账户市值汇总但数量、成本、通道不合并。
- 分析请求可携带 target_versions 与 scenario_legs，旧请求字段全部兼容。阶段目标有版本、前驱、确认、期限与内容摘要；同版不同内容/不明前驱/未确认拒绝。正式纪律、确认目标、系统默认三者分开，现金约束冲突显式阻断精确建议。
- 逐腿按position_id/account_ref/issuer_id/snapshot_id验证；还贷按净所得及已纳入净资产的CNY贷款余额封顶，重复卖出、跨快照/账户、负/NaN参数与最低流动性失败均拒绝。费用/税/换汇假设显式，不伪造真实交易费用。
- 合成回归：100 passed（Step1及原分析/D1/U1/情景）；实际来源kind大写兼容修正后情景12项重跑通过。合成示例非真实账户：1213000投资分母、303800理想，卖出美团161400还贷和PDD81500转固收后，理想约28.89%；境外减仓转固收40900或境内减仓还贷54533.33均回到25%。
- 所有数值是静态假设；不达L2仅标illustrative_unqualified，不提供股数/指令。不同快照不混算，无数据库/正式目标写入。

## Step 3：ExitPlan 与专业报告

- [原PEER编排](../backend/services/decision_service_v3.py)调用[组合函数](../backend/services/position_analysis.py)，不新增模型链路或数据权威。原Expressing只消费核验后的证据；Review后以同一视图重新评估，并在最终文本/done/MCP中应用相同门槛。
- ExitPlan包括issuer/snapshot、阶段目标版本及摘要、独立账户成员、分批条件/资金去向、经营/技术条件、备用规则、七日默认复核期限、证据与不允许动作。复核日不是卖完日。
- L1只给方向/研究条件；L2可以列明确假设的静态金额；L3价格观察带有来源/时间/币种/有效期，股数仅在同一US上市单位、当前FX/报价、明确账户情景及持仓约束下由Decimal规则求整。港股缺交易单位/报价时不猜股数。L4始终阻断，不产生订单/链接/草稿/确认。
- 原研究Repository新增事实投影（图层携带，原文本接口兼容），保留URL/record/真实发布时间及抓取时间；不把as_of抓取钟、叙事或LLM抽取KPI当当前事实。新新闻保留原publication字段，新入库时钟使用明确UTC；旧记录时间缺失不补造。
- 跨账户浓度/情景资格必须用全投资分母重新核验；还贷还需net_worth范围的贷款事实，不能用某单标D1-B资格绕过其他账户缺口。原can_propose_rebalance=false不会被重评升级。
- 三份脱敏报告：[LI](acceptance/v42/LI.md)、[PDD](acceptance/v42/PDD.md)、[美团](acceptance/v42/MEITUAN.md)。它们是SYNTHETIC_FIXTURE，不是生产专业分析或真实行情验收。Mock/Seed均拒绝成为当前数值事实。
- 额外回归发现原国金测试夹具依赖墙钟，在clean main同样可于夜间触发AUTO_WINDOW_CLOSED/旧ready回退；只冻结测试来源及校验时钟，原国金业务代码未变。该模块91 passed。原Pattern回归改为检查新报告和不变的Pattern持久化/done合同。
- 候选冻结前定向回归180 passed；独立MCP 2.3环境17 passed；固定视图/交易确认Playwright32 passed；前端lint/build通过。最终全量和精确SHA记录在下节，不以这些数量替代生产验收。

## PRD 完成矩阵

以下为初次本地交付评估；远端独立审查发现的实际缺口与修正结论以文末「远端工程验收」为准。不得继续将本表的初评作为全需求完成证明。

| 需求 | 开发状态 / 可复测证据 | 生产限制 |
|---|---|---|
| P0-A 来源/时效/冲突 | 已实现：统一证据、Seed/Mock/未知时间拒绝；52周、PE/EPS/币种、季度归属、利润/现金流差异；新官方披露优先 | IR/供应商实际字段与最新财报未在线验收；AV估值时钟未知保持缺证 |
| P0-B 日线/周线 | 已实现：原Registry、闭合OHLCV、MA/RSI/MACD/量价/观测区间、参数/币种/完整性及重算校验 | 港股源依原Tiger可用性；AV compact不足周线历史时显式不足；不新增付费数据源 |
| P0-C 公司敞口/阶段目标 | 已实现：复用issuer全成员/分母，版本目标与正式纪律/默认值分开；冲突透明 | 当前已知资产时间/完整性限制不因开发解除；目标由请求携带确认版本，不写生产纪律 |
| P0-D Planner/L1-L4 | 已实现：JSON格式/route验证、fallback trace、逐级blockers与置信上限；不可自证或绕过false | L4本期未获授权，恒不允许；原交易保护全部保留 |
| P1-B 资金情景 | 已实现：顺序资金去向、全分母与还贷资格、费用/税/FX/流动性、贷款封顶/资产守恒、账户/快照隔离 | 费用与划转为显式静态假设，不是实时执行或收益预测 |
| P1-A ExitPlan | 已实现：阶段/账户/条件/去向/备用/复核期限/质量；金额和数量分级 | 无新订单/交易准备/确认调用；不足时字段为空，不保证某真实账户有L3资格 |
| P1-C 七层报告 | 已实现：目标敞口、经营估值、技术、事件、情景、复核、来源质量；Web/MCP共用结果 | 真实三只股票的设备/新源验收NOT_RUN，不把合成报告当生产PASS |

## 发布评审边界与建议

1. 开发分支只做本地提交；无push/PR/远端CI、合并、Tag、生产部署或迁移授权，GitHub CI标NOT_RUN。建议授权push/PR后检查exact HEAD的四项CI，feat分支不会命中codex/** push触发器，面向main的PR会触发原流程。
2. 零Schema迁移、零新依赖。发布仍按原Mini组件安装/切换流程，先重新只读核对实际组件SHA；本次SSH连接失败，因此生产运行SHA为UNKNOWN，文档记录e0df721不能代替实时证据。
3. 不复用过期64作当前精确分析；生产真正验收须由用户另行授权准备新合格固定视图及必要真实Provider调用。不得自行同步、修改手工贷款或正式纪律。
4. 三标逐一验收真实IR/财报、日/周线、市场币种、发行主体/全部账户、目标版本及同一快照；不达资格时验证正确降级。真实订单及prepare/confirm接口仍不在4.2验收范围。
5. 旧图单测在clean main同样存在mock包/checkpointer假设（非默认Gate）；原安装器单测在本机Rosetta/旧Git/目录umask有环境限制。临时原生Git wrapper与umask077可通过9项安装器检查，不改系统配置或生产实现。

补充来源口径：原纪律get_rules缺失/损坏配置时会回落代码默认值。4.2仅增加只读describe_effective_rules，按已加载完整规则与现有配置内容核对来源及摘要；不修改get_rules行为/值，不从配置存在推断新的用户确认。默认/未核验规则与目标差异不再误标为“正式纪律已满足”。

必要的4.1兼容修复：原BrokerKlineProvider忽略已部署配置TIGER_PRIVATE_KEY_PATH，只找代码内旧secrets位置。4.2在同一provider优先读取原配置，保持与Tiger同步的绝对/相对路径语义和旧回退；不改凭证、账户、同步、交易或Provider注册权限。Mock SDK测试证明配置路径被复用，无真实私钥读取/文件写入/券商调用。

## 最终本地验收（2026-10-22）

- 分支：`feat/wealthpilot-4.2-single-position`。业务/测试候选：`f95a72d7cf8743a634071189faff1a6e0595542d`。本节文档收尾是其文档后继，不重跑已冻结业务代码、不冒充远端CI。
- 最后fetch核对：main = origin/main = `2dfc0c43bfb0716e2d90a6a548cf9eb4729cf90b`。原PoC工作区及原7项未跟踪文件未变；用于clean main对照的临时worktree已归档。
- 新增回归共43项（证据21、情景14、ExitPlan/专业上下文8），并包含在下列全量Gate中。原专业推理实际收到同一issuer全部账户/目标版本/情景，不仅在展示层补数据。

| 检查 | 最终本地结果 / 日志 |
|---|---|
| 根pytest（候选f95a72d） | **1739 passed, 7 skipped**；1746 collected；`/tmp/wp42-delivery-pytest.log` |
| Offline M5（候选f95a72d） | **18/18**；public_network_attempts=0；`/tmp/wp42-delivery-m5.log` |
| 额外reader/deploy/shared-DB/restricted-Web/sync/ExecutionPlan/market回归 | 307 passed, 2 skipped, 2 deselected；`/tmp/wp42-final-extra.log`；新增Tiger配置分支另以26项定向验收补齐 |
| 原Skill loader | 11 passed；先在独立临时DB建空表，未使用个人DB；`/tmp/wp42-loader-initialized.log` |
| MCP 2.3独立环境：两HTTP入口及当前/U1协议 | 37 passed；`/tmp/wp42-33c-protocol.log`；最后专业上下文变更另有28项原PEER/ExitPlan定向及候选全量/M5覆盖 |
| 共享SQLite真实loopback MCP只读 | 2 passed（额外检查中先跳过的可选SDK绑定场景已补跑）；`/tmp/wp42-shared-http.log` |
| 原固定视图及B3人工确认Playwright | 32 passed；`/tmp/wp42-browser.log`；前端与main无diff，全部使用合成临时DB |
| frontend lint / build | PASS；`/tmp/wp42-frontend-lint.log` / `/tmp/wp42-frontend-build.log`；既有大chunk提示保留 |
| compileall / 工程文档 / diff检查 | PASS；无decision_engine、订单/Batch、同步实现、数据库模型、依赖锁或前端修改 |
| GitHub四项CI / 独立PR review | NOT_RUN：没有push/PR授权，本地检查不能代替远端exact-HEAD CI |
| PowerShell native PortableFake / 固定Tunnel二进制实测 | NOT_RUN（本地环境未提供），待原CI执行，不作为真实Windows/生产验收 |
| 真实IR/财报/行情/三标专业模型及生产SHA独立复验 | NOT_RUN / UNKNOWN；三份报告仅SYNTHETIC_FIXTURE；生产SSH被远端关闭，不隐式刷新券商 |

测试隔离：显式禁用dotenv；各job独立`/tmp` SQLite；Public Demo/Mock；行情禁用；IBKR只读和实盘开关关闭；无外部密钥。独立SDK为现有`/tmp/wp-u1-mcp-env`的mcp 2.3，business Python为项目原3.11环境。安装器测试仅在进程内umask077并使用临时原生Git wrapper；没有改系统Git/权限或共享SDK依赖。

失败/修复追溯：早期全量发现旧Pattern文本断言需按新报告合同更新、国金墙钟夹具及其跨模块复用需冻结；业务实现不改。新增Tiger配置Mock首轮用3690而非原SDK规范03690，被正确过滤；保留修正提交及`/tmp/wp42-keypath.log`，修正后26 passed（`/tmp/wp42-keypath-fixed.log`），最终全量绿色。过渡候选测试曾为修复/冻结取消，不作为最终通过证据。旧backend/graph独立mock单测在clean main同样有包/checkpointer假设问题，未纳入默认Gate；本期Planner新增解析/降级测试及原PEER/M5已覆盖相关新行为。

## 本地提交清单

| 完整SHA | 职责 |
|---|---|
| `96e003ada903eef0996fe4f86f108daec99ca35b` | feat(analysis): qualify single-position evidence and output granularity |
| `f4378900b831d9db5cb1ce5204e5977de2c5e457` | feat(allocation): simulate account-specific exits on fixed asset views |
| `f5106672920692b3ed692494573d90f4e0bae7aa` | test(sync): freeze synthetic source clocks for offline regression |
| `fd14e6fa76f558bccea964b2626ad85ee13fd62f` | feat(analysis): deliver evidence-gated ExitPlan and professional reports |
| `36b90f40eb05410b96ff7d9a8a31fa552540c04f` | fix(analysis): reject listed market currency mismatches |
| `fe9a8fb98653774a10ef9dcc3bac21e392b38988` | test(sync): share frozen clock with launch boundary regressions |
| `33c03dcf0efb559f0b05e9b673be2aa732583284` | fix(analysis): require verified listed-unit EPS basis |
| `09cbd59d3b243d94f1c928e2ad65a6bd45144d38` | fix(market-data): reuse configured Tiger key path for kline evidence |
| `5b7e8f291434f15ce13eada7f6a77f75d0984ec2` | test(market-data): match canonical Tiger Hong Kong symbols |
| `f95a72d7cf8743a634071189faff1a6e0595542d` | fix(analysis): pass issuer targets and scenarios to original reasoning |

文档收尾提交SHA以当前Git HEAD及最终交付消息为准。所有提交仅在本地；没有push、合并、Tag、生产迁移/部署、券商同步或真实订单准备/提交/修改/撤销。下一步建议：独立授权push/PR后跑原四项CI及review；合并、生产组件安装/切换和真实来源验收分别授权。


## 远端工程验收与独立代码审查（2026-10-22）

本阶段用户单独授权push、PR、CI、独立review及必要修复；不授权合并、Tag、生产部署/迁移、券商同步、真实交易准备或订单。本节取代前文“尚无push/PR授权”的历史状态及过度乐观的完成判断。

### 基线与工程事实

- 最新main仍为`2dfc0c43bfb0716e2d90a6a548cf9eb4729cf90b`；是开发分支祖先。初始11个提交线性、无merge。
- 已验证业务候选`f95a72d7cf8743a634071189faff1a6e0595542d`至文档HEAD`d6775e8b1b85b3cde0ba855f3ca9d62718446ee2`只有本文件42行新增。
- 任务worktree已提交内容干净，但存在用户未知的未跟踪`docs/acceptance/v42/归档.zip`；未读取/移动/暂存/上传。原工作区不变。不能声称整个目录无未跟踪内容。
- 已push原开发分支，创建[PR #39](https://github.com/song-buaa/WealthPilot/pull/39)，base为最新main。
- 初始HEAD的[Quality Gates 39771152237](https://github.com/song-buaa/WealthPilot/actions/runs/39771152237)四项全部成功。审查发现该测试集未覆盖的漏洞，故初始绿色不能代替修复后exact HEAD验收。

### 独立发现及最小修复

由三个独立审查任务分别检查证据/Web、配置情景、MCP边界，再交叉复核修复。不是GitHub人工approval，也不是生产业务验收。

| 优先级 | 原始问题 / 最小修复 | 回归入口 |
|---|---|---|
| P1 | Web多标未设置证据、二次比较模型自由输出；现逐腿prepare，最终按同一视图重核并只组合受控报告 | test_v42_multi_policy.py |
| P1 | MCP错路由/错误/超时保留早期自由模型text；严格按工具匹配route并清除失败流结果 | test_v42_mcp_policy.py |
| P1 | 组合仅加警告，缺快照或D1 C=true均可能返回精确建议；Web/MCP组合最终仅保留Owner事实、目标假设、L2静态情景 | test_v42_multi_policy.py / test_v42_mcp_policy.py |
| P1 | 客户端confirmed_by_user自证目标确认，缺目标仍可L3；现目标为request_proposal_unverified，confirmed_stage_targets为空，缺可信确认阻断L3/L4 | test_v42_scenarios.py / test_v42_exit_plan.py |
| P1 | 组合在最终重评前测算、单标嵌套资格未跟随adapter最终重评；现先重核完整分母/贷款/TTL，再重建同一单标结果及嵌套情景 | test_v42_mcp_policy.py |
| P1 | Explain保存LLM原reasoning/risk/strategy，且重新读当前持仓/硬编码FX反推股数；现清原文本，Explain只取同快照证券成员及有效报价 | test_v42_evidence.py |
| P2 | 研究缺出版钟、旧出版钟可被新data_as_of掩盖、抓取钟未来/倒序；现拒绝为valid。无topic不做跨主题替代 | test_v42_evidence.py |
| P2 | Web最终public_view可能升级原false；全部终检保留原veto。Web组合遗漏正式纪律/默认来源，现复用原loaded规则和来源 | test_v42_multi_policy.py |
| P2 | 目标加容忍偏离超过正式集中度上限不告警；补目标纪律冲突。原资金守恒公式无新发现 | test_v42_scenarios.py |

MCP实际SDK新增协议测试`pocs/chatgpt_skills/test_v42_protocol.py`覆盖两工具真实schema、target_versions/scenario_legs完整往返及跨快照拒绝，并加入原CI独立SDK步骤。协议可传递不等于确认来源可信。

### 修正后的PRD完成与合并判断

| 需求 | 独立审查结论 | 仍缺事项 |
|---|---|---|
| P0-A | 部分实现：来源、时间及冲突降级可离线复测 | FundamentalsData/现有AV构造没有自由现金流字段，利润/FCF冲突目前依赖字典夹具；真实新财报/估值源未验收 |
| P0-B | 可复算技术证据及缺源降级已实现 | 真实三标日/周线、市场/币种和供应商可用性NOT_RUN |
| P0-C | issuer/快照/分母及目标假设合同已实现；可信确认未完成 | 当前没有权威阶段目标确认记录；客户端布尔和digest不算确认，不借用交易确认；生产L3因此不可用 |
| P0-D | 路由降级与多入口最终门槛修复；保留false且不授予L4 | 无可信目标时保守关闭L3；不声称精确减仓能力已可用 |
| P1-B | 纯情景资金守恒、费用、还贷分母、账户/快照隔离已验证 | 费用、FX、账户划转和贷款事实实际可用性仍需真实业务验收 |
| P1-A | ExitPlan骨架和调用方显式情景行可用，部分实现 | 常规阶段行未自主选择账户/计算分批金额股数与资金去向；需请求先提供scenario_legs。不能称完整自动形成退出路径 |
| P1-C | 七层事实/门槛报告及Web/MCP最终限制可用，部分实现 | 当前确定性模板丢弃自由模型专业解释，多标改为逐标报告组合；组合保守报告替代旧自由叙事，是4.1体验收缩，尚无完整专业结论质量验收 |

**判定：工程检查通过与完整4.2需求验收是两件事。当前仍有P0-C/P1-A等实际缺口，不能宣告满足完整功能合并条件。** 本阶段只做安全修复，没有凭空建立第二套确认系统、扩张架构或把合成数据当真实业务验收。需在后续评审明确补齐权威目标确认与完整退出路径；本PR不合并、不部署。

### 验证与剩余边界

- 修复过程中定向证据/情景/ExitPlan/多标/MCP/原分析/Pattern回归反复执行；最终本地结果、完整提交SHA及最终CI见PR工程验收清单和本轮最终交付。修复期间运行的全量日志仅作排错，不冒充代码冻结后验收。
- 原CI四项为Backend tests、Frontend checks、Offline M5、MCP PoC tests。Windows相关仅Ubuntu pwsh隔离AST/fakes；没有原生Windows runner/真实国金桌面验收，不能标Windows业务PASS。
- LI/PDD/美团真实业务验收均NOT_RUN。现有三份报告仍仅SYNTHETIC_FIXTURE；本阶段未调用生产professional模型、真实Provider、券商同步、交易prepare/confirm或订单API。
- decision_engine核心、生产同步实现、订单/Batch、ORM/schema、依赖和前端业务代码不变。报告与组合解释收缩须明确评审，CI不能证明投资判断质量或生产版本。
- 没有合并main、创建Tag、生产迁移或部署。任务分支保留等待用户决定下一步；不以绿色CI自动推定发布可用。
