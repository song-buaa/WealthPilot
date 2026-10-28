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

最终兼容性收口：canonical 市场投影复用原分类 Owner，基金/现金/非支持市场继续 fail closed，ETF 与原有显式市场覆盖保留；财务冲突仅记录同季度原始直接字段的数值差异，派生项单列 UPSTREAM_FINANCIAL_SOURCE_CONFLICT，未受影响字段仍可解释，L3 冲突阻断不变。Reviewer 保留 hard/soft 分层语义。原季度观察只供确定性冲突核对，不经 field_quality metadata 回流模型提示。末端/时钟/身份/专业解释/Web/MCP/Explain/快照专项共 214 passed；前一候选本地全量 1948 passed、7 skipped，最终提交全部四项远端 Gate（含全量 pytest）以本修复 PR 的完整 SHA 清单为准。

最终市场兼容补充：核验原 quote 币种与 technical 日历仅完整支持 US/HK；SH/SZ canonical 保留在持仓/issuer 中，行情投影明确降级，不以裸 ticker 或 canonical 扩张未支持市场；`CN` 模糊基金码不猜交易所。已知 ETF 类型优先于泛“基金”标签。AV 服务只映射 US 报表，HK/SH/SZ/LSE 不误用裸代码发出 US AV 请求。无报价的快照 currency 保持未知，不再默认 USD；合格技术证据继续保留自己的市场与币种。

## PR #40 代码集成（2026-10-23）

用户授权后已重新 fetch，核对候选 `5437da0ad962ac7dc49c68b589b39a31ee5bc3e4`、main 祖先关系及候选四项 CI，以 `git merge --ff-only` 集成并普通 push main。PR #40 为 MERGED，local main/origin/main/远端 main 同 SHA；[合并后 main CI](https://github.com/song-buaa/WealthPilot/actions/runs/39911140134) 四项全部通过。无生产切换、Tag、配置/数据/目标/券商操作，真实三标验收仍待复验。后续独立模型升级工程候选见[记录](GPT6_MODEL_UPGRADE.md)。


## 最终真实验收阻断定点收口（2026-10-23，独立 PR 候选）

用户报告生产38c3faf部署PASS，但完整三标PEER仍BLOCKED、模型请求0次；此为本轮起点，不把新分支当成生产已更新。仅处理来源合格渠道身份的市场投影与一次性验收预算；固定视图只勘探，不写生产、不改TTL、不新建数据平台。

### A：市场读取身份，原事实不变

`position_market_symbol()` 复用原 `position_security_symbol(symbol, broker, security_type)`。参数security_type来自原持仓字段broker_security_type，不是vehicle_type。仅已有guojin + QNX_NATIVE_POSITION + 合法数字HGT/SGT渠道才投影为数字HK身份；支持既有`:HK`及相同受证实来源的无市场后缀形式，不能仅凭名称、CNY或渠道后缀猜测。基金/现金/非US-HK等原拒绝逻辑保留。

经用户授权由生产部署chat执行一次指定记录只读查询，当前渠道symbol实际含`:HK`，此前摘要的裸symbol不作为原始字段真值；完整来源合同已核对。仅查询身份字段，未查询金额、数量、成本或账户号。测试使用相同字段结构的合成记录，生产证据只存私人验收目录。现有证券/issuer投影、聚合分组及原存储symbol/ticker/交易身份不改；原Registry继续做日/周线完整性校验，不补造缺失交易日。

### B：专用真实PEER验收入口

新增[一次性入口](../scripts/v42_peer_acceptance.py)和[发送前预算](../app/llm_acceptance.py)，复用原`analyze`→U1 frozen_input→原PEER，不从MCP/Web触发第二轮。原`llm_config`只有验收guard激活时才显式固定service_tier=default；正常应用请求无变化。PR42事后`llm_observation`保留，与发送前预算分别记录。

