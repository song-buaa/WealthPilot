# WealthPilot PoC 03 Stage 1.5 · 仓库整理与 GitHub 发布记录

> 2026-10-11。范围为 MacBook 仓库、文档、GitHub CI/PR/main 与部署准备。没有连接 Mac Mini、迁移真实库、操作真实账户或启动交易。PoC 02 原凭证事故保持 FAIL；PoC 03 Stage 2 仍 BLOCKED。

## 基线和成果完整性

开始时 local main、origin/main、远程 main 均为 `4da23b1868f3e6dae85a15383405aad3b15da5e3`，已 fetch 核对。`v3.15.1` 的实际 commit 为 `5492b1dbead99677e0e84d8a177a78e6e7157036`，是历史 SemVer 稳定版本，后续 main 和 functional milestone Tags 不能忽略。

| 原工作树 | 实际成果 / 本轮保护 |
|---|---|
| `codex/phase1-poc01-private-mcp` | HEAD 在上述 main；Phase 0 / PoC 01 29 个 nonignored 未提交文件保留，不覆盖、不删除。 |
| `codex/phase1-poc02-wealth-query` | HEAD 在上述 main；PoC 01/02 56 个 nonignored 未提交文件保留，不覆盖、不删除。 |
| `codex/phase1-poc03-mac-mini-prep` | 干净 HEAD `56aa7ed90d636dfb5bf2d306a6af3e3fb9c2fd12`；87 个新增文件，覆盖全部前序审计、代码、测试、报告、证据和部署准备。 |
| `codex/tovest-pattern-reuse-audit` | 独立干净审计树，未整合、未修改。 |

新树 `codex/poc03-github-release` 从最新 main 创建。精确 cherry-pick `221cb0f`、`3edef27`、`56aa7ed` 为 `83f800a`、`99806c1`、`6cf77a3`，三次提交职责保留、无 squash；整合后 tree 与 56aa7ed 完全一致，再开展本轮必要改动。其他历史分支、Tags、工作树均不删除、不移动；ignored 数据/配置只统计，不拷入候选。

详细核对：[stage15-preparation.json](../pocs/poc03_mac_mini/evidence/stage15-preparation.json)，包含全部工作树和分支的起始状态、原 85 文件哈希比对、PoC 01 包含 cache 的 32 文件保护及 tree equality。原离线包仍在仓库外保留，不上传 Git。

## 必要文档和部署改动

- README、AGENTS、治理、CHANGELOG 区分历史 Tag、当前 3.x main、4.0 目标及实际 PoC 边界。当前公开 CHANGELOG 移除既有真实验收资产比例；不重写已公开历史。
- 新增 [当前架构](ARCHITECTURE_CURRENT.md)，保留 PEER/Skills/规则计算/用户确认边界，不把财富 MCP 的通过扩大为投资分析或交易已迁移。
- [Stage 2 说明](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md) 改为 GitHub 完整 SHA 主流程，保留一致性导出、独立生产 Key、合成门槛、真实验收、健康与回滚；离线包仅应急。
- `deploy.py install-github` 固定公共仓库，验证 main ancestry、exact-commit 四项 Actions success；下载固定版本/文件名/哈希 ARM 资产，复用原安装器。不修改 current、私有数据/配置或启动服务。
- 原有 65 wheels / 双环境锁保留。其中上游 source-only Tiger SDK 的既有 353 KiB 纯 Python wheel 纳入 Git，摘要与 Stage 1 一致并附 Apache license/provenance；其余 wheel 从 PyPI 固定资产获取，不在 Prod 重新编译源包。
- CI 增加 MCP PoC tests，原 Backend tests / Frontend checks / Offline M5 三项保护 Gate 不变。不修改 Ruleset，不使用 bypass/force push，不加入实际部署任务或真实 Secrets。

## 本机质量记录

| 项目 | 状态 / 证据范围 |
|---|---|
| 全量后端 | PASS，896 passed、7 skipped；显式白名单环境、临时 SQLite、Demo/Mock，非 integration。 |
| 前端 | PASS，npm ci、lint、build；保留大 chunk 非阻断提示，未额外拆包。 |
| Offline M5 | PASS，18/18，public_network_attempts=0。 |
| 读取 + 部署 + GitHub guards | PASS，51/51；17 reader、25 Stage 1 deploy、9 GitHub；临时库/Git、fake API/installer/launchctl，不操作 Mini。 |
| PoC 01 HTTP / PoC 02 HTTP | PASS，11/11 与 9/9，在独立 MCP 环境分别执行。最初联合收集同名 test_http 失败，已改为独立 pytest 进程并复验，未修改工具业务代码。 |
| 公共依赖下载 | PASS，全部 65 wheels 与固定 Tunnel 摘要验证；不是原生 ARM 安装 PASS。 |
| Python compileall | PASS，现有应用/测试与 pocs。 |
| 敏感文件/候选历史 | 新增文件/新增 blob 不含凭证模式、真实库/私钥文件或实际财富 payload；合成 fixture 与 metadata 证据明确区分。当前核心文档清理既有验收比例，历史不重写。模式扫描不能证明不存在所有未知形式秘密。 |

本机结果不是 GitHub CI 结果，不能据此跳过远端四项检查。实际 PR、CI run URLs、发布完整 SHA、main 合并/本地对齐结果在最终交付记录保存；核对 GitHub 和 `git rev-parse main origin/main`，不将本文的起始 main 当作最终发布 Commit。

## 发布与设备验收边界

按现有 strict / linear-history 规则：推送干净开发分支、创建 PR、等待 exact HEAD 四项 CI 通过，确认 remote main 未变化后 fast-forward 发布同一 HEAD 到 main，由 GitHub 识别 PR 合入。没有强推、保护绕过、历史分支删除或新稳定 Tag。GitHub 上的代码发布不等于 4.0 正式生产上线。

设备 Codex 下一阶段只使用实际发布完整 SHA。原生 ARM、网络/SSH/FileVault、源数据一致性迁移、独立最小权限 Key、私人范围、Web/iOS 新调用、MacBook 关闭、进程/重启恢复、无写入/交易任务及备份恢复仍待实际验收。旧 Key 对生产组织范围的影响须核对，新 Key 不自动补救旧事故；真实边界未通过保持合成。
