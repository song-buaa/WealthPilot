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


### 本轮提交与本地检查记录

- `fa5f3bd4d008f5566fe1141ae96079217feaceb5`：目标确认来源降级、缺可信目标阻断L3及纪律冲突。
- `9b06097ac5f453e5e49aaf49759aa2f6bdb2a052`：最终Web/MCP/Explain过滤、快照/时效、不升级false、协议与回归。
- `d35e70830c51ed49a31d033c237ffb3167ef976e`：独立审查报告及能力完成状态更正。
- `c8771c5258dc11d721370053df15e1f469df5a21`：保留既有“仅作一般分析”降级提示以兼容原U1合同。此后仅文档修正；最终PR HEAD及exact-HEAD四项CI在PR验收清单记录。
- 定向121 passed（`/tmp/wp42-review-targeted-final.log`）；补充原U1与MCP边界32 passed（`/tmp/wp42-review-u1-final.log`）；独立SDK实际schema协议1 passed（`/tmp/wp42-review-protocol.log`）。
- Offline M5 18/18且公网0（`/tmp/wp42-review-m5-final.log`）；frontend lint/build、compileall及工程文档检查通过。既有大bundle提示保留。
- 修复期间全量1 failed/1757 passed/7 skipped，唯一失败是上述U1旧提示文案；已最小恢复并通过32项重测。该次混合工作树运行不作为最终候选PASS；冻结代码全量最终结果、CI链接及完整HEAD在PR #39最终工程验收清单中单列。

## PR #39 剩余四项收口（2026-10-22）

本节更新上一轮工程审查留下的 A–D 实际需求缺口；前文测试与“未完成”判断保留为历史记录，以本节最终矩阵为准。开发基线为 `05d6b1de078550f73080174cd8312c0e7991291c`，继续使用原分支和 PR，不改生产环境。

### 定向勘探与最小实施点

| 缺口 | 原 owner 与最小修改 | 依赖和风险 |
|---|---|---|
| A 可信阶段目标 | 原 StageTargets 合同 + 本地 File-as-Source-of-Truth 配置；新增 stage_targets 服务与本地管理 CLI，分析只读解析 | 配置路径只能由服务端设置；候选/确认/期限/组合隔离；正式纪律不变 |
| B 账户级退出 | exit_candidates 只决定账户顺序和阶段，全部金额继续委托原 AllocationService.simulate_exit_scenario；原 ExitPlan/报告展示 | 费用、流动性与划转仍为明确假设；真实单位/费用不足不输出数量 |
| C 专业解释 | 原 Expressing 模型一次调用增加引用型定性解释合同；原输出 Owner 在所有最终入口重新投影 | 不恢复任意模型交易文本；无合格证据降级；未新增投研或模型引擎 |
| D FCF/时钟 | 原 AV Fundamentals 客户端增加 CASH_FLOW 字段映射；原 Schema/证据 Owner 按字段重核 | 官方源不保证披露与估值观察时钟；未知不能用抓取日替代 |

### A：候选、受控激活与可信读取

[StageTargetStore](../backend/services/stage_targets.py)保存组合绑定的版本、前驱、内容摘要、候选来源、确认来源/时间、生效与失效日期及状态。写入使用私有权限、锁与原子替换；拒绝符号链接、损坏内容、相同版本篡改、错误摘要、过期及无确认的激活。该文件是独立的用户配置真相源，没有修改正式交易纪律或 SQLite schema。

[管理 CLI](../scripts/stage_targets.py)仅供本地受信操作者使用，不暴露 MCP/Web 写工具，不借用订单确认。服务端必须显式配置 `WEALTHPILOT_STAGE_TARGETS_PATH`，MCP 主进程到业务 Worker 的既有配置白名单透传该路径。请求里的 `confirmed_by_user`、摘要或自填确认来源均不能获得服务端资格；与当前权威版本内容不符也不能升级 L3。可信目标只是 L3 的必要条件，不能绕过行情/财报/全资产质量/正式规则；L4 仍关闭。

使用方法（示例占位值，不代表用户确认）：

