# WealthPilot PoC 03 · Mac Mini 第二阶段执行说明

> Tunnel 兼容修复更新：MacBook Dev → GitHub PR/CI/main → Mac Mini Prod 拉取完整 Commit 为主要路线。设备交接报告 ARM 安装、数据迁移及本地 synthetic MCP 已通过；Tunnel 启动阻塞，两个 system jobs 已停止，real 尚未启用。继续执行由设备端 Codex 完成，本轮 Dev 不操作 Mini。原离线包仅应急保留；PoC 02 凭证事故仍 FAIL，新 Key 不表示旧 Key 已撤销。代码发布不等于 4.0 生产上线。


2026-10-14 后续状态：用户确认 Mini real MCP 及 ChatGPT Web/iOS 已可查询；本轮 Dev 不复验设备。上述停服/Tunnel 阻塞为历史当轮记录，不得据此停止当前服务或重做迁移。Web & Data Sync Batch 1 交付 opt-in 共享 WAL，默认固定副本不变；生产权限切换、历史参考、备份恢复/回滚必须先阅读 [Batch 1 执行边界](WealthPilot_4x_Web_Data_Sync_Batch1.md)，不要只 chmod 运行库。

## 1. 第二阶段入口与目录

Mac Mini Codex 首先阅读 AGENTS.md、README、PROJECT_GOVERNANCE、本说明和 PoC 03 报告。确认这台机器为目标 Mac Mini、使用哪一个非 root 本机用户及其真实 hostname，再执行；分别记录已有设备证据与本轮重新验收；Dev 回归测试不能替代设备 PASS。

目标：macOS 13+、**原生 arm64 CPython 3.11**。MacBook 现有 x86_64 Python 不适合作为生产解释器。选择可信官方 Python 安装或已有原生解释器，由设备端核实；不要复制 MacBook venv。生产依赖从固定哈希 ARM wheels 安装，不在设备重新解析最新版本或编译源包。

建议在运行用户的目录分离以下内容（脚本支持带空格的绝对路径）：

```text
~/Library/Application Support/WealthPilotProd/
  releases/<git-sha>/code/          Git checkout，原业务代码只读使用
  releases/<git-sha>/envs/{mcp,business}/
  releases/<git-sha>/tunnel/        固定 v0.0.16 官方 ARM binary
  current -> releases/<git-sha>/
  data/wealthpilot.db              迁入的一致性副本，0400
  data/migration-reference.json    金额参考，0600，含私人金额
  backups/                        不可覆盖的源备份和恢复前备份
  config/                         运行参数/范围证明，0600
  credentials/                    独立生产 Tunnel Key、所需 Tiger 私钥，0600
  logs/                           每服务最多 4×1MiB 生命周期元数据
  run/                            私有工具完成记录（需运营期定期归档）
  plists/                         待安装 launchd 配置
  evidence/                       设备侧私有验收记录
```

父目录 0700，配置及凭证不得置于 Git/代码目录。不会自动安装原 FastAPI 后端、Web、IBKR Gateway、同步任务、订单轮询或交易执行。Tiger 只使用既有 `get_prime_assets` 现金查询，是否需要该配置以迁移源参考为准；不默默丢弃现金。Web 展示比对已在 PoC 02 验证，本包不增加生产 Web 服务。

## 2. GitHub 指定 Commit 与设备审查（Mac Mini）

查阅本轮 PR/main/CI 交付记录，使用已合入且四项 CI 成功的完整 SHA。不使用未经发布的 56aa7ed、浮动 main 或 Tag 名代替指定 Commit。仓库目前公开，可 HTTPS 无凭证 clone；若将来变私有，先核对用户已有只读 GitHub 访问方式，不把 Token 写到 URL/参数或配置。

