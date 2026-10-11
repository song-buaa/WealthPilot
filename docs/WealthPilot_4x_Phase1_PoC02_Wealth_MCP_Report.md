# WealthPilot 4.0 · Phase 1 / PoC 02 验证报告

> 2026-10-11；功能验收 PASS：ChatGPT Web 与真实 iOS 新聊天均通过私人 Secure MCP Tunnel 查询真实财富总览，三个金额与现有财富服务及原 React 页面一致。凭证展示处理 FAIL：运行密钥曾意外进入工具输出，用户明确拒绝轮换并要求继续。两项结论分开，不将事故标记为已解决。报告不保存真实金额、账户标识或凭证。

## 当前结论

本轮最终目标已实现：独立单工具 `get_wealth_overview` 复用既有财富/组合计算，在只读 SQLite 事务内查询原项目真实数据库，返回资产、负债、净资产、CNY 和数据质量状态。实际 Web/iOS 请求返回 `synthetic=false`、`status=partial`，服务端完成与 Tunnel 投递证据均通过。Web 回复与原 Wealth summary 逐分一致，原财富页面按既有整数元格式显示一致；iOS 新请求的金额一致由用户确认。

没有业务代码改动，没有交易、风控、订单或 PEER/Skills 迁移，没有提交、推送或合并 main。PoC 01 已保护。本次验证不等同于生产发布、7×24 可用性或完整安全验收；凭证事故与数据时效性限制仍需后续处理。

## 1. 基线与范围

- main / origin main / 远程 main 核实为 `4da23b1868f3e6dae85a15383405aad3b15da5e3`，不根据旧版本说明推断。
- 原工作目录 `codex/phase1-poc01-private-mcp` 的未提交工作保留。PoC 01 的 32 个文件已复制到仓库外的固定基线，逐文件 SHA-256 摘要见 [poc01-baseline.json](../pocs/poc02_wealth_mcp/evidence/poc01-baseline.json)；原目录及新工作树复制均 32/32 一致，见 [preservation check](../pocs/poc02_wealth_mcp/evidence/poc01-preservation-check.json)。没有提交、推送、合并或移动 Tag。
- 新托管工作树 `/Users/songbin/.codex/worktrees/poc02-wealth-query/WealthPilot`，短期分支 `codex/phase1-poc02-wealth-query`，从上述 main 创建。新增仅限 `pocs/poc02_wealth_mcp/` 和本报告，不修改现有业务代码。
- 参考前序两份 Phase 0 审计报告，重新以当前代码核对财富服务；没有迁移 PEER、Skills、交易、风控或订单链路。

## 2. 工具和业务复用

工具：`get_wealth_overview(currency="CNY")`，输入不接受远程 portfolio/account/db 选择。组合和数据库由本地操作者固定配置；默认仅返回合成数据。

调用关系：独立 MCP → 一次性业务环境 worker → enforced readonly SQLite snapshot → 既有 `wealth_service._current_totals` → 既有 `portfolio_service.get_summary` / `app.analyzer.analyze_portfolio`。

| 发现 / 处理 | 代码证据与含义 |
|---|---|
| Wealth 的公开 summary GET 会写快照 | [wealth_service.py](../backend/services/wealth_service.py) 的 `get_summary` 调用 `_record_aggregate_snapshot`，后者 add + commit；MCP 避开此入口。 |
| 原有计算保留 | 同文件 `_current_totals` 及 `_manual_totals` 处理核心净资产、手工负债、养老金展示排除和投资关联调整；PoC 不复制财务加总公式。 |
| Portfolio 可读取 Tiger 现金 | [portfolio_service.py](../backend/services/portfolio_service.py) `_get_tiger_account_cash` / `get_summary`；旧逻辑部分失败静默返回零。适配器只观察来源结果/告警，保留原计算，失败不冒充完整零现金。 |
| Web 的三个 KPI 使用 Wealth summary | [WealthOverview.tsx](../frontend/src/pages/WealthOverview.tsx) 使用 `summary.net_worth / total_assets / total_liabilities`；不会混用 Portfolio 杠杆口径。 |
| 更新时间并不完整 | [models.py](../app/models.py) Position 没有估值更新时间；WealthItem 有 value_as_of 等字段；[broker sync models](../backend/services/broker_sync/models.py) 记录同步运行。不能用 created_at 或 aggregate snapshot 时间冒充底层数据更新时间。 |

