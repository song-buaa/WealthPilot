# WealthPilot 4.3｜E1 最终集成、发布与运行验收计划

2026-10-29。E1-B3-2 PM Review 后的一次性发布准备。当前仅准备，不授权 push、PR、merge、Mac Mini 安装、生产数据库迁移、自动扫描、通知或任何研究记录保存。

## 1. 已确认事实与发布判断

- 2026-10-29 实际 `git fetch origin main` 后，`origin/main=a2def5d09ba4968845c3733735643523dee44d66`；`codex/e1-b3-2` 的 `4ed74b7c8e033d69bbbc3b0e2c04af69e98fe7f3` 仅领先一个提交，main 是其直接祖先，当前无合并冲突。
- 差异只涉及 E1 监控域、显式手动 CLI、冻结 SEC fixture、回归和 4.3 文档；不改原 PEER、market data、账户、持仓、交易或 4.2 活跃工作树。原 `ThesisStore` 仅扩展为读取 schema v1/v2。
- E1-I1 的现役生产入口是 Mac Mini 私有 `skills-iteration1/Run_Skills_Endpoint.py` 包装器及原 Tunnel，不是仓库历史 `pocs/poc03_mac_mini/runner.py`。`install-github` 只安装已合入 main 且 exact-SHA 四项 CI 全绿的 release，不自动切换。发布不得误切旧 PoC runner。
- 当前 E1-I1 已有 17 项 MCP 工具。B3-2 核心不注册新 MCP 工具；初始发布后仍应为同 17 项，参数 schema/注解与发布前逐项相同。
- B3-2 没有 scheduler hook、事件网络拉取或通知发送器。因此部署和 schema 迁移不会自动扫描；状态必须保持 `AUTO_SCAN=OFF / NOTIFICATION=NOT_WIRED`。

本轮只读查看 Mac Mini 生产对话的最新证据，现役生产代码已报告为 `a2def5d09ba4968845c3733735643523dee44d66`，MCP 仍沿私有 Skills 包装器运行；该对话同时有其他 4.2 可靠性工作，故正式切换前必须再次核对 SHA、活动任务和包装器，不据此直接操作生产。

发布可进入一个 PR；没有必要再做全面架构审计。生产实际 SHA、包装器参数、解释器、E1 路径及 portfolio scope 仍须在获发布授权后的设备预检中重新读取，不能只用本文件推定。

## 2. 一次性 Git 批次

获完整发布授权后按以下顺序执行：

1. 再 fetch `origin/main`。若 main 仍是上述 SHA，直接 push `codex/e1-b3-2`；若已推进，只对齐已集成 main，禁止取未合并的 4.2 工作树提交。发生真实冲突时仅处理重叠文件并重跑全部 Gate。
2. 创建一个 PR，base=`main`，head=`codex/e1-b3-2`。PR 明确 owner 为 E1 私人研究域、迁移为独立 E1 DB v1→v2、生产扫描默认 OFF、原 17 项 MCP 不变、无交易/账户权限变化。
3. 对 PR exact HEAD 核对四项 GitHub Actions：`Backend tests`、`Frontend checks`、`Offline M5`、`MCP PoC tests`；人工 Review 检查 append-only、scope、时点、幂等和部署回滚。任一失败不合并。
4. 按受保护 main 的 linear-history 流程合并。Mac Mini 安装目标必须是**合并后 main 的完整 SHA**，不能预先把 `4ed74b7…` 当最终生产 SHA。
5. 合并后 main exact SHA 的四项 CI 再全绿，才进入设备安装。

## 3. Mac Mini 安装与 E1 v1→v2 迁移

### 3.1 设备预检

仅在后续生产授权内，由 Mac Mini 现有生产对话执行：