```bash
WP_PY="/absolute/path/to/native/python3.11"
WP_ROOT="$HOME/Library/Application Support/WealthPilotProd"
WP_BOOTSTRAP="$HOME/WealthPilot-PoC03-bootstrap"
WP_REV="REPLACE_WITH_VERIFIED_FULL_MAIN_COMMIT_SHA"
# 新的空 bootstrap 目录，不覆盖已有 checkout 或手工改动
 git clone --no-checkout --branch main https://github.com/song-buaa/WealthPilot.git "$WP_BOOTSTRAP"
 git -C "$WP_BOOTSTRAP" merge-base --is-ancestor "$WP_REV" origin/main
 git -C "$WP_BOOTSTRAP" checkout --detach "$WP_REV"
WP_TOOLS="$WP_BOOTSTRAP/pocs/poc03_mac_mini"
"$WP_PY" -c 'import platform,socket; print(platform.machine(),platform.python_version()); print(socket.gethostname())'
WP_HOST="REPLACE_WITH_ACTUAL_MAC_MINI_HOSTNAME"
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" audit
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" prepare --expected-hostname "$WP_HOST"
```

审查 macOS、剩余磁盘（至少 5GiB）、磁盘故障/备份条件、网络/HTTPS、SSH Remote Login、休眠及断电重启设置。audit 只读观察，部分系统状态需管理员权限；UNKNOWN 必须继续人工核实，不等于通过。由已授权的另一设备测试 SSH，**不要依赖在同一台 Mini 上找到 ssh 客户端作为远程可达证明**。不要自动开放公网端口或修改系统安全设置。

