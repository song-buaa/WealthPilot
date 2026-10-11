# WealthPilot 4.0 · Phase 1 / PoC 03 第一阶段报告

> 2026-10-11。第一阶段部署准备 PASS；Mac Mini 部署和生产验收 BLOCKED，按用户要求留给第二阶段。没有连接或操作 Mac Mini，没有迁移真实库、创建生产凭证或安装系统 launchd job。PoC 02 凭证展示事故仍为 FAIL，不表示已补救。

## 1. 本阶段结论与交付

已提供可从固定 Git revision 构建的离线部署包、最小部署/停止/版本回滚与数据库恢复工具、两份 launchd 模板、完整执行说明和设备侧记录模板。代码仅增加部署工具和文档；PoC 02 的 server、worker、reader 及原业务服务原样复用。

包是**第一阶段完成的生产候选部署材料**，不是完成生产验收的发布。实际设备架构、原生 Python、SSH、网络、数据迁移、私人授权、Web/iOS、MacBook 关闭后的独立运行及重启恢复均未在本阶段验证。第二阶段执行入口：[Mac Mini 部署说明](WealthPilot_4x_Phase1_PoC03_MacMini_Deployment.md)；工具入口：[部署工具 README](../pocs/poc03_mac_mini/README.md)；记录模板：[设备验收模板](../pocs/poc03_mac_mini/evidence/mac-mini-stage2-record.template.json)。

## 2. 基线保护与版本追溯

【已证实】本轮读取 AGENTS、README、PROJECT_GOVERNANCE 并核对本地 main、origin/main 和远程 main，均为 `4da23b1868f3e6dae85a15383405aad3b15da5e3`。稳定版本不从历史文档推断。原 PoC 01、PoC 02 分支 HEAD 均仍在该 main，成果主要是未提交文件；不能把 main 当作已包含 PoC。

| 工作树 | 本轮处理 |
|---|---|
| 原项目 `codex/phase1-poc01-private-mcp` | 29 个 nonignored 未提交文件在仓库外保存，源文件不改动。 |
| PoC 02 `codex/phase1-poc02-wealth-query` | 56 个 nonignored 未提交文件在仓库外保存，源文件不改动。 |
| `codex/tovest-pattern-reuse-audit` | 核对为干净的独立审计树，未修改。 |
| 新 `codex/phase1-poc03-mac-mini-prep` | 从最新 main 创建托管工作树，局部固定前序成果及新增部署工具；仅本地提交。 |

新树路径 `/Users/songbin/.codex/worktrees/poc03-mac-mini-prep/WealthPilot`。仓库外冻结目录为 `/Users/songbin/Documents/WealthPilot-PoC-baselines/2026-10-11/poc03-stage1`，私有权限保存源文件和清单。原 PoC 01 的包含 cache 在内的 32 文件历史清单亦逐项核对一致。进入 Git 的复制排除缓存，不删除源缓存。

前序 Phase 0 / PoC 01/02 冻结提交为 `221cb0f`。最终部署完整 revision 由包内 `release.json`、Git bundle 和交付记录确定；不移动 Tag、合并 main 或 push。原报告中“当轮未提交”的描述作为历史事实保留。清单见 [baseline inventory](../pocs/poc03_mac_mini/evidence/baseline-inventory.json)、[保留核对](../pocs/poc03_mac_mini/evidence/baseline-preservation-check.json)。

【已证实】对导出 Git bundle 的可达历史文件名检查没有 `.env`、`.db`、SQLite、PEM/Key 文件候选。新包只收录 Git 源码与公开依赖资产，不包含真实库、私人金额参考或凭证；文件名检查与模式扫描不等同于完整历史安全审计。

## 3. 部署设计及事实边界

运行结构为两个 system-domain LaunchDaemon：固定只读 MCP entry 与官方 Secure MCP Tunnel client，均通过 `UserName` 用非 root 用户运行。MCP、健康端口只配置 loopback。没有完整 FastAPI 生命周期、订单轮询、券商同步、交易执行、生产 React 服务或额外 OAuth 系统。