实现及运行说明：[README](../pocs/poc02_wealth_mcp/README.md)、[MCP server](../pocs/poc02_wealth_mcp/server.py)、[readonly reader](../pocs/poc02_wealth_mcp/reader.py)、[worker](../pocs/poc02_wealth_mcp/worker.py)。业务模块的 session 临时绑定仅发生在专用 worker，退出时恢复；不影响现有后端进程。调用私有 `_current_totals` 是明确的短期耦合，未来服务改动需要重新验证。

返回契约 `poc02.wealth-overview.v1`：整数 CNY 分，资产/负债/净资产，查询时间，最新与最旧已知估值日期，ok/partial/unavailable，issues 和来源状态。日期未知为 null；过期、同步失败、现金读取失败和 FX 降级不能隐藏。新鲜度阈值是 PoC 提示而非新投资规则。无账户 ID、持仓明细、资产名称、备注、券商凭证或原始异常。

## 3. 私有连接验证

【已证实】当前账号 Tunnels 页面显示创建入口；用户创建 PoC 02 Tunnel 并通过本地隐藏输入脚本保存运行密钥。认证后的 Tunnel metadata GET 返回 200，关联组织和 ChatGPT workspace 各 1 个；未将 IDs/用户资料/密钥写入报告。客户端 v0.0.16 darwin-arm64 校验官方 SHA256SUMS，MCP 8788 和健康端口 8789 只绑定 127.0.0.1，控制面成功轮询。见 [runtime evidence](../pocs/poc02_wealth_mcp/evidence/secure-tunnel-runtime.json)。

【已证实】无凭证、无效凭证分别访问官方源码确认的 metadata GET 和 poll GET，均返回 401。最初错误使用 POST poll 得到 405，没有将其算作鉴权通过。见 [anonymous probe](../pocs/poc02_wealth_mcp/evidence/anonymous-api-probe.json)。

【边界】这是单用户本机 PoC，信任本机操作者和本机进程。应用层 noauth 由 Tunnel 的认证及关联范围保护；不能接回 PoC 01 的匿名 HTTPS endpoint。Activation evidence 文件只是防止误开放的操作门槛，不是认证协议。未模拟第二个真实 ChatGPT workspace 或他人身份。

【已证实】用户确认所关联组织/工作区为个人范围并要求开放真实数据。用户从 Chrome 发起请求，工具完成记录为 2026-10-11T07:39:31.821404Z，synthetic=true；Tunnel response delivery accepted、HTTP 200。见 [synthetic Web](../pocs/poc02_wealth_mcp/evidence/chatgpt-synthetic-web.json)。按独立时间窗口对应用户请求，未宣称有经认证的客户端身份标签。

【FAIL，用户拒绝轮换】重新绑定 Chrome 原生窗口时，工具自动输出了尚未关闭的 Save your key 弹窗，导致运行密钥进入工具输出。没有将该值复制到仓库/报告，也没有在事故发生时启用真实数据。已停止 Tunnel 并告知用户；用户明确表示“不用换密钥，不用更换凭证”，要求继续。因此恢复使用现有密钥，不声称凭证已撤销或事故已解决。后续浏览器读取使用 emit:false 并仅输出允许的字段，避免自动输出凭证页面。