```bash
python scripts/stage_targets.py --store /private/stage-targets.json --portfolio-id 1 candidate --content /private/proposal.json --source user-reviewed-prd-candidate
python scripts/stage_targets.py --store /private/stage-targets.json --portfolio-id 1 show
# 仅在取得用户对完整内容、组合、期限、版本及摘要的明确确认后：
python scripts/stage_targets.py --store /private/stage-targets.json --portfolio-id 1 activate --version VERSION --digest DIGEST --operator OPERATOR --confirmation-reference USER_CONFIRMATION_REFERENCE --confirm-stage-target I_CONFIRM_THIS_VERSION_AND_DIGEST
python scripts/stage_targets.py --store /private/stage-targets.json --portfolio-id 1 deactivate --version VERSION --reason USER_REQUEST
```

已根据私有 PRD 方向在用户私有目录准备候选记录，未纳入 Git、未配置到生产、未激活。组合身份、阶段点位及有效期仍须用户正式核对；本次不能代替确认。候选可参与标明假设的研究方案，不能成为权威目标。

### B：无需手填情景腿的分批方案

[账户候选](../backend/services/exit_candidates.py)根据原 issuer 全成员、目标阶段和资金政策自动构造方案：比较不同账户优先、境外转固收与境内偿还人民币信用贷；逐阶段由原计算 Owner 复算分母、负债与净资产。对逐步退出但未指定中间阶段的目标，采用明确标注的三次默认复核；不是新用户目标。集中度方案额外比较“其他逐步退出目标先完成”造成的被动集中，前提假设明确列出，互斥方案不相加。

唯一纳入净资产的人民币信用贷可自动选择；房贷不能冒充信用贷，多个信用贷或未找到时保留方向方案并要求选择。账户资金政策可使用准确 account_ref 或既有 broker 别名，包括 snowball 存储名到 IBKR。缺政策、划转/贷款约束失败及目标未达明确保留 blockers。

金额是静态情景结果，费用/税/换汇成本及最低现金按零时明确标为未核验示意。港股/美股报价、原币、当前 FX、证券/账户绑定的交易单位、费用证据及累计持仓约束齐备后，才可由确定性程序生成数量；否则价格触发/数量为空。真实 Provider 未提供单位和费用证据时不声称已可精确执行。全部候选 `execution_authorized=false`，不创建或准备订单。

### C：专业解释与所有入口的最终门槛

原 PEER 的原模型调用继续解释经营变化、投资逻辑、竞争机制、风险情景、减仓理由及复核条件。新增 `professional_explanation` 为逐段 `basis=inference`、主题、正文与 evidence_refs；事实和数值由原确定性报告展示。有效研究、事件、技术和单独通过质量检查的财报字段可以支撑推论。引用只解析 Owner 返回的当前证据；排除 Seed、Mock、过期、冲突、错误标的和伪造记录。相同新闻同时投影为 research/event 的一致记录兼容，内容不同仍拒绝。

最终过滤保留合格的定性经营分析，剔除模型任意正文、价格/股数/量化交易指令及旧 Explain 残留字段。Web 单标/新建仓、多标、组合、MCP adapter 与 Explain 使用同一投影；最终时效变化会再次降级。无可引用证据时不补写经营结论。Explain 每次读取重新核验服务端保存的同一资产/目标/证据上下文；过期或缺少重核上下文时，返回明确历史状态并撤去旧价格、数量、有效资格及旧专业正文，不重拉数据或调用模型。原自由多标比较不重新开放，仍由逐标受控分析和账户方案比较提供研究依据。

Web 显式固定快照请求通过[快照适配](../backend/services/snapshot_chat.py)复用现有 U1 frozen_input、MCP 只读数据库边界及原 PEER，在一次性隔离子进程中读取同一冻结库；避免共享 Web 进程改绑数据库。返回前复核资产/贷款、目标、原始证据及单位/费用时效，只向 SSE/Explain 发送最终投影。不启动后台同步或订单生命周期；原无快照请求继续原入口。会话历史与最终安全消息复用原 conversation 服务，不另建记忆系统。

### D：真实字段能力与时效语义

原 Alpha Vantage 客户端已支持 `operatingCashflow`、`capitalExpenditures`；严格使用四个连续季度、同币种和可靠支出口径计算 `FCF = OCF - CapEx`，不以绝对值猜测负数编码。数据缺失、季度不齐、币种/期间冲突或口径不明均缺失降级；补齐 FCF 与利润冲突检查。年度/季度原报告、财政期间、正式发布日期、抓取时间和估值观察时间分开保存。

季度经营字段按期间使用 150 日窗口，年度按 450 日；实时估值按 1 日窗口；另外验证披露与抓取时序，不能机械共用 TTL。字段级质量相互独立：合格经营数据可支撑定性解释，但估值缺钟不会因此合格，L3 同时要求经营与估值的可靠证据。FCF 缺失明确标注，不用夹具填真实值。