1. 记录现役 MCP/Backend/Web/Tunnel SHA、launchd 入口、私有 Skills 包装器哈希、解释器、端口、portfolio scope、E1 DB 绝对路径和启动参数；敏感配置只核存在性/权限/摘要，不输出正文。
2. 本机 `tools/list` 冻结 17 项名称、输入 schema 与注解；确认 E1 四项仍存在。
3. 确认没有运行中的 PEER job；重启不会自动重放旧 job/token。确认 Backend/Web/Tunnel 不在本轮切换范围。
4. 若现役生产 SHA 含目标 main 没有的新能力，停止切换，先形成定点兼容补丁；禁止版本降级。

### 3.2 安装但不切换

复用 `pocs/poc03_mac_mini/deploy.py install-github`，传入合并后完整 main SHA、现役原生 Python 3.11 和实际 hostname。该命令必须证明 main ancestry 与 exact-SHA 四项 CI 全绿，并只创建新 release；不切换 current、不启动服务、不改数据库。

### 3.3 两阶段切换，保留安全回滚窗口

1. **先换代码、不迁库**：备份私有包装器/启动参数；让现役 Skills 包装器引用新 release，但继续使用 schema v1 E1 DB。只重启原 MCP，验证原 17 项、E1 Thesis/Review 只读及预览取消。失败则直接恢复旧代码/包装器；此时数据库未迁移。
2. 验证通过后暂停 E1 保存入口并停止原 MCP。使用 SQLite Backup API 对独立 E1 DB 做一致性备份；记录 `application_id/user_version`、原三张研究表行数、payload/sha256 清单、文件权限及 `integrity_check`，不输出研究正文。备份在 release 外、0600、禁止覆盖。
3. 用新 release 的 business Python 和同一 portfolio scope 执行：

   ```bash
   python -m backend.services.investment_thesis.monitoring_cli \
     --store <现役E1绝对路径> --portfolio-id <现役scope> init
   ```

4. 验证 `user_version=2`；原 `theses/thesis_versions/decision_reviews` 行数、payload SHA 和不可覆盖触发器与迁移前一致；新增 `e1_monitor_events/e1_review_task_events` 及四个不可更新/删除触发器存在且初始为空；目录0700、文件0600。
5. 启动同一个 MCP。再次逐项比较 17 项工具，线上只读回查现有 TEST ONLY Thesis/Review；不创建新研究记录。`AUTO_SCAN` 仍为 OFF，因为没有任何 scheduler 注册。

迁移前代码验证失败时恢复旧代码即可。迁移执行失败或迁移后 17 项回归失败时，在仍禁止 E1 写入的窗口恢复 v1 一致性备份并切回旧包装器。迁移成功且恢复用户写入后，不再用旧数据库覆盖新确认记录；后续故障只能保留 v2 库并前滚修复。

## 4. 一次真实到期复核验收

此验收在部署完成后单独执行，不等于自动调度启用，也不授权保存真实投资判断。

1. 在 ChatGPT 线上创建全新的 `E1-B3-2 TEST ONLY` 合成 Thesis，状态 `WATCH`，不对应真实证券；先预览，用户明确说“保存这次判断”后才保存。
2. 对同一 revision 创建合成 `RESEARCH` Review，结论 `UNKNOWN`，设置距验收约 10 分钟且带时区的 `next_review_at`；仍需单独预览和真人确认。不得调用 PEER、行情、持仓、账户或交易工具。
3. Ops 从只读回查确定 thesis_id/revision/review_id/scope，生成固定 monitor JSON 和 request_id，通过手动 CLI `enable` opt-in；用户不手工拼 JSON。
4. 到期前注入 `as_of` 手动 `scan`：0 个待办。到期时再次扫描：恰好 1 个 `PENDING`；相同 `as_of` 和新进程重复扫描均只返回 existing，不新增。
5. 将任务 `DEFERRED` 到新的带时区时点；延期前 `list` 不返回，延期后返回同 event_key。受控重启原 MCP 后，用新的 CLI 进程仍读到同状态。
6. 将任务转为 `HANDLED`；再次扫描不生成新待办。独立只读核对 Thesis/Review 内容和 SHA 始终未改变，任务状态也未自动保存新 Review。

通过标准：到期、去重、延期、处理、跨重启均为实际生产私有库收据；`research_record_saved=false`、`execution_authorized=false`、`order_created=false`。测试结束后取消 monitor，生产自动扫描仍 OFF。

