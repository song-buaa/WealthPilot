# PoC 03 Tunnel 启动兼容性修复与继续执行记录

日期：2026-10-11。基线为核对一致的本地 main / origin/main / 远端 main：`34bdce4e991c5b79642eaf55c0bc068e0a09d4e1`；独立分支 `codex/poc03-tunnel-config-fix`。原 PoC 01/02 未提交成果、PoC 03 准备及发布工作树保持原样；本轮不连接 Mini、不操作生产目录、数据或服务。

## 1. 设备交接事实与根因

输入为设备 Codex 的 `Dev_Tunnel_Config_Fix_Handoff.md`（2026-10-11T14:16:10Z）。它报告 Mini 原生 ARM 安装、迁移、独立普通 Key、个人范围、认证 metadata / 四项拒绝、本地 synthetic MCP 已有证据；两个 system jobs 已停止，real 未开启。上述为设备交接证据，本轮没有远程复测。

固定官方 v0.0.16（构建 SHA `5f99daabd4aa4a77049e6d81d54a0d8c18335397`）区分 profile 文件与通用 config：

- `--profile-file config/tunnel.json` 在认证前报 `must end with .yaml`，不是 Key 权限失败。
- `doctor --config config/tunnel.json --json` 可以加载同一 JSON；`run --config` 实测能使用 `file:` Key 引用向 loopback 控制面请求固定虚构 Tunnel，证明不仅是 help 文本匹配。
- `--log.file stdout` 将 stdout 当文件名，可能创建 untracked 文件，使下次 Git 校验拒绝启动；改为空值，标准输出由原 wrapper 丢弃。未增加原始日志或 payload 捕获。

现行官方文档介绍 profile 工作流和运行 Key 的 Read + Use 权限；本修复没有按 latest 升级客户端。JSON 兼容结论来自固定二进制行为，不能仅凭官方 help 的 “YAML” 字样推断。[OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

代码证据：`pocs/poc03_mac_mini/runner.py::child_command`、`deploy.py::configure`；全仓相关入口搜索只找到该 runner 使用错误 profile 参数，既有 PoC 02 无需改动。JSON 结构、Key 路径、loopback、固定版本、launchd 结构和财富计算均保留。

## 2. 宽权限普通生产 Key 的验收边界

用户已明确选择宽权限普通 Key。本轮不要求更换凭证，不把 `minimal_permissions_verified=false` 改为 true。

最小例外沿用已有私有 `config/access-evidence.json`，由 `deploy.py accept-runtime-key-risk` 记录个人单成员组织、本人个人 workspace、普通独立运行 Key、owner 明确接受宽权限风险、UTC 时间和当前 Key 的摘要绑定。它只替代最小权限门槛；另外七项仍须严格 boolean true。记录动作不启动 real、不改变原验收字段、数据或 runtime；普通最小权限路径保持原有行为。

`runner.py::verify_real_access` 在 switch 和每次真实 MCP 启动共同检查。缺少接受、非个人声明、Admin/共享用途、缺少或非 boolean 最小权限事实、Key 内容变化、任何其他 Gate 未通过时阻塞。它是操作者声明及防误启用记录，不是自动权限证明或新认证系统；真实访问保护继续由 Tunnel 控制面和私有范围完成。声明不能用于绕过未核对的旧暴露 Key 对新 Tunnel 的访问风险。

生命周期只记 `broad_permissions_owner_exception` 分类，不输出 Key、摘要、私人 ID 或金额。宽权限风险仍存在，最小权限不标为 PASS。旧 PoC 02 凭证事故保持 **FAIL**，未标记已撤销或补救。

## 3. Dev 验证

| 项目 | 结果 | 证据 / 范围 |
|---|---|---|
| 固定 Darwin ARM64 官方 zip 摘要 | PASS | `a160820d45089b5253d8d671fe2a689bde0f73d78c135d1497a7b33f28a3ac62`，与安装锁相同 |
| 真正 v0.0.16 CLI 配置回归 | PASS | `test_tunnel_contract.py` 四项：doctor 配置/引用读取、run 实际 loopback 请求及无 stdout 文件、旧参数拒绝、损坏 JSON 拒绝；仅虚构 Key/ID |
| 只读适配器、部署与发布回归 | PASS | 77 passed（包含四项真实 CLI 测试及 47 项部署测试）；临时库、fake launchctl，未触达生产 |
| 独立 MCP HTTP | PASS | PoC 01：11 passed；PoC 02：9 passed，分两个进程 |
| 后端全量与 compileall | PASS | 896 passed / 7 skipped；显式禁外部 Provider、临时 SQLite，未读取真实数据 |
| 前端 npm ci / lint / build | PASS | 既有 >500kB chunk 非阻断提示仍存在 |
| Offline M5 | PASS | 18/18，公网请求尝试 0；临时库和冻结 fixture |
| Git diff / 历史与敏感资产检查 | PASS | 从最新 main 的干净历史；11 项指定文本文件，无密钥/私钥/个人 ID/DB/二进制资产或业务代码改动，不提交设备交接原文 |
| GitHub 四项 CI / main 发布 | 待发布核对 | 实际 PR/main Commit 与检查 URL 由发布交付记录提供；不预填 PASS |
| Mini Tunnel、readyz、Web/iOS | BLOCKED / 待设备执行 | 本轮 Dev 不连接 Mini，已有失败未用本机结果覆盖 |
| MacBook 关闭、重启/进程恢复、常驻 | BLOCKED / 待设备执行 | 按原 Stage 2 实测，不宣称已生产上线 |
| 最小权限事实 / 旧凭证事故 | false / FAIL | 宽权限接受与旧事故补救明确分开 |

CI 在已有 `MCP PoC tests` 内增加固定 v0.0.16 Linux amd64 二进制测试，官方 zip SHA-256 为 `d60cdba019bce451bcc3a15478cc5b9cb11270b049f5b56ea39a80b517f8b117`；下载校验失败或行为回归会阻止此 Gate。其余三个既有 Gate 保留。Linux/Dev 的二进制测试不替代 Mini 本地连接验证。

重现：设 `TUNNEL_CLIENT_BINARY=/absolute/path/to/pinned-v0.0.16/tunnel-client`，使用既有 business Python 执行 `python -m pytest -q pocs/poc03_mac_mini/test_tunnel_contract.py`。未提供二进制时四项跳过，不能声称兼容 PASS；CI 明确下载并传入，不能跳过。所有 subprocess 使用临时 HOME、空外部环境及 loopback 假服务，无真实 API 授权或账户调用。

## 4. 交付和设备继续执行

发布采用短期分支 → PR → 四项 CI → strict 最新 main / 线性历史合并；不移动 Tag，不删除旧工作树，不强推或绕过保护。具体完整发布 SHA、PR 与 CI 记录在本轮交付结果，设备必须使用该完整 SHA，不能使用旧 34bdce4 或浮动 main。

按 [Stage 2 第 9 节](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md#9-tunnel-v0016-修复后继续执行已有配置数据和-key-保留) 安装新 SHA，复用原 data/config/credentials；不重复 configure/save-key/restore。原生 smoke → 重新 enable 两个既有 system jobs → synthetic switch → local MCP/readyz/listeners/认证拒绝/Web → 记录明确个人宽权限例外 → 其他七项实际 Gate → real 本地及 Web/iOS → MacBook 关闭/恢复验收。

任何访问隔离、来源读取或金额差异仍阻塞 real；来源过期/未知继续 partial。失败先停止或回退 synthetic；回退旧版本不能声称旧 Tunnel 启动问题已解决。生产数据和交易链路不在本轮修改范围。