| 资产 | 实现 / 边界 |
|---|---|
| 固定代码 | [build_release.py](../pocs/poc03_mac_mini/build_release.py) 只接受干净已提交 Git HEAD，生成 bundle、源码归档、固定 ARM Tunnel ZIP、ARM wheels、manifest 和 SHA256SUMS。 |
| 分离目录与安装 | [deploy.py](../pocs/poc03_mac_mini/deploy.py) 将 release、双 venv、data、config、credentials、logs、backups 分离；离线 hash-locked 安装，拒绝非原生 ARM 3.11、非目标 hostname 及覆盖已有 release/config/key。 |
| 启停/回滚 | services/switch 默认出计划，真实 launchctl 需 --apply；先停 Tunnel 再 MCP，先启 MCP 再 Tunnel；拒绝操作其他部署的同名 jobs，bootstrap 失败停止两服务。代码切换不自动回退数据。 |
| 凭证与真实门槛 | [runner.py](../pocs/poc03_mac_mini/runner.py) 默认为 synthetic；八项真实验收 attestation、数据库迁移 SHA、现金配置存在性及 Git revision 检查通过后才启用真实。attestation 是防误启用门槛，非认证。 |
| 一致性迁移 | [snapshot.py](../pocs/poc03_mac_mini/snapshot.py) 源库 mode=ro + read transaction + SQLite backup；包含已提交 WAL；验证 integrity 和全部表行摘要。恢复保留当前库、原子替换并设 0400，拒绝未审阅侧文件。 |
| 金额口径 | reference/check-reference 复用原 PoC reader 和业务计算；私人参考文件保留迁移时现金读数。此重放证明同快照迁移，不冒充 Mini 此刻实时现金/FX 一致。实际 MCP 比对另行验证，不一致返回 FAIL。 |
| 私有访问检查 | [access_check.py](../pocs/poc03_mac_mini/access_check.py) 第二阶段认证 metadata GET、四项无凭证/无效凭证拒绝与精确关联范围核对；只输出计数及状态，不打印 ID/Key/API body。最小权限需操作者另行核实。 |
| 日志 | 原始子进程 stdout/stderr 丢弃；每服务约 4MiB 上限的生命周期元数据。完成记录仅状态和时间，不含金额；运营阶段需归档，未引入定时任务。 |

【合理推断】经设备验证后，system-domain launchd 可用于用户退出 GUI 后常驻和进程退出恢复。**不能据此认定 FileVault 冷启动、断电、睡眠或网络中断恢复已经通过。** FileVault 启用时可能需要现场解锁；第二阶段必须分别记录。hostname 是操作确认，不是硬件型号证明。

【已证实】MacBook 的 ARM 硬件不意味着现有 Python 原生 ARM：业务 conda 和本轮最小业务环境实际为 x86_64。最小环境本机兼容 smoke 已通过，原生 ARM 执行仍 BLOCKED。

## 4. 第一阶段验证

