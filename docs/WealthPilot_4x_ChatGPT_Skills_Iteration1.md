# WealthPilot 4.0 · ChatGPT Skills 迭代 1

日期：2026-10-15。基线：`d5c4291ed0202a460be1f51056fd69748065fda6`。仅 MacBook Dev；保留全部既有工作树/未提交文件，不连接 Mini，不读取个人数据库，不提交真实委托。PoC 02 凭证展示事故仍为历史 **FAIL**，本轮没有补救该历史事件。

## 已交付边界

新增 `pocs/chatgpt_skills` 独立 MCP 入口，不改当前生产 runner、完整 Web、PEER、SkillsLoader、订单引擎、风险阈值、SQLite 模型或数据库权限。ChatGPT 通过工具 schema 提取明确指令，服务端不增加 LLM 或新的交易解析引擎；不包含其余投资分析意图。

P1 保留 `get_wealth_overview` 契约，增加最少两个详情工具：`get_manual_wealth_items` 与 `get_investment_details`。直接调用 WealthService.list_items、PortfolioService.get_positions/get_summary 和既有聚合器；不另算资产负债总计/收益/仓位。只返回字段白名单，删除 notes、原账户 ID、凭证与原始错误。账户返回稳定 opaque ref，可据此过滤；不是新增账户资金账本。日期未知、旧来源和最近同步失败保持 partial，现金来源失败保留持仓但不返回伪零完整汇总。

每次 P1 请求复用 `readonly_snapshot` 的 mode=ro、query_only、SQLite authorizer 和单事务；服务 session 绑定到同一连接，禁止财富查询附带快照写入。已有共享 WAL 契约继续校验数据库身份/Portfolio。一笔请求内一致，后续请求能看到已提交变化。

P2 是套餐无关的四个工具：草稿、预览、确认、状态。`ActionDraft → SymbolStrategy → OrderManager → BrokerAdapter` 沿用原实现，当前 worker 仅构造 MockBrokerAdapter，真实读取配置不能启用交易。BUY/SELL、限价及条件限价沿用原 OrderRequest 类型；数量、价格、币种必须明确，不能由模型补猜。预览包含标的、方向、数量、订单类型、限价/触发价、币种、GTC、配置账户范围、原风险提示；费用明确未估计，风险 FX 采用原单订单 FALLBACK_RATES，并标注不是实时汇率。此次不宣称 LSE ExecutionBatch/WhatIf、真实资金/合约或多券商账户选择已经通过。

确认绑定完整预览及风险 hash；重新读取原风险规则，沿用原风险确认文字。纪律数据直接使用既有聚合器、discipline/config 和 rule_engine.check，不走包含投研/LLM/市场回退的完整 data_loader.load，未修改规则引擎。只给原 API 两个金额 helper 增加可选 Portfolio 参数，原 Web 默认行为不变。

确认与策略先持久化再提交 Broker；崩溃后不自动重放未知请求。相同 request_id 重用草稿，变更参数冲突；同一确认重复调用只返回既有订单尝试。保留拒单、部分成交、未知状态，状态刷新沿用原累计成交与审计写回。因刷新本身会写数据库，`get_trade_order_status` 正确标为写工具。Web 修改草稿不能绕过已预览的参数。

## 本地验收

所有数据库和券商都是临时合成/Mock。运行方式见 [入口 README](../pocs/chatgpt_skills/README.md)。本地/CI 测试结果以当前 PR exact HEAD 为准。

| 项目 | 状态 | 证据 |
|---|---|---|
| P1 字段、现有金额/仓位口径、账户隔离及敏感字段过滤 | PASS | `tests/test_chatgpt_skills.py` |
| P1 单事务 WAL、下一请求新值、误写拦截、无 wealth_snapshots 写入 | PASS | 同上 + 原 shared_database 回归 |
| P1 来源未知/过期/失败，保留最后持仓 | PASS | 同上及原 reader 回归 |
| BUY/SELL、条件单、完整预览、原风险提示、确认、Mock 成交/状态 | PASS | 同上，使用原 OrderManager/MockBrokerAdapter |
| 无确认/旧hash/草稿改动拒绝、重复/并发确认仅一单、拒单/网络未知、崩溃/重启不重下 | PASS | 同上 |
| Streamable HTTP 七工具发现、annotations、输出 JSON Schema、错误恢复、完整模拟链路 | PASS | `pocs/chatgpt_skills/test_protocol.py`，独立 MCP 环境 + 业务子进程 |
| Backend/Frontend/Offline M5/旧 MCP/部署/共享库回归 | PASS（本地） | 后端929 passed/7 skipped；前端lint/build；M5 18/18；旧reader/部署73、共享库34、旧HTTP11+9、Web39、同步56 passed |
| 当前账号实际发现/写工具确认，Business Web/iOS | BLOCKED（未实测） | 本轮没有 Business 账号会话或购买要求 |
| Mini 新入口与真实 P1 明细 | NOT RUN | 本轮不操作生产 |
| 真实委托、真实订单状态 | NOT RUN | 本轮无交易授权，未接实盘适配器 |

