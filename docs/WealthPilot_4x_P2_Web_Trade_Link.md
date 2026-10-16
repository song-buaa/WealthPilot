# P2：个人 ChatGPT Pro → 原 Web 最终确认

P1 三项查询保留。`prepare_trade_confirmation_link(order)` 仅校验显式参数并生成 URL，不打开数据库、不创建草稿、不连接券商、不下单。真实 MCP 只暴露这四项只读工具；原 Mock 写工具仅保留给自动化测试，不新建 Mock App、Tunnel 或生产服务。

## 使用与合约

设备设置 `--web-base-url` / `WEALTHPILOT_WEB_BASE_URL` 为本人 Tailnet 私人 HTTPS 根地址；工具不接受模型指定跳转地址。缺少配置明确失败，不返回假链接。URL 格式：`/#/action?trade=1&request_id=<uuid>&symbol=NVDA%3AUS&side=BUY&quantity=10&order_type=LIMIT&limit_price=180&currency=USD&time_in_force=GTC`。

参数放在 hash fragment，不作为 HTTP query 发给服务器；链接本身不是授权凭证。每次工具生成新 request_id，同一链接重复打开复用同一草稿和首次订单尝试；新交易需新链接。参数包含显式市场、方向、整数数量、订单类型、所需价格及币种；缺失、重复、无效、不支持的参数明确失败，不推测。symbol 复用唯一标准化工具，币种必须匹配。沿用原单标 GTC，不新增订单类型或有效期。

HashRouter Action 页面调用 `/api/action/trade-link/preview`，在内存打开原 ActionDraftCard 并预填，可修改参数。生成链接和打开预览均无业务写入。点击“加入投资行动”才经原 `/api/action/drafts`、confirm 创建原策略，直接打开原 ConfirmOrderDialog，保留原风控、文字确认、复选框和最终 place_order。链接来源使用已有草稿字段与 SQLite 事务防止重复创建/提交，不改变非链接路径规则；rejected/unknown 也返回首次尝试，不自动重试。

CONDITIONAL_LIMIT 保留原策略及适配器语义：展示触发价，不宣称新增券商止损单或自动触发服务。真正提交仍按原 OrderManager/BrokerAdapter 规则执行。

订单复用原列表和行动记录。Web 对待结订单通过原 OrderManager.sync_order_status 按需刷新；失败保留旧状态并提示核实，不新建 OrderPoller 或调度服务。最终确认显示配置的券商模式；Mock 明确为模拟，配置状态不等于连接认证成功。

## 真实券商与网络

用户选择 Tiger 优先。原工厂配置为 `BROKER_MODE=tiger.live`、`ENABLE_TIGER_LIVE_TRADING=true`，凭证仍用原 KeyringCredentialProvider 的 `tiger.live`。如尚未绑定，只在 Mini 本机复用既有 Tiger 数据凭证写入原 Keychain，不创建新券商 Key、不传回 MacBook/Git/聊天。先通过原适配器只读认证；首次真实委托由用户在实际需要时亲自确认，不能为验收专门下单或用 Mock 成交替代。

设备初检为 ibkr.readonly、Gateway 未监听；该事实不代表 Tiger 就绪。设备验收收据独立记录实际结果。任何凭证或账户权限失败都如实标为 BLOCKED。

统一入口采用 Tailscale Serve 私人 HTTPS → 原 loopback 8790，仅本人 Tailnet，禁用 Funnel。Vite preview 只允许设备自身的精确 HTTPS hostname，不使用 allowedHosts=true。iPhone 须登录同一个人 Tailnet 并连接；不修改公司 VPN/Clash。桌面浏览器和真实 iPhone 独立验收，模拟尺寸不冒充手机实测。

## 发布与回滚

沿用 MacBook 分支 → PR/四项 CI → main → Mini install-github 固定 SHA。复用完整 Web、原 backend 环境、shared WAL 生产数据库和原 MCP/Tunnel。无 schema 迁移，不启用新任务。生产配置/凭证/备份在代码外。

先安装候选、独立验证四读取和纯预览；备份旧 MCP/Web/backend 启动入口与运行配置，仅更新固定 Git 版本及前端构建引用。保留全局 current 和 Tunnel 身份/凭证。系统服务切换需要管理员授权，检查注册和健康后才宣布通过；不复用曾失败的首次切换脚本。

回滚恢复本轮备份的启动文件、原运行配置并验证健康；不覆盖/回滚生产 DB，不删除草稿/订单或 Keychain。Serve 如需恢复，仅关闭本轮 443 代理，不 reset 其他网络配置。Tiger 未验收则恢复原只读配置。

## 验证