| 项目 | 状态 | 证据与范围 |
|---|---|---|
| 原工作树/PoC 基线保持 | PASS | [保留核对](../pocs/poc03_mac_mini/evidence/baseline-preservation-check.json)，SHA/分支/HEAD/文件集合与 tracked status 均一致。 |
| 部署/备份/恢复/范围脚本 | PASS | 新增 24/24；使用临时库、fake launchctl 和 fake API，不操作系统 jobs/远程账号。[测试输出](../pocs/poc03_mac_mini/evidence/deployment-reader-tests.txt)。 |
| 原 PoC 02 读取回归 | PASS | 17/17，合计上述测试 41/41；写入拒绝、金额口径及过期/未知/来源失败保留。 |
| 原 PoC 02 MCP HTTP | PASS | [9/9](../pocs/poc03_mac_mini/evidence/mcp-http-tests.txt)；仅本机隔离环境和合成 fixture。 |
| M5 工程纪律 | PASS | [18/18](../pocs/poc03_mac_mini/evidence/m5-offline.md)，离线 public_network_attempts=0，临时数据库。不是完整发布质量 Gate。 |
| 全新最小业务环境 | PASS | [compatibility](../pocs/poc03_mac_mini/evidence/minimal-business-compatibility.json)：Python 3.11.15 x86_64；fixture 金额正确、数据库写入拒绝、无禁用模块导入、禁网络。 |
| 本地 validator 实际调用 | PASS | [smoke](../pocs/poc03_mac_mini/evidence/local-validator-smoke.json)：唯一只读工具/契约/合成 partial。临时 server 已停止。 |
| ARM 安装资产 | PASS | [65 个 wheel 资产](../pocs/poc03_mac_mini/evidence/arm-wheel-assets.json)，MCP 29 / business 46 个精确版本与 ARM hash 锁；[离线跨平台解析](../pocs/poc03_mac_mini/evidence/arm-wheel-resolution.json)通过，不代表 ARM 运行。 |
| launchd 模板/脚本语法 | PASS | [静态验证](../pocs/poc03_mac_mini/evidence/preparation-static-checks.json)，plist lint 和 Python compile；没有真实 bootstrap。 |
| Mac Mini 原生 ARM 安装和读取 | BLOCKED | 未接触目标设备；须第二阶段实际执行。 |
| 真实一致性导出、迁移金额和恢复 | BLOCKED | 本阶段未读取/导出真实源库，当前恢复 PASS 仅属于临时 fixture。 |
| 独立生产 Key/范围/最小权限 | BLOCKED | 未创建/输入任何新凭证或启动生产 Tunnel，账号授权留设备阶段。 |
| Mini 本地、ChatGPT Web/iOS 真实调用 | BLOCKED | PoC 02 的 MacBook 功能 PASS 不能移作 Mac Mini 证据。 |
| MacBook 关闭、进程恢复、设备重启、长时间运行 | BLOCKED | 必须实际设备及新调用证据，不能用模拟命令或 plist 字段代替。 |
| 生产无交易任务/无意外写入 | BLOCKED | 静态固定入口与临时只读测试通过；实际进程、端口及真实主库/WAL 不变仍待设备核实。 |
| PoC 02 密钥展示事故 | FAIL | 原事故与用户拒绝轮换的历史保持；没有将其标记撤销、解决或补救完成。 |

ARM 资产准备中曾遇到未固定 cryptography 的源码/Rust 构建失败（MacBook x86 Python 与 ARM 编译工具链不匹配）；现已采用 PoC 02 已验证的 cryptography 46.0.3 二进制版本，准备和离线哈希解析通过。没有修改系统 Python/编译工具链。tigeropen 3.5.8 上游只提供源包，本轮由官方源包构建纯 Python `py3-none-any` wheel，源 URL/SHA 记录在资产清单；设备安装不会重新编译，ARM SDK 实际 import 仍需验证。

## 5. 第二阶段阻塞项与交接

设备 Codex 按执行说明完成：确认目标硬件/原生解释器 → 离线安装和 smoke → 源端协调一致性导出及私人参考 → Mini 迁入与金额校验 → 用户创建独立生产凭证/个人 Tunnel → 合成私有端到端 → 八项门槛证明 → 真实本地/Web/iOS → MacBook 关闭 → 退出/重启/恢复演练。

新 Key、新 Tunnel **不自动撤销或隔离同组织旧 Key 的权限**。生产访问审核需单独记录旧事故凭证对生产范围的影响及用户处理决定；不能仅因新 Key 已创建就将边界审核或旧事故改成 PASS。若任一真实访问、原生依赖、现金来源或金额检查失败，停留/退回合成并保留 BLOCKED/FAIL，禁止启用同步或交易链路修补数据。

第二阶段记录须保存时间、完整 revision、实际设备观察、工具完成时间窗口、比较布尔结论和恢复条件，不保存真实金额/账户/凭证。至少覆盖模板全部验收项；FileVault 解锁和网络重连应分别填写。保持原代码 revision 的 clean checkout，任何必要代码修复回 MacBook Dev 分支再重新打包，不直接改 Prod。

本轮没有引入投资分析 Skills、交易执行或复盘功能。后续范围限定为完成本包设备验收和运维条件，不提前推进其他架构重构。
