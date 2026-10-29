# WealthPilot 4.3｜E1-N1 自然复核与主动提醒收口

## 实现结论

E1-N1 继续由 E1 私人研究域持有，不新增研究引擎、新闻平台、账户或交易服务。`NaturalReviewScanner` 复用 schema v2 的 `ReviewMonitorStore`、4.2 已合入的 SEC directory/Submissions/full-submission 适配器和同一私人 SQLite：只扫描显式 opt-in 且带正式事件条件的当前 monitor，追加水位/扫描收据和待复核任务，不修改 Thesis/Review。

首次扫描只以当时可见 accession 建立水位，不回放历史事件，也不抓历史原件。后续候选必须同时通过 ticker→CIK→Submissions 身份、accession、form、SEC acceptance clock、固定原件 URL、full-submission header 和 SHA256 核验。`10-K/10-Q/20-F` 映射为正式财报，`8-K/6-K` 仅映射为中性的正式监管披露；任何事件只表示“需要复核”，不推断方向。原件失败保留为 `PARTIAL_SOURCE_FAILURE` 并重试，不伪装成“没有新事件”。

### SEC 时钟语义修复候选（2026-10-29）

生产验收在官方 directory HTTP 200 后发现，原实现把网络抓取完成时间 `fetched_at` 当作扫描的 point-in-time 截止：请求正常耗时 679.722ms 即被误判 `SOURCE_UNAVAILABLE`，下游事件层也会拒绝已公开但稍后抓取的原件。本修复明确分离两类时钟：`as_of` 只约束 filing 的 SEC acceptance / `published_at` / `known_at`；`fetched_at` 保留真实取得时间，只要求格式有效、带时区且不早于该披露。directory/Submissions/原件的 CIK、accession、URL、header、SHA256、身份和失败隔离校验均不放松。

冻结回归覆盖真实网络完成时间晚于扫描开始时间、历史截止前的未来 filing 不进入水位、截止后同一 filing 可发现、坏/无时区抓取时间继续失败、首次水位不回放。该提交只是本地修复候选；合并、Mac Mini 安装、真实 SEC 水位及水位后自然新 accession 仍须后续明确发布授权和运行证据。

扫描默认由 `WEALTHPILOT_E1_NATURAL_SCAN_ENABLED=false` 关闭。一次手动运行最多 10 个 monitor、31 次 SEC 请求，每个 monitor 最多处理 3 个新事件；源失败按 monitor 隔离。运行入口：

```bash
WEALTHPILOT_E1_NATURAL_SCAN_ENABLED=true python -m backend.services.investment_thesis.monitoring_cli \
  --store <private-e1.db> --portfolio-id <fixed-scope> natural-scan \
  --as-of <timezone-aware-ISO8601> --run-id <unique-run-id>
```

命令不会安装调度、调用 PEER、刷新账户、发送通知或保存研究判断。相同经济事件用 `SEC:<CIK>:<accession>` 和 thesis/revision 形成稳定 task key，水位与 task 均跨重启持久化；版本推进、关闭和取消继续要求重新 opt-in。

## ChatGPT 只读待办与提醒语义

显式 E1 私人库新增只读 `get_due_investment_reviews`。它只投影 `PENDING` 和已到延期时间的 `DEFERRED`，最多 50 项，支持稳定 cursor 与 `known_event_keys`。返回 `event_key`、Thesis/revision、触发原因、SEC 时点/来源、最近 Review 和下一步动作；不扫描 SEC、不改变任务状态、不运行模型、不写研究记录、不通知或交易。

该接口提供 `AT_LEAST_ONCE_STABLE_EVENT_KEY`，不是 exactly-once 推送。ChatGPT 可把已展示的 `event_key` 传回以抑制本次对话中的重复；在用户完成或延期前，另一会话仍可能再次看到同一待办，这是可见且保守的语义。只有显式 task transition 才改变服务器状态。

[OpenAI Scheduled tasks](https://learn.chatgpt.com/docs/automations) 官方说明显示，任务可在支持的 ChatGPT 计划/工作区中使用该聊天可用的 connected tools、skills 和 plugins，并建议先手工测试提示词。因此最短提醒路径是：在已连接 WealthPilot-Prod 且能发现该只读工具的聊天中，创建低频 Scheduled task，定时调用 `get_due_investment_reviews`，无新 `event_key` 时静默，有新任务时提示用户回到原 Thesis→可选 PEER→Review 预览→明确确认保存流程。E1-N1 发布后已完成私人连接的真实 Scheduled task 调用、单条 TEST ONLY 提醒、稳定 event key 抑制及 HANDLED/取消清理；自动扫描仍为 OFF，WealthPilot 自建通知仍为 `NOT_WIRED`。

若目标 ChatGPT 工作区不允许 Scheduled task 使用私人 connector，最短替代是沿现有 Mac Mini 运行架构低频执行 `natural-scan`，用户进入 ChatGPT 时手工调用只读待办工具；不建设新的推送服务。后续如需主动通知，应先验证平台能力，再选择现有 ChatGPT Scheduled task；失败时保持 `NOT_WIRED`。

## 测试与上线验收

冻结测试覆盖：无 opt-in 零网络；首次水位零历史待办；新增 SEC 原件生成一个任务；重复扫描/进程重启不重复；原件失败非“无事件”且可重试；身份不匹配、未来时点、取消 monitor 拒绝；分页和已见 key 抑制；原 Thesis/Review 哈希不变。入口为 [N1 回归](../../tests/test_e1_n1_natural_review.py)、[B3-2 回归](../../tests/test_e1_b32_monitoring.py)和[独立 MCP 协议](../../pocs/chatgpt_skills/test_e1_save_protocol.py)。

SEC 时钟修复的一次性发布验收顺序：

1. 单 PR 跑原四项 CI；合并后 Mac Mini 仅安装确认的完整 main SHA，不改变 E1 schema v2、原进程、Skills 包装器或 Tunnel。
2. 切换前备份并核对私人库、原 Thesis/Review/monitor/task 数量和 canonical SHA；切换后确认 schema/integrity、18 项 MCP 及原研究保存行为不变。
3. 自动扫描继续 OFF。只对已明确 opt-in 的 TEST ONLY monitor 手动运行一次真实 SEC 扫描，确认正常网络耗时不再产生 `SOURCE_UNAVAILABLE`，首次水位只含 acceptance 不晚于 `as_of` 的 accession，且不回放历史事件。
4. 立即以新 `run_id` 重扫，确认水位和 task 去重跨进程保持。水位后若出现真实新 accession，再核验原件 header/SHA、待办及重复去重；验收窗口没有新 accession 时标记 `NATURAL_NEW_EVENT_PENDING`，不伪造或无限等待。
5. 已通过的 ChatGPT Scheduled task 提醒链路不重复创建测试提醒。全程不运行真实 PEER、不刷新券商、不调用交易、不自动保存 Thesis/Review。异常时恢复原代码配置并保持扫描 OFF，保留 append-only E1 数据。

ChatGPT Scheduled task 实际工具调用和用户主动提醒已经通过；当前唯一未清阻断是 SEC 自然扫描时钟缺陷及水位后自然增量证据。本地修复候选不等于已合并、部署或真实 SEC 复测通过。只有修复发布、官方读取与首次水位通过，并在水位后取得真实新 accession 的发现/原件/待办/去重证据，才能按实际证据把 E1 标记为 `E1_RELEASED`；否则保持 `E1_CORE_RELEASED_N1_NOTIFICATION_PASS_NATURAL_NEW_EVENT_PENDING`。
