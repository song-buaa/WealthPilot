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