普通用户 LaunchAgent 依赖登录；本方案使用 `/Library/LaunchDaemons` 的 system-domain jobs，通过 `UserName` 以非 root 用户运行，支持解除磁盘锁定后的系统启动。**FileVault 冷启动可能需要现场用户解锁，launchd 无法代替解锁；不能据此承诺无人值守冷重启自动恢复。** 留给第二阶段分别验证进程恢复、解锁后的重启恢复和断电恢复。需要修改休眠/电源策略时由设备操作者审阅，第一阶段不修改这些设置。[Apple launchd 文档](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

## 3. GitHub 安装与原生兼容（Mac Mini）

```bash
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" install-github \
  --revision "$WP_REV" --python "$WP_PY" --expected-hostname "$WP_HOST"
WP_CODE="$WP_ROOT/releases/$WP_REV/code"
WP_BIZ="$WP_ROOT/releases/$WP_REV/envs/business/bin/python"
WP_MCP="$WP_ROOT/releases/$WP_REV/envs/mcp/bin/python"
WP_TOOLS="$WP_CODE/pocs/poc03_mac_mini"
"$WP_BIZ" "$WP_TOOLS/compatibility.py" > "$WP_ROOT/evidence/native-business-smoke.json"
chmod 600 "$WP_ROOT/evidence/native-business-smoke.json"
```

安装失败保留该 release 供诊断，不标记安装完成、不要修改锁文件绕过哈希/架构检查。重试使用新的空部署根或经审阅清理失败的未激活 release；不要删除已运行版本。实际 arm64 import、SQLite/SQLAlchemy、OpenAI SDK 被动导入及 Tiger `get_prime_assets` 方法导入均需通过；smoke 的临时库、禁网络与禁后端/交易模块边界不可取消。此时尚未启动任何生产进程。

install-github 验证 exact-commit 四项 GitHub Actions（Backend tests、Frontend checks、Offline M5、MCP PoC tests）全部完成且 success，并检查 commit 为 origin/main ancestor。API 不可达、限流、检查缺失/未完成/失败均拒绝，不绕过。来源是固定公共仓库。

依赖为两环境原生 ARM 哈希锁：64 个固定文件名/版本/摘要的 PyPI wheel 加 Git 中既有纯 Python Tiger wheel，以及固定摘要官方 Tunnel v0.0.16。首次联网只下载公开资产，不读数据库/运行凭证；私有暂存目录构造同 SHA 临时包复用 Stage 1 安装器，完成后清理。安装不修改 current、私人配置/数据，不启动 jobs。离线包不再是长期主要流程。

## 4. 生成迁移材料（MacBook 源端，第二阶段协调执行）

本阶段没有执行真实库导出。设备部署阶段在 MacBook 用固定 Git 版本的工具生成一致性备份，**不要 cp 在线数据库或只复制主文件漏掉已提交 WAL**。本脚本用 read transaction + SQLite backup 捕获同一快照，验证完整性和全部表行摘要；不会调用 init_db/get_summary，也不会写入源库。[Python SQLite backup](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup)

```bash
WP_SRC_CODE="/absolute/path/to/clean-checkout-of-the-same-published-commit"
WP_SRC_PY="/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python"
WP_EXPORT="$HOME/WealthPilot-private-transfer/poc03-export"
mkdir -p -m 700 "$WP_EXPORT"
"$WP_SRC_PY" "$WP_SRC_CODE/pocs/poc03_mac_mini/snapshot.py" backup \
  --source /Users/songbin/Documents/GitHub/WealthPilot/data/wealthpilot.db \
  --destination "$WP_EXPORT/wealthpilot.db"
# source-config.json：由源端本地配置提取 portfolio_id 及原 Tiger 三字段，0600；不含 Tunnel Key
"$WP_SRC_PY" "$WP_SRC_CODE/pocs/poc03_mac_mini/snapshot.py" reference \
  --database "$WP_EXPORT/wealthpilot.db" --config "$WP_EXPORT/source-config.json" \
  --output "$WP_EXPORT/migration-reference.json"
```

`migration-reference.json` 含真实三项金额及当时查询到的现金参考；仅保留在私有目录、通过可信加密传输到同一用户的 Mac Mini。不要提交、上传聊天或写入普通报告。现金读取失败会阻止生成参考。源库和原备份均保留；检查实际 portfolio_id，沿用财富页核心净资产口径。

使用现有可信加密传输或经用户确认的 SSH，传递三个文件：`wealthpilot.db`、`wealthpilot.db.manifest.json`、`migration-reference.json`。仅按实际需要另外传输 Tiger 私钥至 Mini 的 credentials 目录；不要把源端 `.env` 整份迁过去，也不要传旧 PoC Tunnel Key。生产 Tiger 配置路径改为 Mini 本地私钥绝对路径，并确认账户权限与现金来源；不连接 IBKR 或加载交易适配器。

Mac Mini 上将闭合备份放进私有 `backups/source-import/` 目录；校验后导入：

```bash
WP_IMPORT="$WP_ROOT/backups/source-import"
"$WP_PY" "$WP_TOOLS/snapshot.py" verify --database "$WP_IMPORT/wealthpilot.db" \
  --manifest "$WP_IMPORT/wealthpilot.db.manifest.json"
# 首次没有已启动服务；已有部署时须先按第 7 节停止，证明端口/进程消失
"$WP_PY" "$WP_TOOLS/snapshot.py" restore --backup "$WP_IMPORT/wealthpilot.db" \
  --manifest "$WP_IMPORT/wealthpilot.db.manifest.json" --target "$WP_ROOT/data/wealthpilot.db" \
  --archive-dir "$WP_ROOT/backups" --services-stopped
cp "$WP_IMPORT/migration-reference.json" "$WP_ROOT/data/migration-reference.json"
chmod 600 "$WP_ROOT/data/migration-reference.json"
"$WP_BIZ" "$WP_TOOLS/snapshot.py" check-reference --database "$WP_ROOT/data/wealthpilot.db" \
  --reference "$WP_ROOT/data/migration-reference.json" > "$WP_ROOT/evidence/migration-amount-check.json"
chmod 600 "$WP_ROOT/evidence/migration-amount-check.json"
```

该金额检查重放**同一份迁移现金参考**，复用原财富计算，不重新实现加总；它证明数据库迁移及统计口径一致，不证明 Mini 此刻的实时现金/FX 与源端采集时相同。后面的实际工具查询会使用原现金读取并独立比对；变化必须解释或重新建立经授权的时点参考，不能把差异改成 PASS。`check-reference` 对不一致返回失败且不输出金额。

## 5. 独立凭证与合成私有连接（Mac Mini + 用户账号授权）

默认使用**独立生产普通运行 Key**，只授予 Tunnels Read + Use；不使用 Admin Key，不复用 PoC 02 Key，不把新 Key 发给 Codex/聊天。创建、输入及确认凭证由用户完成，关闭密钥弹窗后再让浏览器工具观察安全页面。创建/修改 Tunnel 范围需要 Read + Manage；运行 Key 不需要 Manage。[OpenAI Docs：Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

优先创建单独的生产 Tunnel，仅关联目标个人组织/个人 ChatGPT workspace，停止 MacBook 旧客户端。不要让两台机器同时轮询同一 Tunnel，以免请求落到开发机。**新 Key/新 Tunnel 不自动撤销旧 Key，也不自动隔离同组织的旧权限**；PoC 02 事故仍 FAIL。设备阶段应核对暴露旧 Key 对生产范围的影响，并记录用户处理决定，不能将新凭证创建写成旧事故已补救。

在 Mini 私有目录准备以下输入，不提供任何实际值到 Git：

- `config/wealth-input.json`：`portfolio_id`（正整数）；必要时 `tiger_cash` 精确三个字段 `TIGER_ID`、`TIGER_ACCOUNT`、`TIGER_PRIVATE_KEY_PATH`。输入不接受任意数据库位置，生产库固定在 data。
- `config/tunnel-id`：生产 Tunnel ID，0600。
- `config/expected-scope.json`：`organization_ids` 与 `workspace_ids` 各为期望的私有 ID 数组，0600，不在报告打印。

```bash
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" save-key --expected-hostname "$WP_HOST"
# 由用户在隐藏提示中输入新生产 Key，脚本拒绝覆盖旧文件
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" configure --revision "$WP_REV" \
  --wealth-input "$WP_ROOT/config/wealth-input.json" --tunnel-id-file "$WP_ROOT/config/tunnel-id" \
  --expected-hostname "$WP_HOST"
"$WP_PY" "$WP_TOOLS/access_check.py" --root "$WP_ROOT" --expected-scope "$WP_ROOT/config/expected-scope.json"
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" services install --expected-hostname "$WP_HOST"
# 审阅生成的两个 plist 和计划；以下是实际系统安装，仅第二阶段执行
sudo "$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" services install --apply --expected-hostname "$WP_HOST"
"$WP_MCP" "$WP_TOOLS/validate.py" --expect synthetic --output "$WP_ROOT/evidence/local-synthetic.json"
curl --fail --silent http://127.0.0.1:8789/readyz
```

Scope probe 使用认证 metadata GET 与四项匿名/无效凭证 GET；不模拟其他人的账号，不能代替用户确认成员范围或最小权限。未通过时停留合成，不开启真实。核实 lsof 只有 127.0.0.1:8788/8789，无公网监听；TLS/证书问题应修正可信 CA，不能关闭验证。实际网络须允许 outbound `api.openai.com:443`、Tiger 和所需 FX 服务；不安装公网反向代理。[OpenAI 网络要求](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

用户在 ChatGPT Web 添加/连接生产 Tunnel 的自定义 MCP，并发起合成 CNY 请求，取得 Mini 的真实完成记录和投递证据。账号连接/授权由用户确认；不要仅凭“已连接”标记工具调用通过。若最小权限未验证，保持 `minimal_permissions_verified=false`。默认阻塞 real；仅个人生产普通 Key 可按下文记录用户明确接受宽权限风险的例外。例外不证明最小权限通过，也不得复用旧 Key、使用 Admin Key 或放宽访问范围。

## 6. 真实模式与端到端验收

全部实际证据完成后，在 Mini 本地建立 `config/access-evidence.json`，0600。字段必须由第二阶段根据真实观察填写，未完成填 false；默认八项均 true 才允许真实模式；个人宽权限例外仅替代其中最小权限门槛，其余七项必须真实为 true：

```json
{
  "access_scope_confirmed": false,
  "synthetic_web_pass": false,
  "anonymous_denied": false,
  "no_public_listener": false,
  "independent_production_credential": false,
  "minimal_permissions_verified": false,
  "migration_verified": false,
  "native_business_smoke_pass": false
}
```

这是操作者防误启用门槛，**不是认证协议**；真实安全边界由 Tunnel 的控制面授权、组织/工作区范围及可信本机环境实施。不要把样例 false 批量改成 true 替代实际验收。这些字段只依据设备实际证据，不依据 Dev 的临时测试填写。

```bash
sudo "$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" switch \
  --revision "$WP_REV" --mode real --apply --expected-hostname "$WP_HOST"
"$WP_MCP" "$WP_TOOLS/validate.py" --expect real \
  --reference "$WP_ROOT/data/migration-reference.json" --output "$WP_ROOT/evidence/local-real.json"
```

真实启动还检查数据库摘要与迁移参考一致、现金配置存在性一致和 Git 无改动；不修改业务代码。仅一个 `get_wealth_overview(currency=CNY)` 工具，返回整数分/scale=2，保留 partial、过期和未知日期。金额差异或来源 unavailable 时验收 FAIL/BLOCKED，先停止 Tunnel/切回合成，再查明来源；不打开同步/交易能力修补数据。

依次验收并保存**不含真实金额/凭证**的摘要证据：

1. Mini 本地真实工具：发现唯一只读工具，三个金额与参考逐分一致，原库无业务写入。
2. ChatGPT Web 新请求：专门时间窗口，Mini `run/completions.jsonl` 的 synthetic=false、partial/ok 和 Tunnel 投递对应；确认三个金额、CNY 分换算及质量提示。
3. 真实 iOS App 新聊天调用生产插件：独立时间窗口，记录实际 App 版本、用户对比结论和 Mini 记录，不用同步旧聊天代替发起请求。
4. 关闭 MacBook 后再从 Web/iOS 发起请求，Mini 出现新完成记录；证明运行代码、数据库、Tiger 私钥和 Tunnel Key 均来自 Mini。关闭 MacBook 前确认不能再通过开发机转发。
5. 用户审阅后仅终止本服务的子进程，等待 launchd 重建 worker-wrapper/child，观察新 PID 和再查询成功；不要使用 pkill python 或停止其他服务。
6. 用户协调实际重启 Mini，解锁 FileVault 后验证 system-domain jobs、Tunnel ready 与远程查询；分别记录有无现场解锁，未验证冷启动不能声称无人值守恢复通过。
7. 本轮数据库与 WAL 字节无变化，log 只有元数据，没有 broker-sync/order-poller/交易入口或完整 FastAPI 后端进程。静态入口限制和临时 smoke 的模块阻断是证据，不能单靠 BROKER_MODE=mock 宣称禁止全部交易。
8. 先停止服务，再在独立恢复副本演练 snapshot restore/verify/check-reference；保留源导出及恢复前备份，只有实际恢复并重新读取通过才写备份恢复 PASS。

将执行记录填写到设备端 `evidence/`；更新验收报告时区分脚本 PASS、设备 PASS 和 BLOCKED。真实数据过期/未知仍显示 partial 是正确行为，本轮不自动刷新持仓。

## 7. 停止、回滚与运行边界

```bash
# 停止顺序：Tunnel → MCP，默认没有 --apply 只出计划
sudo "$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" services stop --apply --expected-hostname "$WP_HOST"
# 代码回滚：选择已安装、经校验的旧完整 SHA；首次部署只有本版本时使用 synthetic 模式
WP_OLD_REV="REPLACE_WITH_PREVIOUS_INSTALLED_FULL_GIT_SHA"
sudo "$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" switch \
  --revision "$WP_OLD_REV" --mode synthetic --apply --expected-hostname "$WP_HOST"
```

代码/模式回滚不自动回退数据，旧版本需保留。`backups/previous-runtime.json` 记录上次 revision/mode。数据恢复先停止两服务，用第 4 节 restore 命令选择经验证的不可变备份；脚本先保留当前库，再原子替换，拒绝未审阅 WAL/SHM/journal 侧文件。不要删除真实侧文件绕过失败。恢复后重新核对 manifest、三项金额和现金参考，再启动/授权真实模式。

若 bootstrap 失败，脚本停止两服务以避免半启动的私有入口。已有相同 label 但属于其他目录/用户/命令的 jobs 会拒绝覆盖和停止。bootstrap 并不证明服务健康；必须继续 readyz 和实际查询验证。launchd 没有 TCP 依赖排序：Tunnel 可在 MCP 暂不可达时重试，恢复行为仍需设备端验证。

日志仅记录启动/退出和 PID，原始子进程输出丢弃；每服务轮转总上限约 4MiB。工具完成记录需运营期定期归档（不含金额），控制磁盘增长。首次生产候选目标是有限只读基础环境，7×24 可用性、网络重连、休眠和备份保存周期须在第二阶段取得实际证据；本阶段不加入调度同步、监控平台或 CI/CD。

优先保留最小权限独立生产凭证；个人宽权限例外只按第 9 节记录，凭证变更由用户完成。任何退回匿名公网、复用旧事故 Key、启用交易、修改业务代码或放宽其他访问范围均超出本包范围。不要把设备阶段的临时修复直接留在 Prod 代码中；必要变更回 Dev 短期分支，再以新的 Git SHA 重新打包。

## 8. 后续更新与离线应急

更新：Dev PR/CI/main 发布新完整 SHA → Mini 用新 SHA bootstrap 的 install-github 安装（旧 current 保持运行）→ 原生 smoke → 审阅 switch --mode synthetic 计划再 --apply → readyz / 本地合成 → 必要范围/数据门槛及真实验收。不重新 configure 覆盖配置。健康失败时停止入口并 switch 上一已安装 SHA，重新验证；无上一版本时保持停止或合成。

原 Stage 1 离线包及交付摘要保留在 MacBook 仓库外。以后可从已通过 CI 的指定 main Commit 用 build_release.py 重建应急包，不加入真实数据/凭证。先核对可信交付记录的包 SHA、完整 revision 和包内 SHA256SUMS，再用包内工具：

```bash
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" install-release \
  --package /absolute/path/to/verified-emergency-package --revision "$WP_REV" \
  --python "$WP_PY" --expected-hostname "$WP_HOST"
```

离线安装不能联网核对 CI，操作者必须保存可信 main/CI 记录并匹配 SHA。原 56aa7ed 包仅为 Stage 1 准备资产，不作为本轮已完成完整发布 Gate 的首次 Prod 候选。应急安装同样不自动开通真实，Stage 2 实测不得省略。


## 9. Tunnel v0.0.16 修复后继续执行（已有配置、数据和 Key 保留）

根因：`--profile-file` 指定 profile YAML，拒绝 `.json` 后缀；`--config` 是通用配置入口。固定 v0.0.16 实测可加载 deploy.py 生成的同一 JSON，并解析 `file:` Key 引用。runner 将该参数改为 `--config`，同时将 `--log.file` 设为空值：v0.0.16 将非空 `stdout` 当文件名，空值才使用标准输出，由现有 wrapper 丢弃，避免创建文件使 Git checkout 变脏。未转换 YAML、升级客户端或增加 OAuth。doctor 的配置子检查 PASS 不表示整体 readyz/OAuth/远程调用 PASS；验收依据实际运行。[官方 Tunnel 指引](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

1. 使用本轮发布的完整 main SHA，在新的干净 bootstrap checkout 按第 2–3 节 `install-github`，保留旧 release、current、data、config 和 credentials；**不再执行 prepare/configure/save-key/restore，也不重新迁移数据库**。安装后更新 `WP_CODE`、`WP_BIZ`、`WP_MCP`、`WP_TOOLS` 至新 release，重跑原生 smoke。先核对已有私有文件及数据库摘要，证据存于 Mini 的 evidence，勿打印内容或金额。
2. 交接指出两个 jobs 已停用。只在 Mini 上、核对 label 属于本部署后执行下列 enable，再审阅 synthetic switch 计划并应用；switch 使用原 runtime 的用户/hostname、保留现有 JSON、数据库和 Key。不要改生产源代码。

```bash
sudo launchctl enable system/com.wealthpilot.readonly-mcp
sudo launchctl enable system/com.wealthpilot.secure-tunnel
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" switch \
  --revision "$WP_REV" --mode synthetic --expected-hostname "$WP_HOST"
sudo "$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" switch \
  --revision "$WP_REV" --mode synthetic --apply --expected-hostname "$WP_HOST"
"$WP_MCP" "$WP_TOOLS/validate.py" --expect synthetic --output "$WP_ROOT/evidence/local-synthetic-after-fix.json"
curl --fail --silent http://127.0.0.1:8789/readyz
```

3. 实测 loopback listeners、认证 metadata 与四项拒绝检查、个人范围、Web 新合成请求和 Mini 完成记录。旧暴露 Key 对新生产 Tunnel 的权限仍须核对；未完成或存在未解决的访问隔离问题，保持 `access_scope_confirmed=false`，停留 synthetic。不能用宽权限例外豁免此项；旧 PoC 02 事故永久保留 FAIL，例外与事故补救无关。不传旧 Key 到 Mini，不让旧客户端轮询生产 Tunnel。
4. 仅当用户已明确选择独立的宽权限**普通**生产 Key、组织只有本人、workspace 为本人个人空间，且本机 `access_scope_confirmed` / `independent_production_credential` 已根据证据为 true，才能记录例外。将原有 `config/access-evidence.json` 的 `minimal_permissions_verified` 保持 false；不要批量改验收字段。以非 root 运行用户执行以下命令，原文件其他字段原样保留：

```bash
"$WP_PY" "$WP_TOOLS/deploy.py" --root "$WP_ROOT" accept-runtime-key-risk \
  --personal-single-user --ordinary-key --accept-broad-permissions --expected-hostname "$WP_HOST"
```

此命令在既有私有 evidence JSON 增加 `personal_runtime_key_exception`：个人/单成员/普通 Key 的明确声明、owner 风险接受、UTC 时间以及当前生产 Key 的 SHA-256 绑定；不显示 Key 或摘要，不启动服务，不替操作者验证 Key 的 Platform 权限。宽权限可能允许更多 API 操作，泄露影响大于 Read + Use；本工具仍只有财富只读入口。声明为人工证据，不是新认证机制。Shared workspace、其他成员组织、Admin Key、缺少明确接受不适用。Key 内容变化时旧例外失效，须重新核对并记录。

5. 其余七项真实通过后，按第 6 节审阅 real switch 再应用。switch 与每次 MCP 启动共同校验例外，生命周期只记录 `broad_permissions_owner_exception`，不改写最小权限事实；失败保持/退回 synthetic。继续真实本地、Web/iOS、MacBook 关闭、进程退出、设备重启与恢复演练。未完成不得宣称 7×24 或 4.0 生产上线。
6. 出现健康失败，先停止两服务，按第 7 节回退已安装版本的 synthetic/停止状态；旧 34bdce4 Tunnel 参数已知失败，不作为可用 Tunnel 恢复证明。回滚不修改数据库；旧版本不识别宽权限例外，会再次阻塞 real，这是预期行为。