## 5. SEC 正式事件与通知的最小缺口

### 5.1 SEC 自然接入

现有 `research_v2.sec_disclosures` 已有严格 SEC URL、CIK/发行人解析、Submissions 的 accession/acceptance clock、原件哈希和限速，但 `collect(symbol)` 是按分析请求的当前周期采集，不是有 watermark 的事件 feed。直接实施安排：

1. 只对已 opt-in monitor 的美股 symbol/issuer 做有界扫描，不做全市场循环；复用 `resolve_sec_issuer`、`discover_filings`、SEC request identity/限速和 full-submission 原件校验。
2. 映射为现有 `FrozenDisclosureEvent`：`economic_event_id=SEC:<CIK>:<accession>`，source_ref 使用受限 SEC 原件 URL，published/known 使用 Submissions acceptance clock，fetched_at 为实际取得时间，source_sha256 为原件哈希。
3. 10-K/10-Q/20-F 及已验证财务原件映射 `OFFICIAL_FINANCIAL_FILING`；6-K/8-K 仅作为 `FORMAL_REGULATORY_EVENT` 提醒复核，不推断假设失效或财务方向。
4. 复用 task event_key 做持久化经济事件去重；无身份、无 acceptance clock、未取到原件或未来信息全部拒绝。初始限制单次监控标的集合和请求数，失败降级为 `NO_ELIGIBLE_EVENT_SOURCE`。

这需要一个后续最小代码 PR：E1 官方事件适配器 + 冻结/离线契约测试 + 默认 OFF 的单个 Mac Mini 有界扫描入口；不增加新闻平台、付费供应商或第二套研究引擎。

### 5.2 用户通知

当前仓库没有 ChatGPT 主动通知实现。近期最小方案不是自建推送服务，而是在后续 PR 增加一个只读 `get_due_investment_reviews` MCP 工具，返回 PENDING/到期 DEFERRED 的摘要和 event_key；原 17 项保持原样，新工具不运行 PEER、不修改状态。

随后由用户另行授权在现有 ChatGPT 研究 chat 创建低频 Scheduled task，调用该只读工具，仅当出现新的 event_key 时通知。[OpenAI 官方 Scheduled tasks 文档](https://learn.chatgpt.com/docs/automations)说明，chat 内 scheduled task 可复用现有上下文，Web scheduled task 可使用该 chat 已连接的插件；应先在普通 chat 手动测试，并以实际 plan/workspace 权限为准。若只读轮询不满足需求，[MCP Events 官方文档](https://developers.openai.com/plugins/build/mcp-events)要求 MCP 2.0、持久订阅和出站签名 webhook，属于较大后续工程，不是 E1 结项的最短路径。

通知到达后仍只引导：读 Thesis/Review → 按需原 PEER → 预览新 Review → 等待真人当次确认 → 保存。Scheduled task、事件到达或“研究完成”均不能替代保存确认。

## 6. 最终结项标准

以下全部实际通过后，E1 可结项并转 S3：

- PR/合并后 main exact SHA 四项 CI 全绿，Mac Mini 运行该 SHA；
- v1→v2 迁移前后原研究内容/哈希不变，备份可用；
- 原 17 项 MCP 全部发现并完成受影响回归，E1 I1 读写仍需预览/真人确认；
- 真实到期 TEST ONLY opt-in、扫描、去重、延期、处理、跨重启通过；
- 自动扫描状态明确：初始 OFF；SEC 自然源和通知分别按其后续授权状态标 `NOT_WIRED / LOCAL_PASS / ONLINE_PASS`，不合并成模糊 PASS；
- 无券商读取/同步、prepare、WhatIf、订单、B2迁移或自动投资判断保存。

当前停止点：计划和兼容证据已准备；等待一次完整的 push/PR/CI/merge/安装/迁移/人工验收授权。自动扫描、SEC 自然接入和通知启用仍是单独的后续授权，不随核心发布默认开启。