- 原服务/临时 SQLite + MockBrokerAdapter：纯链接、预览无写入、错误参数、草稿确认、订单状态、重复点击、P1 回归。
- 独立 MCP 环境：生产只暴露四读取、链接调用数据库哈希不变、原 Mock 自动化链路。
- Playwright：桌面预填/修改/确认/Mock 成交/历史/重复链接，以及手机尺寸弹窗和取消；纳入原 MCP CI Job，不新增基础设施。
- 设备：真实四工具、私人 HTTPS、MacBook Safari、ChatGPT Web/iOS 点击和预填实际验证。
- 首次真实提交/成交尚未发生时只将该项保留待用户操作，不追加模拟生产环境。

PoC 02 凭证展示事故保留历史 FAIL；P3/P4/P5 不在本轮范围。

参考：[OpenAI 工具声明](https://developers.openai.com/plugins/reference)、[连接刷新](https://developers.openai.com/plugins/deploy/connect-chatgpt)、[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。


## IB01 / IBKR 入口兼容修复

历史 IB01 成交使用 Typed Trade Intent → ExecutionBatch → 原 Web 人工确认 → IBKR：
`conId=354802220`、`ISIN=IE00BGSF1X88`、LSEETF 上市、USD、SMART 执行。
普通单标入口仅识别 CN/HK/SH/SZ/US，并以币种反推市场；USD 并不能证明 US。
因此不能只扩大 UK 白名单，或把 IB01 改写成 IB01:US 后走当前 Tiger 模式。

`prepare_trade_confirmation_link` 为 IB01 提供既有 IBKR 批次路径的预算参数变体：

```json
{"order":{"broker":"IBKR","symbol":"IB01","side":"BUY","order_type":"LIMIT","currency":"USD","target_amount":"4000"}}
```

接受 IB01、IB01:UK、IB01:LSE、IB01:LSEETF，仅通过已有可信合约登记归一为
IB01:LSE，不开放任意 UK 证券。工具结果包含登记的合约身份与 `execution_path=ibkr_batch`；
URL 使用 `trade=ibkr`，聚焦当前 request_id；历史列表仍保留，但不会在此流程加载。

### 统一确认体验与含费用预算（2026-10-16 产品确认）

IB01 继续使用原 ActionDraftCard → ConfirmOrderDialog；独立 IBKRTradeLinkPreview 已移除。
预算 `target_amount` 是**包含费用与现有资金安全垫的 USD 总支出上限**，不再是名义金额。
新 Typed Intent 使用 `ALL_IN_TOTAL`，既有 FIXED_TOTAL/历史 Case 1 金额语义保持不变。
`整数股数 × 限价 + IBKR WhatIf 费用预留 + 原安全垫 <= 用户预算`，同时要求真实 USD
现金足以覆盖该预算。复用原整股/WhatIf 收敛算法；费用不明、延迟/缺失/过期行情、休市、
合约身份不符或资金不足均不可确认或提交，不使用估算行情冒充 LIVE。

打开链接先取得登记身份，再调用 `/api/execution-batches/trade-link/quote`：在一次性内存
SQLite 中运行原 Typed Intent / ExecutionBatchService，读取原 IBKR 事实及 WhatIf 后销毁。
不打开或写生产数据库，不创建 SymbolStrategy/OrderRecord，不调用 place_order。
展示合约、账户掩码、方向、预算、报价时间/质量、建议及实际限价、整股数、费用、安全垫、
资金占用与阻断原因。纯登记预览不代表 Gateway/行情/WhatIf 可用。

用户修改限价后必须重新取报价、合约和现金，重新计算数量/费用及 WhatIf。点击意图确认时
绑定显示过的报价时间和经济参数 hash；报价过期或最新经济事实变化需重新预览。
确认意图后保存原消息/意图及批次，直接进入同一个 ConfirmOrderDialog，原批次 ID 与
request_id 关联。确认计划锁定版本，再由用户最终确认；最终按钮仍受原 IBKR 开关控制。
持久化批次改价会先取消旧批次、撤销 hash/递增版本，再重建事实；即使重算失败旧确认也失效。
重复打开同一请求不创建第二笔交易；终态不自动重试。

统一弹窗根据批次 ID 展示状态，通过原订单状态同步刷新后读取该批次；不会把历史批次
当作本次结果。最终仍调用原 ExecutionBatch.submit_next_leg → OrderManager → IBKR SMART，
保留合约、WhatIf、资金、报价、版本/hash 和人工确认检查。不会调用普通 Tiger 下单路径。
美股/港股保留原草稿、确认组件和业务调用方式。

自动化使用一次性库和 Fake/Mock Broker，桌面/移动尺寸测试不冒充 Safari/iPhone 实机。
生产验收仅打开预填、内存报价和确认界面，不创建或提交真实订单，也不启用实盘。
Gateway Read-Only 阻止 WhatIf 时如实显示阻断，不调整开关。

新 IBKR 链接固定 DAY，与原 IBKR `_build_live_limit_order` / WhatIf 的实际有效期一致；
上一版误标的 GTC 链接需重新生成。普通美股/港股单标路径继续原 GTC 契约，不修改适配器。