无需改走受保护 HTTPS，目前已获得 Tunnel 的正向可用证据。若后续 Tunnel 场景遇阻，再评估既有托管认证方案；不建多用户、RBAC 或自研 OAuth。ChatGPT 无任意自定义 API Key/mTLS 提示输入，OpenAI 管理的客户端 mTLS 也不能单独证明个人用户授权。[官方认证说明](https://developers.openai.com/plugins/build/auth)

官方参照：[Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)、[Custom MCP setup](https://developers.openai.com/api/docs/guides/custom-mcp-server)。文档支持不等于账号/手机端已通过，后者必须实际验收。

## 4. 测试矩阵

| 检查项 | 结果 | 证据 / 限制 |
|---|---|---|
| PoC 01 固定、其他未提交工作保留 | PASS | SHA-256 32/32；独立工作树/分支，无提交合并。 |
| 单工具 Streamable HTTP 发现/调用/契约 | PASS | 独立环境 HTTP 测试 9/9；[test_http.py](../pocs/poc02_wealth_mcp/test_http.py)。 |
| 输入/未知工具/Host/Origin 错误与恢复 | PASS | 同上；真实模式缺少通过证据时拒绝启动。 |
| 既有统计口径、临时库无写入、查询一致性 | PASS | reader 测试 17/17；[test_reader.py](../pocs/poc02_wealth_mcp/test_reader.py)。测试 INSERT/UPDATE/DELETE/DDL/ATTACH/mutating PRAGMA 均拒绝；单事务隔离并发变更。 |
| 日期未知/过期/最近同步失败/现金失败 | PASS | 同上，来源异常只返回固定状态，不泄露私人异常文本。 |
| 现有财富服务回归 | PASS | 隔离 fixture `tests/test_wealth_service.py` [12/12](../pocs/poc02_wealth_mcp/evidence/wealth-service-tests.txt)；无真实数据源。 |
| M5 决策回归 | PASS | [M5 offline](../pocs/poc02_wealth_mcp/evidence/m5-offline.md)：18/18，public_network_attempts=0，临时数据库。 |
| Secure Tunnel 入口/创建/控制面可用 | PASS | 实际 UI + 认证 metadata 200 + 正常轮询。 |
| 匿名 / 无效 key 控制面访问 | PASS | 4 项 GET 探针均 401；并非另一个 ChatGPT workspace 测试。 |
| 合成 ChatGPT Web 调用 | PASS | 用户发起请求 + 服务端真实完成记录 + Tunnel response delivery HTTP 200。 |
| 个人组织/工作区范围确认 | PASS | 关联各 1 个，用户确认个人范围并授权开放真实数据。 |
| 浏览器密钥展示保护 | FAIL | 意外工具输出密钥弹窗；用户明确拒绝轮换并要求继续。未标记补救完成。 |
| 真实数据库读取无业务写入 | PASS | [真实预检](../pocs/poc02_wealth_mcp/evidence/real-preflight.json)：原项目 SQLite mode=ro；三个金额非空，Tiger 现金 queried；主库/WAL 内容不变。实际数据 partial：过期/未知日期明确返回。 |
| 实际 ChatGPT Web 真实金额与 WealthPilot Web 对比 | PASS | [Web 回复](../pocs/poc02_wealth_mcp/evidence/chatgpt-real-web.json)：synthetic=false，三个金额与原 Wealth summary 逐分一致；[页面核对](../pocs/poc02_wealth_mcp/evidence/wealth-page-comparison.json)：原 React 页面、原 fmtCny 格式均一致。 |
| 实际 iOS 新请求与服务端证据、Web 对比 | PASS | [iOS 证据](../pocs/poc02_wealth_mcp/evidence/chatgpt-real-ios.json)：用户从真实 App 新聊天发起，第二条独立真实完成记录；[投递](../pocs/poc02_wealth_mcp/evidence/real-ios-tunnel-delivery.json) HTTP 200，用户确认金额与 Web 一致。手机 UI 未由代理直接检查。 |

自动化输出：[HTTP](../pocs/poc02_wealth_mcp/evidence/http-tests.txt)、[reader](../pocs/poc02_wealth_mcp/evidence/reader-tests.txt)，独立环境版本清单见 [environment](../pocs/poc02_wealth_mcp/evidence/mcp-environment.txt)。真实金额比对证据只保存布尔结论与差异是否为零，不保存财务 payload。

## 5. 真实 Web 证据与验收边界

Chrome 实际完成记录为 `2026-10-11T07:49:57.086457Z`，synthetic=false、status=partial；[控制面投递](../pocs/poc02_wealth_mcp/evidence/real-web-tunnel-delivery.json) accepted、HTTP 200。独立时间窗口内未混入本机 MCP probe。浏览器 AX 在本机私有文件中读取最新回复，与原财富 summary 的三个字段用 Decimal 精确比较，全部相等；金融 payload 未写入报告或证据。

现有完整 Web 后端未运行。使用 [web_compare.py](../pocs/poc02_wealth_mcp/web_compare.py) 从原 SQLite 的只读连接建立本地私有副本，运行原 `wealth_service.get_summary` 一次，包含同一既有 Tiger 现金/FX 读取。原 summary 的快照写入只发生在副本。运行新构建、未修改的原 React WealthOverview 页面，并核对其实际显示：Web KPI 的原 fmtCny 四舍五入到元，ChatGPT 精确到分，格式化后三项相同。这是同数据和同原有代码的隔离页面验收，不宣称生产 Web 进程已运行。

[验证服务检查](../pocs/poc02_wealth_mcp/evidence/web-validation-harness.json)确认只监听 loopback，POST/PATCH/DELETE/PUT 返回 405、构建 index 与实际服务字节一致、真实主库/WAL 内容未变；没有运行 init_db、业务后台生命周期任务、同步或交易接口。

真实数据状态为 partial，issues 为 LIVE_CASH_VALUATION_DATE_UNKNOWN、STALE_COMPONENTS、VALUATION_DATE_UNKNOWN；三个金额可用，既有 Tiger 现金 queried。查询时间与估值日期分开，缺失日期返回 null。金额一致仅证明同口径与数据传递正确，不证明旧数据具备实时行情准确性。

## 6. iOS 证据、未验证项与后续建议

iOS 请求服务端完成于 `2026-10-11T07:54:44.573298Z`，synthetic=false、status=partial；控制面投递于 `07:54:44.867410Z` accepted、HTTP 200。用户确认从真实 iOS App 的独立新聊天调用并与 Web 回复一致；没有用同步旧聊天代替手机调用。客户端归属依据单独请求时间窗口与用户确认，工具协议本身未提供可认证的 Web/iOS 标签。手机显示的逐分对比是用户确认，代理直接精确比较的是 Chrome 回复与财富 summary。App 版本 1.2026.258 为同日 PoC 01 用户提供，本轮未再次查看版本。

本轮最终功能验收没有剩余 BLOCKED 项。以下能力仍是待验证，不能标记 PASS：第二个未关联工作区/他人身份拒绝、运行密钥最小权限及撤销验证、长时间运行/断线恢复、Mac Mini 常驻与 ARM 业务环境、生产 Web 运行时比对。此次 ARM Tunnel 客户端运行成功，业务 Python 实际仍为现有 x86_64 环境；不宣称完成全栈 ARM 验证。

后续建议限定为迁移所需问题：

1. 正式常驻前处理本次凭证展示事故；当前依用户决定保留凭证，不声称已撤销或替换。记录并验证运行密钥权限与回收流程。
2. 评估正式只读财富服务入口，避免长期依赖私有 `_current_totals` 与 worker 内 session 绑定；保持既有财务口径，本轮不重构。
3. 完善估值日期/来源失败契约，并验证私有 Tunnel 重连、启动顺序和 Mac Mini 环境。当前 partial 反映既有数据的真实限制。

真实配置与凭证始终在 Git 外 0600 文件中；完成验收后停止 MCP、Tunnel 和页面验证进程，清理本次生成的真实数据库副本及财务/浏览器临时 payload。未删除用户创建的 Tunnel、插件或运行凭证。收尾证据见 [cleanup](../pocs/poc02_wealth_mcp/evidence/cleanup.json)。