- 必须在全新专用进程运行，显式`--allow-paid-acceptance`；SDK固定2.29.0、正常Sol/Luna profile、原operator私有0600配置。不会创建或拷贝Key，不加载其他项目OPENAI_API_KEY，不启动backend lifespan。
- 同一私有JSON账本及其`.lock`覆盖三标、业务重试、SDK重试、Chat/Responses、同步/异步发送。flock+线程锁、私有属主/模式/硬链接校验、创建串行化、文件与目录fsync。读取同一账本续用，不重置额度；每标只可claim一次，重启不重放已claim的整轮分析。
- 每次实际HTTP发送前先持久化保守预留，最多24次、总预留不超过USD3；失败、超时、缺Usage不退款。输出上限含推理Token，输入用含schema的UTF-8字节量+固定/消息framing裕量作保守上界；超过128000输入上界拒绝，排除长上下文价格。只允许已知Sol/Luna、明确default tier、纯文本、n=1、非流式、store=false；工具/图像/continuation/background/未知模式与无输出上限一律拒绝。
- 价格版本`acceptance-standard-text-max-cache-2026-10-23-v1`，依据[官方价格](https://developers.openai.com/api/docs/pricing?latest-pricing-expanded=true)，输入采用已知最高12h写缓存单价而非缓存折扣：Sol $5/M、Luna $0.25/M；输出$10/M、$0.50/M。这是保守预留，不是实际账单；不使用事后Usage释放预算，actual_bill始终UNKNOWN。未来执行前须重核该价格合同；未知/已变化合同停止，不沿用旧价格冒充保证。
- HTTPX每次传输（含SDK重试及重定向）独立拦截，要求当前服务端analysis_run_id已claim且未关闭。原生socket/UDP与子进程审计拒绝未关联发送或新worker；关闭后guard保留至CLI退出，迟到线程不能恢复无预算传输。不是防恶意修改Python源码的沙箱，也不约束其他生产进程；本次验收必须只用该专用入口及同一账本。
- 另有极窄、非模型AV只读GET白名单（财报/既有日周线/quote函数），不会消费模型次数，不是通用代理；URL/方法/函数/重定向均校验，不记录Key/URL。其他实时行情通道、Embedding/专用付费搜索及未知网络路径拒绝，沿原optional能力降级；不为了完成验收开放未知收费源或券商通道。可用的原缓存仍遵循其原有时效/质量规则。**该限制可能使某标技术证据不足，应如实BLOCKED/降级，不能把它包装成数据齐全。**
- Worker/父进程私有日志策略不改；专用入口丢弃stdout/stderr和原日志，仅输出安全摘要。账本无Prompt/回答/账户/持仓；私有0600结果文件只保存最终受过滤结论/专业解释及安全观测，不保存完整资产上下文、原始模型回答或Provider响应。COMPLETED仅代表入口完成，不代表投研业务通过；UNKNOWN不能冒充$0。

未来单独授权后的命令模板（本轮不运行）：

```bash
<完整ARM Python> scripts/v42_peer_acceptance.py \
  --database <原共享SQLite> --portfolio-id <原portfolio> \
  --snapshot-id <已核验新鲜固定视图> --contract <原共享库契约> \
  --operator-env <原私有operator JSON> --ledger <本轮唯一私有账本路径> \
  --output <全新私有验收结果路径> --allow-paid-acceptance
```

只在完整依赖/SDK/价格/视图及用户授权均成立后执行；不得改用原MCP/Web另开付费请求来绕过账本。读同一次结果做Web/MCP/Explain合同对照，不重复模型生成。真实生产、三标业务质量、ARM入口运行均未在本轮开发执行。

### C：新鲜固定视图的最小方案（未执行）

| 路径 | 能否保证不调用券商 | 行为与条件 |
|---|---|---|
| `frozen_input`/`load_view` | 能，但不能生成新视图 | 原30分钟VIEW_TTL、私有文件属主/权限/哈希均保留。过期即拒绝，不能更新captured_at或复制旧payload重启时钟 |
| 原公开`prepare_asset_analysis` | 不能预先保证 | 调原AssetAnalysisPreparation.request/run，run对每个enabled broker调用reserve(freshness_seconds=300)。仅最近成功、完整、publication matched、无未收敛成交等满足原复用条件时才不发远端读取；条件变化会创建同步任务。禁止在“不得同步”权限下盲调 |
| 一次capture-only运维封装 | 底层具备，现有公开入口无该选项 | 若原源事实仍合格，另行授权后可一次性复用create_frozen_copy→原只读capture→原WealthSnapshot载荷/持久化合同；保持原owner/fingerprint/Portfolio/租约校验，不用空owners绕过，不复制旧payload。新增的是实际捕获时间，positions/cash/price/FX/手工事实时间原样保留。本PR不新增此生产写入口/功能 |

先只读检查同一Portfolio所有分母相关来源：last-good publication与持仓匹配、账户覆盖/未收敛成交、原positions_as_of/cash_as_of/account_synced_at、price_as_of/交易日、原FX及手工资产负债复核。上下文过期不证明源事实合格或不合格；本轮只查过指定持仓身份，未获权核查完整源事实，因此当前可否capture-only为UNVERIFIED。

需要新增视图时，即使不读券商也会创建私有SQLite一致性副本/缓存、WealthSnapshot与请求状态，并可能按原TTL/容量规则清理过期缓存副本（不删除历史快照记录或用户归档），属于单独生产写授权；本次未创建。严格限定只能使用现有公开prepare且禁止任何券商读取，则不能保证完成，继续BLOCKED。不得改变300秒精确源门槛、30分钟视图TTL、原近收盘估值/FX/手工事实时效，不把“新视图”当作L3资格。

若事实已过期且必须刷新，单独授权范围至少列明：

| 来源账户范围（账户号仅原私有配置解析） | 原读取/来源 | 本地副作用及授权 |
|---|---|---|
| 老虎投资账户：LI/PDD及同账户完整分母 | 原TigerSyncService持仓、原币现金/账户数据只读接口 | 原SnapshotRun/claim与Position/现金/FX/来源质量更新；需明确只读券商查询及本地同步写入 |
| IBKR投资账户：LI及同账户分母（原snowball映射，须核验当前来源） | 原ReadonlyIBKRClient get_positions/get_account_info，Gateway只读三重护栏 | 同上；不能把Snowball标签猜成真实账户或扩展订单/历史交易查询权限 |
| 国金投资账户：美团/LI及同账户分母 | 原已发布、验证的本地原生三文件导出批次，现有GuojinSyncService | 本地批次来源/时间验证和发布；若批次旧，Windows终端新导出/调度行为另行授权，不能重写原文件时间或替换批次 |
| 富途等其余已启用、影响分母的来源（如有） | 仅按现有enabled/owner配置确认，不能忽略物质敞口 | 范围未核实不冒充全账户齐全；不得趁机扩大券商访问 |

调用前列出实际enabled/owner映射（私有、不暴露账号），避开原调度窗口和正在执行任务。刷新源与新视图写入分别明确授权；不触发订单准备、交易、目标激活或纪律修改。行情/财报只读Provider及24次/USD3模型预算也须分别授权，不以开发PR权限代替。

### 工程与业务验收边界

[专项](../tests/test_v42_acceptance_budget.py)覆盖来源正反例、原US/HK/ETF/基金投影、原PEER路由/最终门槛、SDK重试/超时、并发次数/金额、Chat/Responses/异步、另类客户端/重定向/子进程、重启不重置和旧视图0发送。原[PR42观测](../tests/test_llm_observation.py)、[真实来源冻结回归](../tests/test_v42_real_research.py)、U1及权限过滤继续复用。完整测试、独立审查及exact-HEAD四项CI由本PR交付记录绑定；不把合成回归当三标真实验收。

最终是否可实际运行三标仍取决于：本PR合并/授权部署、新鲜合法视图及来源质量确认、完整ARM专用入口验证、已复核价格/授权预算。开发完成不自动解除真实业务BLOCKED。


## L1证据链P0/P1修复（2026-10-27，开发候选）

基线bb297bd。用户提供的10月25日LI诊断为Sol成功但42记录全部unknown、0引用/0候选；本轮不追加真实调用，不把合成修复对照当真实研究质量PASS。

### 范围、Owner与实现

| 切片 | 原Owner与最小修改 | 安全边界 |
|---|---|---|
| P0-1 配置/诊断 | 原AV adapter的`get_next_av_key`兼容名改为每次解析：ALPHA_VANTAGE_API_KEY → ALPHAVANTAGE_API_KEY → AV_API_KEY → AV_API_KEY_1..4首个非空；三个适配器共用，不隐式跨槽轮换。Worker文件一旦给出AV配置，清理父进程同组遗留值后加载 | 不读/复制4.3 Key；不增加配置系统或生产改动。`entitlement_identity=operator-slot:<变量名>`仅是逻辑配置身份，`entitlement_verified=false`表示该请求路由尚未与已验证订阅身份关联，绝不否定用户已验证的Premium账户 |
| P0-1 端点与缓存 | Fundamentals/Research成功缓存绑定进程内凭据token，保留原fetched_at；不输出token或Key。KlineResult携带安全attempt诊断：function、full/compact、配置槽、状态、条数、首末日、抓取/缓存、fallback原因；full Premium提示后compact仍用同一凭据 | 不把Key、认证URL、异常原文写入日志。缓存TTL保持原值；换配置不复用另一凭据的成功结果。正常full优先；compact只作为显式降级，不冒充full |
| P0-2 字段 | 原evidence独立判断财报币种与估值币种；估值冲突只排除相关估值，保留可信经营字段。正利润/负FCF保留两者作为风险事实，L3原阻断仍保留 | 真正官方来源冲突继续阻断对应字段及派生指标，不覆盖原值，不借机放松L3 |
| P0-2 公告关联 | 原Research事实投影携带结构化official_disclosure；原evidence对每个季度的字段、主体、期间、口径、币种、单位与数值逐项核对，仅匹配齐全时向分析副本加入publication_links及真实披露时间 | 原AV和研究记录不写回。财期不冒充公告日，fetched_at不冒充公告日。一个季度不能给四季度TTM补钟；净利润口径未独立核实时不自动关联 |
| P1-1 周期 | 原OHLCV/日历/指标重算生成day/week的period_quality与具体拒绝码。原technical_verified仍表示两个周期完整合格；L1可通过verified_period_records使用单独合格日线 | 周线不足不得声称周线或多周期共振；缺日/根数不足/过期/指标不符分别提示。原L3完整技术条件不变，Web/Explain返回后复核有效周期 |
| P1-2 日期/候选 | 原News抓取保存真实fetched_at，Facts保留ingested_at；evidence_records不再以入库时间充抓取时间。源发布日期仍只取明确原字段。原repository有界检查最多100候选后优先保留合格/官方事实，再截取20 | 不改历史卡，不从URL/文件名猜日期，不把旧事件重新变新，不使用叙事KPI替代源事实；100以外候选仍可能未覆盖，明确保留此边界 |

只在原PEER/Skill/证据/输出owner内扩展。无新Provider、资产/订单Owner、数据库表或Schema；模型、TTL、固定视图、阶段目标、正式纪律、券商同步与订单逻辑均不修改。

### 官方披露关联合同

既有Research Facts可保留独立的官方来源记录和`raw_facts.financial_observations`，本轮增加只读透传`raw_facts.official_disclosure`。这是原事实层字段的最小扩展，不是从叙事自由抽数或用confirmed标志自证。仅接受已保存的公司官方HTTPS域、匹配原issuer的来源，真实published_at/fetched_at与非未来时钟、原record_id，以及逐项定位的财务观察。来源缺失则继续UNKNOWN。

- official_disclosure：`issuer_id`、`source_url`，须与原symbol/官方域/原URL一致。
- 每项financial_observation：`source_field`、精确`value`、`fiscal_period_end`、`period_basis=quarterly`、`currency`、`unit=currency_units`、明确`unit_scale`（1/1000/1000000）、`accounting_basis`、可复核的`source_locator`。
- 当前明确映射：totalRevenue/total_revenue、operatingCashflow/operating_cash_flow、capitalExpenditures/positive_capital_cash_outflow。不同净利润归属口径不推定相等。
- 对收入TTM须四个原季度分别匹配；同比还须前四个季度；FCF须每季度OCF和CapEx分别匹配。转换单位后严格核对数值，不以百分比容差掩盖LI收入差异。
- 只在副本field_provenance加入每季度`publication_links`，保留官方原URL、record_id、issuer、字段、单位/口径、公告和抓取时间。只取这些已经核对的真实公告时钟；不把最新季度日期无条件套用到全部季度。
- 已有LI官方收入冲突的冻结回归继续保留。缺结构化源事实或时钟的原卡不会被升级；本轮没有导入或确认任何生产官方财务数据。字段关联合同可用与生产事实已具备是两回事。

### 离线对照与质量评估

采用同一份四季度合成公开财务字段和固定2026-10-25时钟，分别在bb297bd与候选源码运行；不是10月25日原始AV响应重放（该原始文件尚未取得）。

| 场景 | 修复前→后 | 不变的拒绝/限制 |
|---|---|---|
| CNY经营字段本身合格 | 5→5字段、6→6引用键 | 不推定USD估值可用 |
| 无关估值币种冲突 | 0→5字段、0→6引用键 | 冲突估值继续拒绝；引用键数含原记录/字段键，不代表不同来源数 |
| 正利润/负FCF | 0→5字段、0→6引用键 | 原L3风险/证据条件不放松 |
| 100个完整交易日日线、周线不足 | 0→1个日线引用，周线继续无引用 | 不宣称周线/多周期共振；L3技术条件仍不满足 |
| 财务披露日期缺失 | 0→0字段/引用 | 绝不以当前抓取时间补钟 |

专项覆盖所有合法配置变量、动态配置/旧父环境、同Key full/compact、缓存原时钟、敏感响应不泄露、逐季度双来源、主体/口径/单位/币种/日期/数值反例、缺日/指标/Seed/过期、新闻日期、候选选择和原PEER→最终Web/MCP/Explain。原173项相关回归继续作为基线；最终测试与exact-HEAD CI以本PR/私人运行记录为准，不用历史数目代替本轮结果。

研究质量只准备原五维评估：经营变化驱动、盈利/现金转化、竞争反证、估值与投资逻辑、风险/退出/复核条件。同一次原PEER调用，确定性代码给事实数值和来源，Sol给定性因果与条件。没有合格事实不要求强写结论，本轮不大改Prompt或重写PEER。

### Premium已验证与生产路由待核对

用户已确认Premium订阅约USD49.99/月、75请求/分钟。已复用4.3现有S1-B3验收记录：PREMIUM_KEY_CONFIRMED=true，12/12真实响应有效，Daily Adjusted full成功取得多年数据；没有重新验证权益、购买或读取凭据内容。该记录的来源身份为原个人凭据文件，未输出Key片段/指纹。

**账户Premium已确认；4.2生产是否选中相同权限的配置槽、LI的TIME_SERIES_DAILY full/compact结果仍runtime-pending。** 不将Daily Adjusted full历史成功偷换为LI Daily full本次已经成功。

最小只读交接（不在本轮执行）：原operator配置中仅列合法变量名存在性及选中槽、进程加载顺序；由原Owner确认与4.3已验身份的对应关系，绝不交付原配置/Key；已有LI端点诊断的function/mode/status/条数/首末日/cache_hit/原fetched_at/fallback原因；已有官方字段及新闻原日期的脱敏投影。优先查历史文件，缺失记SOURCE_MISSING，不刷新Provider补造历史。

### 下一步LI单次真实质量复验（需单独授权）

合并并经授权部署后，先核对上述最小路由和已有事实，不以PDD/美团、固定视图或L2/L3未完成阻碍L1限定试用。随后只运行一次不带snapshot_id的原LI MCP研究；沿既有受控私有进程、原预算账本与人工授权的剩余额度，预算/计数不重置。读取已保存结果检验：引用是否指向合格字段/周期；若仅日线有效只评价技术限定研究；若经营事实有效，再评价经营/现金流/反证/估值条件的研究价值。分别记录候选/保留、资格、模型/耗时/Token/估算成本；无证据仍安全partial，不用模型补数。

本PR不授权合并、部署、生产配置/数据写入、阶段目标激活、Provider/模型请求、券商操作、新视图或交易。真实8/10质量仍待上述受控复验，工程全绿不代表业务已达到8分。


本轮CI环境定点兼容：首个候选0c2c4fa的PR Run 40436379570（Job 121965901536）已通过适配器、部署guards、Tunnel与双HTTP服务，但ubuntu-latest滚动分配到26.04，锁定Playwright报`does not support chromium on ubuntu26.04-x64`，浏览器测试尚未启动。仅将原MCP Job固定为本地锁定版本支持的ubuntu-24.04；不升级/删依赖、不跳浏览器测试、不修改任何Job时限或生产环境，四项Gate保留。最终exact-HEAD CI绑定该CI-only兼容提交，不以失败Run冒充通过。


### PR #44 合并前官方来源可信边界定点修复

发现并离线复现：Research 原 raw_facts 可含导入、自报或 AI 候选字段；repository 的 unverified_until_source_checks 先前会被 _official_observations 仅凭官方域、结构和数值匹配升级。官方域名、可填写的 source_locator、AV 数值一致和自报 published_at 并不能独立证明公告日。原 _build_facts_layer 保留 payload，LLM 叙事层与事实层分开，但这不构成原文核验。

最小修复只限制原关联 owner：普通卡仍透传候选及原始时钟以便追溯，不授予官方时钟权威。assess/link_disclosure_clocks 的可选 disclosure_verifier 是受信任服务端代码依赖，按 record_id 查询与卡片独立核验的原文结果，不从研究卡、请求 options 或质量标志加载。只有该独立结果的 symbol/URL/公告/抓取时间、issuer 与逐项观察全部等于候选，且提供原文 SHA256 与公告定位时，才继续执行既有严格域名、主体、季度、口径、币种、单位和数值关联。publication_links 保留原文哈希及公告定位。卡片自报核验标志/哈希/官方 URL 均不能代替这个独立 owner。

**当前生产调用方未接入独立官方原文核验 owner，默认不传 verifier，因此不会从研究卡自动补齐 AV 公告钟，继续 UNKNOWN。** 本轮不新增 Provider、核验文件库、配置、原文抓取或审批系统。正例只用与卡片分离的冻结源 owner 替身证明严格关联合同；不声称已经核验生产原文，也不把构造一个 verifier 的测试当真实来源认证。将来接入者须先独立核验原文的来源、公告日和可定位财务行，不能直接返回研究卡或 AI 抽取结果；本 PR 不实现或暗中启用该接入。

定点对抗覆盖：自报 URL/日期/匹配数值与各种 verified/quality/hash 标志不提升 AV；独立源缺失、公告日/行值不一致、无公告定位或无原文哈希拒绝；独立源全匹配仍按逐季度规则关联；原独立有效经营字段、日周证据和 L2–L4 拒绝不变。原 LI 真实收入冲突回归保留。最终代码/测试/CI 绑定后续 PR HEAD，不沿用 50be44c 的历史通过结果。


## LI 复验账本最小修复（2026-10-27，开发候选）

范围仅为原 app/llm_acceptance.py Budget 与 scripts/v42_peer_acceptance.py 专用验收入口。原 claim 仍保持每标的一次，不修改历史 analyses 中 LI 的 run_id、不替换或重新归属任何 attempts；PEER、模型/价格版本、HTTPX/AV 白名单、证据、资产与交易代码不变。

原 v1 账本照常读取，snapshot 不再无意义重写字节。只有显式 LI 复验登记才在原 flock 与同一 inode 下扩展为 ledger_schema=2，追加唯一 li_retest 记录（symbol=LI:US、独立 analysis_run_id、authorization_id、original_run_id、authorized_against_sha256）。operator 必须另获真实运行授权，明确提供 --allow-paid-acceptance 与 --allow-li-retest、两个不含私人信息的32位十六进制 ID，以及已核对原账本的 SHA256；这些是操作员登记凭据，不从自然语言、模型输出或 analyze options 自动生成授权。普通 claim、其他股票、历史 ID、无授权字段、摘要变化或重复登记拒绝。即使换授权 ID，也不允许第二次复验。

授权复验模式要求 existing_only：账本不存在不创建新预算。仅调用一次原 analyze_position、symbol=LI:US，不传 snapshot_id，不执行 run_campaign。原三标函数只保留其原兼容性，LI 复验分支绝不回到该循环。HTTPX 所有实际发送/重试继续在同一账本预留并 fsync；发送前同时验证持久登记与本进程合法登记，修改 process-local runs 不能创建新 run。24 次和 USD3 是新旧累计上限，绝不另设复验额度或退还预留。

迁移前在原锁内用 O_EXCL/0600 写入并 fsync `<ledger>.pre-li-retest`，包装原始字节和摘要，再 fsync 目录；该文件是 recovery_only 证据，不能被 Budget 当第二账本打开。随后原 inode 内提交扩展状态，保持旧进程的 flock 互斥关系，不能原子替换文件破坏旧等待者的锁。读取器严格验证 v1/v2 Schema、run 绑定、累计和、权限及限额；旧代码读到 v2 会拒绝。中断/损坏一律拒绝发送，不恢复、不覆盖、不重置原账本；残留恢复证据即使主文件未变，也要求人工调查，禁止自动重试迁移。部署后真实迁移/运行及任何恢复均须单独授权，本开发任务未触碰生产账本。

脱敏合成 v1 副本严格复现原7次、预留USD0.5564585：登记后原数据逐项相等，剩余17次/USD2.4435415仍属同一累计预算；MockTransport 验证第25次前拒绝、成本可先耗尽、SDK重试逐次预留；多进程竞争仅一人登记成功。覆盖旧记录、跨标的/缺授权/重复、重启、旧普通claim、迁移中断保留恢复证据和损坏拒绝。该副本不是从生产复制，也不表示已经执行 LI 真实复验。最终测试和 CI 以本 PR 完整 SHA 证据为准。

## 正常 MCP 单标错误诊断（2026-10-27，待合并候选）

范围仅为正常 `analyze_position`（同一适配器上的两项分析读取沿用），不传 snapshot_id，不改 PEER、模型/Provider、240秒分析/270秒 IPC 超时、专项验收账本、数据资格或交易权限。历史两次真实错误的执行位置仍 UNKNOWN；此修复提供下一次运行证据，不能倒推事故根因。

### 最小链路与字段

- 复用服务端匿名 `analysis_run_id`，在正常 MCP 接收时建立，经 ContextVar、内部 Worker 请求及原 `observed_result` 贯通。客户端仍不能提供/伪造此 ID。
- [BusinessClient/server](../pocs/chatgpt_skills/server.py) 记录接收、启动、发送、IPC、Pydantic 及最终返回；[Worker](../pocs/chatgpt_skills/worker.py) 记录已进入请求、analyze/PEER、结果序列化。阶段表示实际到达/进入，不声称阶段已成功完成。
- Worker 仅在内部请求 `diagnostic_events=true` 时发送闭合集合进度帧；原单个最终结果帧保持不变。父进程支持拆包/合包，所有进度共用原绝对 IPC 截止时间。没有自动重发。旧内部调用未启用该标志时仍只收到一个最终 JSON 行；MCP/HTTP/SSE 公共 schema 不变。
- [安全投影](../pocs/chatgpt_skills/diagnostics.py)仅接受枚举/数值/匿名 ID；[原只读事件入口](../pocs/poc02_wealth_mcp/server.py)输出结构化 stderr，另可用原 `--events` 机制写入私有 0600 JSONL。写入失败只报告固定代码，不影响分析结果；不输出 stdout、不建表/服务、不记录问题、模型文本、异常原文、HTTP body、账户、持仓或凭证。
- 字段：`protocol_is_error`、`business_status`（ok/partial/unavailable/UNKNOWN）、`failure_stage`、`safe_error_category`、`elapsed_ms`、`analysis_run_id`、`stage`；原安全 LLM 元数据确有记录时附 `observed_peer_stages` / `model_error_categories` 闭合枚举，无记录 UNKNOWN。只有实际 `poll()` 获得时才有 `worker_exit_code`。
- `model_call_count` 只来自同 ID 的原安全 LLM 元数据，scope 为 `unified_generation_adapter_only`，不是 HTTP 尝试总数。无观测为 UNKNOWN；Provider 请求数未建立计数证据，始终 UNKNOWN。已有 PR #42 的型号/Token/重试等元数据不变，不构建第二套费用或请求计数系统。
- 正常业务 unavailable 保持正常 MCP 返回；启动、IPC 超时/退出、无效 JSON/信封、串 run ID、序列化和 Pydantic 错误保持 MCP `is_error=true`。已观测的 analyze 异常可沿原合同降级为 unavailable。错误事件的 UNKNOWN 不解释为未调用模型。
- 如 Worker 硬退出/超时，依据同 ID 最后已收到阶段与 IPC 错误定位；未收到的后续阶段、Provider/模型实际执行均不得猜测。最终事件区分业务状态与协议返回，旧 `ok=true` 不再作为业务完成证据。

### 离线验证与权限

专项覆盖正常三种状态、启动拒绝/失败、IPC 超时/退出、无效 JSON/信封、Pydantic、序列化、模型已调用但业务未完成、匿名 ID 串联/防伪、进度不能延长期限、未知统计、私有输出抑制、原读取/Mock 人工确认与无自动重跑；见[正常 MCP 协议测试](../pocs/chatgpt_skills/test_v42_protocol.py)。真实 Worker 测试仅替换 analyze 为合成函数，禁网、不加载生产配置/数据库；原完整 PEER 兼容由全量/离线 M5 回归覆盖。CI 仍执行原四项 Gate，无配置/锁文件修改。

### 合并后部署与一次 LI 验证（未执行）

1. 本次只交付 PR，不自动合并/部署。获授权后 MCP 父进程与其 Worker 必须使用同一已验证 SHA；只切换这条 MCP 代码路径，依赖锁及完整业务 Python 沿用。无需数据库迁移、账本迁移、模型/Provider 配置修改或券商刷新。
2. 原 stderr 安全事件默认可用；若需私有 JSONL，另经部署授权配置代码外 `--events` 路径，文件 0600、目录 0700。不要开放原始业务日志。失败时恢复原 MCP 启动版本并核对健康，不恢复/改写数据库或账本。
3. 获得一次真实调用的单独授权后，通过正常 WealthPilot-Prod 仅调用一次：`analyze_position({"request":{"symbol":"LI:US","question":"请基于当前已有合格证据分析理想汽车的经营、技术趋势与风险；证据不足时明确降级。仅研究，不准备交易。"}})`。不传 snapshot_id/run ID，不使用专项 campaign，不自动重试；本开发任务没有执行此调用。
4. 记录客户端 `is_error` 与业务 status；从私有安全事件取得同一匿名 ID，核对 Worker/PEER/序列化/IPC/Pydantic/返回阶段及原 LLM 元数据。协议失败保留最后已证实阶段、类别、实际退出码；缺观测继续 UNKNOWN。成功也须检查专业解释/证据/降级，不能以事件成功代替研究质量验收。L2–L4/人工交易边界维持原合同。

## 正常 MCP 长研究异步取回（2026-10-27，待合并候选）

### 120 秒与既有证据

已读取 [OpenAI Secure MCP Tunnel 官方 Limits](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)：默认单次绝对期限为120秒，覆盖托管队列到最终响应交付，进度通知不续期。仓库原 [runner](../pocs/poc03_mac_mini/runner.py) 使用官方 tunnel-client HTTP 路径，原 [GitHub 安装器](../pocs/poc03_mac_mini/github_release.py)锁定 v0.0.16；本轮不升级 Tunnel 或修改其配置。已保存的「生产部署」记录显示 PR #46 仅切换 MCP，Tunnel/Gateway保持原状，最新两候选运行分别148.231/164.366秒，均经过Worker/PEER/IPC/Pydantic到mcp_return、partial、4次模型观测且无记录模型错误；第二请求在首个接收约120.6秒后出现。

上述证据与托管单请求期限**高度吻合**，240/270秒内部期限不能解决外层120秒。历史记录没有证明具体客户端请求ID、最终HTTP响应交付/连接器错误码，当前实际Tunnel二进制版本及全部运行配置未在MacBook直接复读，不将它们写成新生产实测；未知不阻挡最小异步适配。没有新真实分析、Provider探针、Tunnel/Gateway操作或配置修改。

### 最小实现及归属

- 原 [server](../pocs/chatgpt_skills/server.py) 增加 `start_position_analysis(request_id, request)` / `get_position_analysis_result(job_id)`，复用原 AnalysisRequest、analysis_call、BusinessClient、Worker、只读SQLite合同及原PEER。没有新增研究引擎/队列/服务/数据库；未修改Worker、PEER/Prompt、模型/Provider、资格、TTL、交易、24/$3账本或PR45历史。
- [进程内登记](../pocs/chatgpt_skills/position_jobs.py)使用线程、同一分析准入锁及不可预测job_id。start只登记并启动线程，不等待模型；get只读取私有内存，没有Worker/模型/Provider调用。无队列，同一时刻最多一项分析，原同步专业读取同时占用相同准入锁；忙时快速拒绝，避免锁等待再跨120秒。
- 后台任务单独持有原BusinessClient；HTTP请求/连接结束时原MCP lifespan清理只关闭原共享客户端，不能关闭该任务的Worker。任务自身完成/失败后才按原路径关闭Worker。不是依赖请求内async task、SSE心跳或延长超时。
- 原匿名analysis_run_id在登记时创建，沿原PR46安全事件/LLM observation贯通并随job返回；不新增原文/资产日志。生产原 `--events` 持久化要求保留。后台任务的原 `mcp_return` 仅表示分析适配器返回，不能证明job结果已被客户端取到；是否可取由get的任务状态及实际返回核对。
- completed返回嵌套原完整AnalysisResult，包括conclusion、professional_result、evidence、data_quality、scope、limitations及llm_observation；不重写结论或重新授予资格。partial和业务unavailable保留原状态。任务completed仅表示原调用结束，不等于业务成功。IPC/校验/任务异常返回unavailable、结果为空、业务状态UNKNOWN，不伪造报告。

### 幂等、重启与有界保留

单靠纯内存无法记住任意旧ID在上一次进程中是否已付费，因此request_id采用工具说明公布的**进程scope + 稳定随机token**；ChatGPT从当前tools/list获取scope，用户无需手填。同ID同参数始终返回原job；同ID不同参数拒绝。旧scope启动明确unavailable，不能在重启后把旧请求当新分析；查询未知job也只返回unavailable/UNKNOWN，不恢复或重跑。部署/重启后须刷新ChatGPT工具定义；失败或未知任务不得自动换scope/ID重试，新的研究须为用户明确的新请求。

默认64条登记收据、8份运行中或未到期结果、每份完整JSON上限2MiB，完成后保留两小时。get不延长保留期；过期查询不再返回报告，下次start/get时惰性清理完整结果，保留不含报告/问题的指纹收据防重跑。容量满拒绝新任务，不提前逐出未到期报告，也不删除收据来换取再次付费。超大/不可序列化结果明确不可用，不截断成假成功。完整结果仅在本进程私有内存，进程退出后无法恢复；新进程不读取旧结果或自动执行旧任务。

防重复保证针对同一稳定ID的一次PEER执行；不改变原PEER/SDK内部合法重试，也不把模型供应商实际账单说成exactly-once。不同ID代表新请求，工具说明禁止因断线、轮询、超时或未知状态自动换ID、改走同步入口。

### 离线验证与部署后一次 LI 使用

[任务登记回归](../tests/test_position_jobs.py)覆盖快速返回、并发同ID去重、参数冲突、忙/容量、结果完整性、2小时到期、防重启重放、线程失败/IPC异常及存储拒绝；[原MCP协议回归](../pocs/chatgpt_skills/test_v42_protocol.py)使用真实loopback streamable HTTP和Worker.main，只有analyze替换为禁网合成函数：start返回并断开连接后Worker继续，反复get/同IDstart不重跑，最终报告逐字段等于Worker原结果；覆盖partial/unavailable/Worker退出。新服务器实例对旧job/旧scope均拒绝恢复执行。原10项工具（启用prepare时11项）保持，增加两项后为12/13；原资产/prepare/人工确认协议测试继续执行。完整PEER兼容由原全量、M5及四项CI覆盖，无新专项验收工具。

本轮仅PR/CI，**未部署、未调用真实模型/Provider**。合并及部署另行授权后：

1. 仅切换同一候选SHA的MCP代码，沿用原完整业务Python、Worker、配置和安全事件文件；无需Schema/账本迁移，不切换Tunnel/Gateway或模型。刷新ChatGPT工具定义，确认原11项加start/get共13项、原权限边界不变。
2. 用户在正常ChatGPT明确发起一次「研究LI:US，使用异步start/get，不传snapshot_id，不同步资产、不准备交易」。ChatGPT取当前工具说明中的scope，生成并保留一个稳定request_id，调用 `start_position_analysis`，保存返回job_id/analysis_run_id。
3. 启动响应若丢失，仅可用完全相同ID/参数查询既有登记；running只按约15秒提示查询同job，不再启动或调用同步analyze_position。每个HTTP调用独立快速完成，原240/270秒专业分析期限不变。
4. 完成后从 `get_position_analysis_result` 读取原报告，按原queried_at、证据和partial/unavailable展示；不把缓存报告当刷新后的当前事实/交易资格。若当前聊天无法等待，保留job_id，稍后让ChatGPT查询同job即可。
5. 对照同一匿名ID的实际持久化事件及原模型观测检查确实只执行一个分析任务、报告内容和最终证据过滤；原正常分析失败/进程重启/到期时如实停止，不自动再付费。此一步需单独真实调用授权，尚未执行，不能以离线/CI证明生产可用性或研究质量已验收。

### 2026-10-28：L1 官方财务原文资格修复

原断点：AV 季度行缺少正式发布日期；`link_disclosure_clocks` 仅有服务端 verifier 合同，没有生产实现。研究卡自报 URL、日期、数值和 hash 不能代替独立核验。

本次在原 `research_v2` 数据层增加 LI 官方原文验证，接入原 evidence Skill，不修改 PEER、Prompt、模型、MCP、技术数据、资产或交易代码：

- 原发行人 IR 索引优先发现财报，研究卡 URL 仅作候选。仅允许 HTTPS 的 LI IR 新闻详情、SEC Archives 完整 submission；禁止重定向及非白名单路径。最多读取一个索引和四份原文，每份上限 2 MiB、网络 timeout 8 秒；无模型、无凭证、无数据库写入。复用既有内存 cache 与 TTL，命中时保留原 fetched_at。Public Demo/AV_DEV_MOCK 不发请求，专项验收原网络白名单不变。
- 来源权限来自独立读取的原文字节、SHA256、原文发布日期节点及表格坐标。IR 发布节点必须一致；明示时区与时分时保留并核验真实时刻，仅有日期时才用日精度；拒绝同日未来时刻和时区不明的时间，TTM 按真实时钟而非字符串排序。SEC 使用原 submission 的 COMPANY DATA、CIK 与 URL 交叉验证及 ACCEPTANCE-DATETIME 的**正式申报日**，不冒充 IR 新闻发布时间或盘中时点。季度期末、发布日期、抓取时间各自保留。版式、主体、日期、单位、币种或会计口径不明则 UNKNOWN。
- 只解析有明确季度列头、季度末、CNY/RMB 和单位的表格单元格。支持 totalRevenue（总收入）、netIncome（合并净利润）、operatingCashflow、capitalExpenditures 的严格标签。归母利润、车辆收入、半年/全年累计、负号不明、重复或歧义单元格不作替代。
- 独立核实的单季度事实进入 `verified_field_records`（`*_quarter`）与原 `professional_explanation` 上下文。它们不进入完整 fundamentals/L3 资格判断。TTM 仍要求四个季度逐字段、主体、报告币种、单位和数值一致；FCF 要核对八个现金流组件，增长率还需去年四个季度。未改 AV 值，收入/净利润冲突只阻断相关字段及其衍生项，保留独立官方季度事实及有效现金流。
- 原 Output Policy 继续过滤解释与精确行动，数字由确定性报告输出。无四季来源时 TTM、无明确现金流口径时 FCF、无真实来源日期时公告时间继续 UNKNOWN；不从 fetched_at、文件名或卡片填补。

离线夹具覆盖来源伪报、正文身份、SEC 申报身份、缺失/冲突/未来公告日、单位/币种/季度口径、重复单元格、季度/TTM 区分、字段冲突、缓存时钟、原工具注入、专业解释最终过滤与 L2–L4 不升级。**本轮未获取真实 IR/SEC 原文、未调用 AV 或付费模型；真实 LI 页面的可访问性、当前版式、可确认的实际字段与真实模型解释质量尚待另行授权复验，不宣称生产已获财务资格。** SEC 仅支持具备完整原始申报头且可解析的季度表格；普通 exhibit、XBRL 或 PDF 不猜测补全。

### PR #48 合并前：真实 2026 Q2 IR 适配修正（2026-10-28）

经最小公开 IR 只读访问，确认当前新闻导航为 `https://ir.lixiang.com/news/`，其真实公告链接为 [LI 2026 Q2 原文](https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-announces-unaudited-second-quarter-2026-financial)。默认及 `zh-hans` 公告路径均实测 HTTP 200；本轮不调用 AV、模型、券商或生产分析。原 `/news-releases` 索引及 `financial-results` slug 后缀假设已移除；改用真实导航中的明确财报标题发现。主机仍严格限定，IR 只支持 `/news` 及无前缀、`zh-hans`、`zh-hant` 的新闻详情路径，不支持任意 URL、路径穿越或开放重定向。

使用真实原始字节对照，原 HEAD 的观察数为 0；修正后当前季度四字段均可解析：营收 CNY 25,666,890,000、合并净利润 CNY -1,705,257,000、OCF CNY 15,025,000、内部正向 CapEx CNY 1,315,790,000。原公告日 2026-08-26 来自原文发表节点；中文日期节点按原文解析，不从 URL、抓取时间或财务期末补造。

- 优先使用原正式合并损益/现金流表，排除百万单位摘要的近似数与非 GAAP 调整表。千元单位、季度列头、RMB/US$ 并列列及合并单元格保留；只读取明确的 RMB 列。支持原始 `Net income/(loss)` 与 `Net cash (used in)/provided by operating activities`，不混用归母利润或投资/融资现金流。
- 同一原文中完全一致的重复单元格合并为一个观察并保留多个坐标；有数值/口径冲突的重复仍拒绝，不挑选与 AV 相符的一条。
- 仅在明确现金流表、同一季度/币种/单位且相邻 OCF/CapEx/FCF 原文勾稽成立时，将负数 CapEx 现金流出规范化为正向支出；保留 reported_value、reported_unit_scale 和规范化依据。原表当前季度为 `15,025 + (-1,315,790) = -1,300,765`（千元）；内部仍为 `OCF - positive_spend`，没有颠倒 FCF 方向。
- 将真实页面裁成无脚本、无私人数据的最小离线 fixture，保留来源 URL、原文 SHA256、原抓取时点、日期节点、表格跨度、字段值及单位。原整页与裁剪 fixture 分别记录 hash，不把裁剪文件冒充原始响应。
- 当前公告只有三个比较期间，不能替代四个连续季度的 TTM 证明。季度与 TTM 独立资格、错误币种/累计期间/歧义字段/不可信日期的拒绝行为保持。未修改异步 MCP、PEER、模型、资产、交易或专项账本。

本次证明真实公告适配与解析，不等同生产研究复验；公开站点可达性可能波动，403/不可达仍 fail closed，无自动付费重试。

## US financial sources P0.1

The focused follow-up reuses the existing issuer/research/evidence owners for exact SEC ticker/CIK resolution, Submissions discovery, standard CompanyFacts and restricted original filing facts. AV and its TTM calculations remain authoritative inputs; source supplements support qualified L1 facts without changing L2–L4 or trading gates. See [scope, trust boundaries and representative verification limits](V42_US_FINANCIAL_SOURCES.md). Local real SEC API downloads are not claimed successful when blocked by HTTP/TLS access failures.