官方能力依据：[AV CASH_FLOW](https://www.alphavantage.co/documentation/#cash-flow)、[GAAP 字段文档](https://documentation.alphavantage.co/FundamentalDataDocs/gaap_documentation.html)。字段存在不等于账户当前可获取：接口不保证逐报告正式发布日期、估值观察时钟及所有 CapEx 符号口径。原 AV 港股限制、访问额度、最新报告/日周线实际可用性仍须运行环境验收。本轮无真实 Provider 请求或付费模型调用。

### 更新后的完成矩阵

| 需求 | 本轮可交付实现 | 待授权/真实来源验收 |
|---|---|---|
| P0-A / D | FCF 正式字段合同、缺失降级、期间/披露/估值/抓取独立时钟、字段质量与冲突 | 三标真实最新披露、AV字段/额度、港股来源 |
| P0-B | 既有日周线/指标及质量门槛保持，数量另需真实单位/费用 | 原 Provider 实际日周数据和币种 |
| P0-C / A | 服务端独立目标配置、版本候选/确认/有效期/停用、请求伪造隔离、MCP配置透传 | 用户确认实际候选；尚未激活正式目标 |
| P0-D | L1–L4 与 false veto 保持，多入口最终重核；可信目标不能自证行情或风控 | 真实资产质量不足时保持降级 |
| P1-A / B | 三标无完整 scenario_legs 自动形成阶段/账户/资金去向方案、被动集中比较、累计持仓约束 | 真实交易单位、费用、划转及贷款余额；不是交易准备 |
| P1-B | 原计算 Owner 保持资产守恒、同快照、资金去向与还贷分母 | 实际账户划转/FX/税费不是零假设 |
| P1-C / C | 原模型的引用型专业解释恢复，事实/推论分开，Web/MCP/Explain/多标统一最终过滤 | 三标真实证据上的专业结论质量验收 |

核心四项实现闭环不等于真实业务验收。LI、PDD、美团的真实生产分析均 **NOT_RUN**；历史三份报告仍仅 SYNTHETIC_FIXTURE，不替代真实验收。正式目标待用户确认，真实单位/费用和来源时钟不足时继续关闭相应精度。没有生产配置/资产写入、迁移、同步、交易准备或订单操作；4.1 生产状态不变。

最终提交、冻结后测试、exact-HEAD 四项 CI、独立审查结果以 PR #39 工程验收清单及最终交付列出的完整 SHA 为准；不把开发期间测试或先前 HEAD 的绿色继承到新 HEAD。满足工程检查后停在待合并评审，不自动合并/Tag/发布。

独立审查附加修复：少见 Unicode 数词（廿、貳、罗马数字等）及全仓语义不能进入定性模型段落；保留日常定性中文。组合自动还贷方案生成后重核 net_worth 负债资格，不能仅沿用投资资产 B。固定快照追问复用现有服务端会话历史，仅存最终过滤后的回答。

### 本轮提交与冻结后验证

| SHA | 职责 |
|---|---|
| `68a51d31c78d001098a2c1389d834ec26e6e911e` | A：独立服务端目标候选/确认、原MCP配置路径及协议边界 |
| `827838b8adda870665dc3ba22cd9526026505483` | D：OCF/CapEx/FCF正式字段、财报和估值独立时钟 |
| `b8b1d850f6e300e3f6d9114ffcda1578d266f7df` | B：自动账户级阶段退出、原币/单位/费用与累计持仓 |
| `5f04c3d6488c02ac00b4bba9f1791f9a4749c2a7` | C：原模型引用型专业解释及多入口最终过滤 |
| `7efbe7dd73f36258329bd804bbdea0e817ac37e0` | Web同快照隔离和原会话兼容、Explain读时重核 |
| `87699d15af6b267e02932141afbe8596c5bcd426` | Explain排除旧研究的派生信号；最终业务冻结 |

交叉独立审查（AI工程review，不是人工approval）覆盖目标授权、账户/资金守恒、FCF口径、专业解释及Web/MCP/Explain。发现并修复Unicode具体股数、Explain过期/原研究与派生信号、组合自动还贷scope、快照追问历史。最终审查无剩余阻断发现。最新专项：A/B/C/D与MCP124 passed；Explain15、快照14及证据28合计57 passed；独立SDK19 passed；Offline M5 18/18、公网尝试0；前端lint/build通过。完整冻结后pytest结果、最终exact-HEAD CI链接和工程合并判断在PR #39最终清单记载。

本轮任何测试修正均只针对隔离夹具：Explain新增测试首轮全局mock在模块首次导入时污染后续测试引用，已收窄；首次snapshot mock不接受新增可选时钟参数已兼容。实现期间混合工作树的1871 passed、后续中止运行均只作排错，不作为最终冻结候选证明。`decision_engine/`、资产/订单ORM、真实同步/订单/Batch、依赖及前端业务代码没有变更。仅保留用户既有未跟踪归档文件，未读取或纳入提交。

## 首轮真实投研验收修复（2026-10-23）

### 范围、基线与来源

用户报告生产已部署 `b6b5a17bb589b8e82d1d655880c45f0675cef984`，首轮 LI/PDD/美团真实验收未通过。重新 fetch 确认该 SHA 为本轮 origin/main，创建干净 worktree 与 `codex/v42-real-research-fixes`。原工作区及用户未跟踪文件（包括 `归档.zip`）不参与修改。本轮风险 L2：跨模块事实/身份/输出合同修复；仅 PR，不合并/部署，不读写生产库/缓存/配置，不同步券商、不激活目标、不调用交易准备或订单，不重复生产付费模型。

依据为用户桌面审阅包，脱敏冻结投影及源成员 SHA256 见[夹具说明](../tests/fixtures/v42_real_research/README.md)。未提交归档、私人模型上下文或资产/账户记录。公开 IR 页面用于核查来源口径，不新增运行时 Provider 或自动写入正式资产。

### 最小修改点与修复对照

| 问题/Owner | 复现及变化 | 仍有的业务限制 |
|---|---|---|
| A / 原 AV adapter、cache、Schema | 红测重放真实 Premium，旧代码仅调用 OVERVIEW，直接 Seed 返回。现三个接口各自请求/分类；只有成功端点进独立新命名空间缓存，旧 aggregate/Seed 不遮住恢复；Demo gate 先于缓存。Premium、限流、网络、Provider notice/error、缺失分别标识。缓存命中保留原抓取时钟 | OVERVIEW Premium 权限问题未被代码解除；估值仍未知，不可据此授予 L3 |
| A / 原 statement_metrics 与证据 Owner | 收入及现金流独立；每字段保留源函数、原季度观察、币种、期间、抓取/披露/估值时钟。OCF 不因 CapEx 缺失消失；四期零 CapEx 标注 ZERO_CAPEX_UNVERIFIED，FCF 缺失 | AV 本轮真实 payload 未给正式披露时间；抓取不能补披露。未建设新的财务 Provider |
| A / 原 Research Facts → evidence | 原 raw_facts 的可选 financial_observations 透传；同季度/币种/字段与 AV 原观察比较、保留双方、冲突阻断。只消费显式结构化事实，不使用 narrative KPI、请求确认或模糊文本抽数 | 文本研究没有结构化观察时不能假装已自动完成数值冲突核对；需原研究链提供可核验的事实字段 |
| B / 原 symbol → Executing → Registry | 红测重放已有 canonical `3690:HK` 与 `3690.HGT`/CNY 持仓，旧入口行情调用为空；现在市场投影优先已存 canonical。渠道、账户数量、成本、issuer 聚合不变 | 原 Tiger 日线缺两交易日，日周线仍 insufficient；没有插值或忽略缺口 |
| C / 原 Expressing → 原模型/解析 → output_policy | 增加技术定性主题及明确候选 JSON 合同；原旧格式解析失败会丢扩展字段，现仅从同次原始返回恢复解释候选，再走原严格过滤。数量、价位、无效引用仍全部拒绝 | 审阅包只保存最终安全投影，原三次生成候选未保存；无法追认当时究竟未生成还是被过滤，不将离线受控响应当真实模型质量验收 |
| C / 原 policy 与 Reviewer | 阶段/候选数/有效证据数/接受数/分类拒绝原因，不记录原文或资产。初次生成与最终重核分开。新合同缺解释明确失败，不由旧 risk/reasoning 清空误判，也不以评分模型覆盖失败 | 新诊断需经另行授权部署后，才能定位下一轮真实生成质量；不自动重试付费模型 |

### 官方口径调查

[LI 官方二季度披露](https://ir.lixiang.com/zh-hant/news-releases/news-release-details/li-auto-inc-announces-unaudited-second-quarter-2026-financial)发布日期 2026-08-26，原合并报表单位为千元人民币。总收入 25,666,890 千元，即 25,666,890,000 CNY；冻结 AV 同季度 totalRevenue 为 25,594,433,485 CNY，差 72,456,515 CNY，不能解释为表格舍入或汽车销售与总收入的混用。具体供应商换算/重述原因 UNKNOWN，保留冲突，不改写任一数值。

同页合并净亏损 -1,705,257 千元，归普通股股东 -1,704,181 千元；AV 利润表 netIncomeFromContinuingOperations 与前者一致，现金流 netIncome 与后者一致，利润表 netIncome 则不同。不能把这些标签无差别当成同一利润口径。冲突比较不强行归一 netIncome。

[PDD 官方二季度披露](https://investor.pddholdings.com/news-releases/news-release-details/pdd-holdings-announces-second-quarter-2026-unaudited-financial/)确认季度收入/利润及 OCF，不足以验证四期零 CapEx；不能把投资现金流当 CapEx。本轮保留 OCF，零 CapEx 与可信 FCF 继续阻断。美团官方 PDF 取得限制和 Tiger 缺日均未以新源或虚构数据补齐。

### 验证与下一轮业务验收

- 确定性入口修复前：3 个红测（LI/PDD Premium 短路、HGT 丢 canonical）；修复后通过。
- 新定向回归覆盖真实财务/行情冻结记录、端点分类/缓存恢复/独立时钟、零 CapEx、LI 冲突、原 PEER 同次模型调用与旧解析恢复、经营/技术推论、无证据/数字/引用拒绝、Reviewer 新旧合同隔离。
- 原全量 pytest、M5、MCP/HTTP、Web/Explain/多标安全门及前端检查结果随最终工程记录补齐。真实模型响应在隔离测试中受控，未对生产重复付费调用。
- 部署须另行授权。之后以同一可信固定视图复验 LI/PDD/3690，逐次记录新阶段/过滤诊断、引用、来源时钟与最终 Web/MCP/Explain 一致性；核验经营解释价值及无基本面时技术定性价值。不要提前生成新生产快照、激活目标或调用交易接口。
- L2 仍需固定快照及资产质量，L3 仍需可信目标/行情/基本面/研究/正式风控全套资格；L4/订单边界不变。真实验收仍为未通过，待修复合并、授权部署和后续业务复验。

本轮独立 AI 工程审查（独立 CLI review 会话，非人工 approval）发现并修复：未核验研究卡不能仅凭 IR URL 参与权威数值冲突判断。另补 MCP 组合最终适配的单字段证据传递与最终诊断重核，合法部分财报解释不再在最后入口丢失，证据到期仍删除。

首次全量发现原国金 CLI 合成测试的固定 2026-10-22 时钟已超真实子进程二十四小时时效；相同环境在 clean main `b6b5a17` 单测复现 NATIVE_BATCH_STALE。仅该跨进程测试改用共同的新鲜合成时钟，其他固定工作日/窗口用例及生产时效代码不变。新增 HGT 测试显式固定 Demo 模式，避免其他测试重载模块后误选真实行情路径。

第二轮独立审查的三项 P2 已收口：完整现金流按自己的季度窗口计算并单列跨端点期间/币种差异（不混合授予资格）；解释合同失败仍服从原 retry_count 上限；只有实际 AV 请求执行限频等待，全部端点缓存命中不等待。新增四项回归及原字段时钟共 65 passed，解释与最终入口复核继续运行。最终 SHA、四项 CI 及完整结果在本修复 PR 的验收清单记录，避免把中途候选冒充最终提交。

最终兼容性收口：canonical 市场投影复用原分类 Owner，基金/现金/非支持市场继续 fail closed，ETF 与原有显式市场覆盖保留；财务冲突仅记录同季度原始直接字段的数值差异，派生项单列 UPSTREAM_FINANCIAL_SOURCE_CONFLICT，未受影响字段仍可解释，L3 冲突阻断不变。Reviewer 保留 hard/soft 分层语义。原季度观察只供确定性冲突核对，不经 field_quality metadata 回流模型提示。末端/时钟/身份/专业解释/Web/MCP/Explain/快照专项共 206 passed；前一候选本地全量 1948 passed、7 skipped，最终提交全部四项远端 Gate（含全量 pytest）以本修复 PR 的完整 SHA 清单为准。