新增业务定向测试 **33 passed**，独立 MCP HTTP **5 passed**（包含交易输出 Schema 验证）。测试只用临时合成库和 Mock，无真实财富/委托。初轮测试发现的纪律上下文绑定、错误返回遮蔽和测试模块别名冲突已修正，并通过上述最终回归。前端在原生 ARM Node 下通过；已有大 bundle 提示保留。GitHub exact HEAD 的四项 Gate 结果另随 PR/仓库外发布收据记录，本地通过不代替 CI 或设备验收。

## ChatGPT 套餐与确认验证

按任务给定的 Pro/Business 能力边界开发，不据此宣称账号权限已通过。2026-10-15 查阅的官方文档支持工具读写注解、显式 schema、后果性操作确认和私有连接流程；本次取得的页面没有完整给出 Pro/Business/Enterprise/Edu 的套餐矩阵，因此该矩阵仍须目标账号实测。API 的 approval 参数也不等于 ChatGPT 套餐/手机支持证明。

- [官方 MCP Server 指南](https://developers.openai.com/plugins/build/mcp-server)：工具注解只是元数据，不替代授权或用户确认。
- [ChatGPT 连接指南](https://developers.openai.com/plugins/deploy/connect-chatgpt)：在目标工作区核实实际可用入口和连接流程。
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)：沿用个人范围边界；Platform Tunnel 权限与 ChatGPT 工作区能力分别核对。

Business 试用时只需先验证现有 Mock 端点，无需当前立即购买：

1. 核实目标工作区的自定义 MCP 和写工具权限、私人 Tunnel 范围；用合成数据连接，记录套餐、日期、浏览器和 iOS 版本。
2. 发现七工具，确认 create/confirm/status 没有误标 readOnly；展示完整参数、金额单位、synthetic 和风险说明。
3. Web 发明确 BUY/SELL，生成草稿；无用户确认时委托数为零，拒绝/取消平台确认时也为零。确认前修改指令必须重新预览。
4. 展示预览后，用户在新一轮明确确认，并通过平台实际写工具确认；服务端核对 hash 与原风险文字。核对成交/部分成交/拒绝/未知、重复确认及断线后不重下单。
5. 在 iOS 新对话独立重做，不能用同步的 Web 历史代替。保存脱敏服务端证据，仅记录工具、状态和比较布尔值。
6. `confirmed=true` 只是工具参数，无法证明真实人类点击。未完成平台确认实测前，不能把本地 Mock PASS 等同于真实交易准备完成；真实 Broker 接入另需原有人工授权、合约/资金/风控及对账验收，不扩大本轮范围。

## Web 备用确认与下一步部署

备用路径只评估复用现有 `frontend/src/pages/Action.tsx`、`ActionDraftCard.tsx`、`ConfirmOrderDialog.tsx` 和 `/api/action`。目前 Action 仅支持 tab URL 参数，没有按 draft_id 自动定位/预填确认的完整深链路；若平台确实不能完成确认，再最小增加原页面的定位/参数绑定，不新建交易界面。本轮没有开发该备用路径，也不宣称已有一键跳转。

下一步先审查 P1 明细的数据开放范围，将 CI 通过的完整 revision 按既有 GitHub 流程安装到 Mini 的独立候选入口，保留现役 MCP/Tunnel/Web；复用原私有配置和共享 WAL contract，进行 P1 真值对比及 Web/iOS 读取验收，再决定切换。失败时停止候选进程、保留现役版本，不恢复/覆盖生产数据库。P2 先以个人批准的试用账号验收独立 Mock 连接；套餐权限与真实执行授权是两个不同条件，不能用一项代替另一项。
