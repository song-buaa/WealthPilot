# PoC 03 第一阶段：Mac Mini 部署准备

本目录提供部署工具，不启动完整 WealthPilot 后端。PoC 02 的 server/worker/reader 原样复用；生产只有财富只读 MCP 和 Secure MCP Tunnel 两个 launchd job。

完整步骤与第二阶段交接：[Mac Mini 执行说明](../../docs/WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md)。验收状态：[PoC 03 报告](../../docs/WealthPilot_4x_Phase1_PoC03_Report.md)。

| 文件 | 用途 |
|---|---|
| `build_release.py` | 从干净、已提交的 Git HEAD 创建 bundle、源码归档、ARM wheels 和 SHA256SUMS；不带运行数据库/凭证。 |
| `deploy.py` | 本机审查、离线安装双环境、配置、launchd 计划/执行、版本/合成-真实模式切换。 |
| `snapshot.py` | SQLite mode=ro 一致性备份、完整性与表数据摘要、原财富计算的私有金额参考和恢复。 |
| `runner.py` | 固定两个入口，验证 Git/真实访问门槛；有限大小的生命周期元数据日志，丢弃原始子进程输出。 |
| `access_check.py` | 第二阶段认证 metadata、匿名/无效凭证拒绝和关联范围核对，不输出 IDs/Key/API body。 |
| `compatibility.py` | 禁公网、禁完整后端/决策/交易模块导入的临时数据库 smoke。 |
| `validate.py` | 本机实际 MCP 发现/只读调用/契约/迁移金额比对，只输出布尔值和状态。 |
| `locks/`、`pins/` | 两个独立运行环境的完整版本锁及 ARM wheel SHA-256。生产离线、only-binary、require-hashes 安装。 |
| `launchd/*.plist.in` | 可审查的配置样本；由 deploy services 渲染真实绝对路径，不直接复制样本到系统目录。 |

所有生产操作仅在第二阶段运行。`deploy.py` 的可变操作要求 Darwin、原生 arm64 Python 3.11、macOS 13+ 和显式匹配的 hostname；launchctl 操作还需 `--apply` 及 root，job 本身使用指定非 root 用户。hostname 门槛不能自动证明硬件是 Mac Mini，需要设备操作者确认。

`audit` 不改系统；`services` 和 `switch` 默认只生成计划。`prepare`、`install-release`、`configure`、`save-key` 是本机文件操作，执行前须核实机器与路径。`configure` 拒绝重置已有运行配置，`save-key` 拒绝覆盖已有密钥。每个 release 保留原 Git revision，不在设备修改业务代码。

真实模式需要八项实际审阅的证据：个人范围、合成 Web 成功、匿名拒绝、无公网监听、独立生产 Key、最小权限、迁移验证、原生业务 smoke。JSON 是操作门槛，实际认证由 Tunnel 实施。不能从 PoC 02 抄一份 true，也不能把旧凭证展示 FAIL 视为已补救。

第一阶段测试只在 MacBook 临时目录运行，没有连接 Mac Mini、安装 launchd、迁移真实库、创建生产 Key 或启动生产 Tunnel。报告中的 ARM wheel 可用性不代表 ARM 实际执行通过。

## Dev 重新打包

先使用交付记录指定的完整 Git SHA 建立干净 checkout；部署包内的源码和 bundle 已包含该 revision。解出包内 `wheels-arm64.zip`，沿用官方 Tunnel ZIP，不从最新上游替换资产。下面命令使用 Python 3.11，输出目录必须在代码目录外且尚不存在：

```bash
python3.11 pocs/poc03_mac_mini/build_release.py \
  --output /absolute/path/to/fresh-deployment-package \
  --wheels /absolute/path/to/extracted-arm-wheels \
  --tunnel-zip /absolute/path/to/tunnel-client-darwin-arm64.zip
```

定向测试在现有财富开发 Python 环境运行，不需要生产配置：

```bash
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  -q -o addopts='' -p no:cacheprovider \
  pocs/poc03_mac_mini/test_deployment.py pocs/poc02_wealth_mcp/test_reader.py
```

不要在重打包时加入数据库、迁移参考、私人配置或任何 Key。bundle/源码与 package manifest 的 revision 必须一致；完整包摘要从可信交付记录核对。
