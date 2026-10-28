# WealthPilot 4.3｜E1-B2 本地真实案例验证

日期：2026-10-28。B1 锚：`52a1e1c2de3664d3908860f5840c643c02070ac7`；本轮 fetch 后已集成 `origin/main` 仍为 `b386bab10a72ee09253bfa5bf746cb89d130354b`。继续使用 `codex/e1-manual-mvp` 独立 worktree，不合入并行 4.2 工作。

## 结果与边界

复用 [B1](E1_B1_IMPLEMENTATION.md) 的 CLI、append-only store、Review 和可选只读 MCP。美团、NVIDIA、第三个新案例 META 欧盟广告同意机制，实际完成三条真实来源→thesis v1/v2→Review→diff→重启读回。非合成记录共六个版本、五份 Review；私人库/原始资料/原 PEER/用户回忆均在仓外 4.3 私人证据目录，禁止提交 Git。

三例均通过已连接原 PEER 的 start/get 取得真实结果，均为 `partial`、`synthetic=false`；同一 job 取回，没有同步 fallback 或重复执行。原响应 provenance 独立归档；CLI 显式导入仍为 `USER_SUPPLIED_ANALYSIS_COPY`，不改称服务端认证的 `ORIGINAL_WORKER_RESULT`。原状态、queried_at、evidence、quality、limitations 保留。历史公共信息示意复盘未调用历史 PEER；当前报告不回填为历史分析。

本地 CLI 共 21 次独立进程；本地只读 MCP 共 12 次查询、两个 server 生命周期，库哈希不变，业务 Worker/network 哨兵零调用。与通过既有授权连接进行的真实 PEER 外部研究严格区分。未复用不合格/过期持仓；三例敞口均 `CANNOT_QUANTIFY`、个人上限 `UNKNOWN`，不输出金额或股数。NVDA 既有发行人映射未扩充。

来源日期只有日精度时保留原文日期及 UNKNOWN，不编造日内发布时间；保守 known_at 只代表本轮取得上界，不能冒充最早公开时间。SEC 接收时间另由 filing index 核对。FACT 标签不证明因果；公司解释与厂商表现不升级为独立因果证明。证据时点不够严格时可保存观察性人工判断，但不宣布历史 PIT 回放通过。

## 最小修复与验证

仅修正 [service](../../backend/services/investment_thesis/service.py) 的到期投影：没有 `hypothesis_due_at` 时 `overdue=null`，原先 `false` 会错误暗示未逾期。新增 [真实回忆日期缺口回归](../../tests/test_investment_thesis.py)。不改变模型、权限、MCP 契约、issuer 或金融源。

本轮代码 Gate：全量 `2365 passed / 7 skipped`；E1 定向 `36 passed`；独立 MCP 原协议+E1 `48 passed`；Offline M5 `18/18`；frontend lint/build、工程文档检查及 diff 检查通过。Build 原有大 chunk 提示非失败。无测试失败需要修改无关代码或 clean main 失败对照；远端 CI 未运行（未推送）。

## 交互结论与停止点

当前可用价值是保留原始判断、正反证据和审查限制，避免跌价/回本/partial 报告被当作加仓许可。线上 MCP 未部署 E1，ChatGPT 不会自然语言自动永久保存。当前由 Codex 手工完成来源核查、类型化 JSON、显式 CLI 保存，再由本地只读适配查询；CLI 是开发桥接。

后续只建议在 4.3 集成时评估一个“研究记录待保存预览→用户明确确认→幂等保存”的最小桥接，写权限另行审查，不能拓展原交易权限。复核事件目前仅存为元数据，未启动调度/通知。私人目录中的中文案例报告、RESULT、原件索引、调用及读回证据为本轮完整交付；此仓内文件只记录实现和验收边界。

本轮无 push/merge/deploy、无券商同步/订单、无 B3 启用；S1/S2 保持结项。本地验证不代表上线或投资盈利能力通过。
