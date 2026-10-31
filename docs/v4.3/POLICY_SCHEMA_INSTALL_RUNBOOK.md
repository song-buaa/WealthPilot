# 投资政策：一次连续上线 Runbook

P3-C3-A本地候选；**本轮无生产修改授权**。下一次由原Mac mini Codex在一个任务中执行完整流程，只保留部署前批次授权和Stage/Validate后的**一次最终政策确认**。不再为每个命令创建任务、PR、报告或人工审批点。最终交付一份上线报告。

## 1. 已有实现与最小增量

复用P3-B-R2已通过的真实备份副本迁移、Publisher与消费者证据；不重复全部历史验收。新增仅为[独立安装服务](../../backend/services/investment_policy/schema_install.py)、[CLI](../../scripts/install_policy_schema.py)及其测试，复用原[SCHEMA](../../backend/services/investment_policy/store.py)、[DDL权限/摘要](../../backend/services/investment_policy/schema_rehearsal.py)、[共享Owner](../../app/shared_sqlite.py)与[受限PolicyStore](../../backend/services/investment_policy/colocated.py)。

[旧通用Driver](../../scripts/migrate_b0_safety.py)包含持仓纠正/可变stage，缺policy专用权限；此入口仅沿用其事务、PREPARED收据及原子契约更新方式，不改变旧Driver。原离线安装器继续只操作隔离副本。无部署平台、Agent、调度器、MCP写工具或业务配置扩展。

## 2. 一次生产任务的连续执行顺序

1. **批次授权与固定版本部署**：取得用户对本Runbook完整范围的明确授权（部署、备份、停写、Schema及Owner契约更新、Stage/Validate；激活等待下文一次确认）。核对获批合并后完整main SHA/四CI及现役兼容性，沿原install-github、Skills包装器、Tunnel、解释器和scope部署；不降级或改Python依赖，不打断在途PEER、不重放任务。
2. **停写与新备份**：等待在途研究完成；原操作员核实并协调所有共享库客户端和自动重生入口（Web、Backend、MCP、Business Worker、其他写进程），按原机制暂停/关闭旧句柄，保存原恢复命令。用原[Backup API探针](../../scripts/policy_backup_readonly.py)在源写禁止保护下取新的业务/E1两库一致性备份。FK0、integrity及逐表/Schema摘要通过；不同库不声称跨库原子。旧R2证据证明既有兼容性，不代替本次新备份。
3. **Schema与Owner**：运行下节plan/apply。核对固定path/device/inode、属主、scope、WAL和原Schema，事务内只安装原三表九触发器。原契约只更新schema_sha256，其他身份字段不变。验证原业务/E1全部行及Schema对象不变、三表为空、NOT_ACTIVE。新连接通过后恢复原服务/Worker；旧缓存句柄必须关闭重建。此步骤无需再单独向用户逐项确认，操作员以本批次授权填入绑定计划的安装记录。
4. **正式Markdown校验与Stage/Validate**：严格核对下列两份批准原文字节Hash；不修改正文。沿原[CLI](../../backend/services/investment_policy/cli.py)在已核实共库和scope显式Stage、Validate，保存Bundle版本/Hash/结构化Hash/规则摘要。Stage/Validate不是正式审批或激活，仍NOT_ACTIVE。
5. **集中展示并等待一次最终确认**：向用户展示原文两个Hash、Bundle ID/版本/SHA、structured SHA、规则摘要和UNKNOWN、数据库身份/scope、新Schema、实际安装SHA、消费者版本及当前NOT_ACTIVE。明确确认将激活这一确切Bundle；不让用户手工拼JSON、request_id或Hash。用户不确认/取消则停在NOT_ACTIVE，不发布。
6. **一次确认后连续审批、激活、验收**：保留用户当次明确确认原文引用；操作员据此生成原Publisher所需APPROVE_STRUCTURED与ACTIVATE两份机器记录，绑定同一确切Bundle/DB/安装收据和顺序CAS，**不对两条命令再次询问**。沿原[publish_cli](../../backend/services/investment_policy/publish_cli.py)及[Publisher](../../backend/services/investment_policy/formal_publisher.py)连续执行approve、release。不要伪造消费者安装收据或生效时钟。
7. **Web/MCP只读验收与一份报告**：核实原Web纪律/配置/政策读取、Allocation、原PEER使用的PolicySession及MCP均引用同一PolicyReference；不为验收付费运行PEER。原18项工具及E1权限不变，显式政策配置可能增加原已有只读政策工具，按实际名称/schema记录。读回正式发布/原历史保护及LEGACY→ACTIVE结果。失败立即停止，保留实际结果。无券商同步、prepare、WhatIf、订单或真实交易。

正式原文（保留私人目录，不进入Git/CI）：

- 投资理念：`699ab8b70dacb5eeeed79379061a3e401f7a0cca5babe8a42c108cc9292ea625`
- 投资政策：`f7d1c42a97373dc87a1392cefd78ad7e0c720cf43b6c5f4e5b62962f5fe452e4`

## 3. Schema最小操作说明

**封闭WAL备份兼容**：默认源连接仍为`mode=ro`，失败不自动回退。若全部客户端已关闭、自动重生已禁止，且确认没有未合并WAL，可由原操作员在同一停写窗口调用原`snapshot.closed_source_evidence`，显式提供`all_clients_stopped=True`、`automatic_restart_disabled=True`、`no_unmerged_wal=True`及真实`closure_evidence_ref`，将返回证据保存为私人0600文件。证据绑定规范路径、device/inode/uid、大小/时间和完整SHA；仅无任何`-wal/-shm/-journal`（包括空文件/悬空链接）的WAL头数据库合格。证据是操作员关闭证明，不是“没看到sidecar就自动证明封闭”。

仅在原备份探针命令追加`--closed-source-evidence "$PRIVATE_CLOSED_SOURCE_RECEIPT"`。probe与实际Backup API使用同一证据及`mode=ro&immutable=1`；源写/网络禁止沙箱不变，操作前后及发布备份收据前复核源身份/Hash/sidecar。源状态未知或变化即拒绝，不checkpoint、不创建/删除源辅助文件。普通活跃WAL不传此参数，保留原提交WAL快照读取和manifest格式。官方语义见[SQLite immutable](https://www.sqlite.org/uri.html#uriimmutable)及[只读WAL](https://www.sqlite.org/wal.html#read_only_databases)；immutable会跳过锁/变化检测，必须维持操作员停写与禁止重生，不能用于忽略实际WAL。

变量由原个人Mac mini操作员从实际运行配置核实。备份、授权、计划及收据只在0700私人目录/0600单链接文件。工具不加载dotenv、业务lifespan、Provider或启动服务。

```sh
"$EXISTING_PYTHON" -I -B "$VERIFIED_CODE/scripts/install_policy_schema.py" plan \
  --database "$BUSINESS_DB" --contract "$OWNER_CONTRACT" \
  --backup "$NEW_BUSINESS_BACKUP" --e1-database "$E1_DB" \
  --e1-backup "$NEW_E1_BACKUP" --code-sha "$APPROVED_FULL_SHA" \
  --plan "$PRIVATE_HANDOFF/plan.json"
"$EXISTING_PYTHON" -I -B "$VERIFIED_CODE/scripts/install_policy_schema.py" apply \
  --plan "$PRIVATE_HANDOFF/plan.json" \
  --authorization "$PRIVATE_HANDOFF/install-authorization.json" \
  --confirm-authorization-sha256 "$OPERATOR_VERIFIED_AUTH_FILE_SHA256" \
  --receipt-directory "$PRIVATE_HANDOFF/install-receipts"
```

plan不写数据库，实际恢复两份Backup API导出到内存并比较全量摘要；拒绝不匹配/旧备份、未来时钟、FK或原Schema异常。备份旁须有原`*.manifest.json`。apply重新检查现场，BEGIN IMMEDIATE内执行固定DDL/原authorizer。没有日常PolicyStore迁移开关或任意SQL参数。

安装授权是**操作员根据用户批次许可记录的机器输入**，不是新增审批产品：schema=`wp.policy-schema-authorization.v1`、operation=`INSTALL_POLICY_SCHEMA`、decision=`APPROVED`、environment=`PRODUCTION`、human_confirmation=true、approval_id/approved_by/user_authorization_reference、approved_at/expires_at（带时区、最长24小时）、plan_sha256。quiescence记录all_database_clients_stopped=true、active_peer_workers=0、components完整集合（web/backend/mcp/business_worker/other_database_writers）、stop_receipt_sha256、stopped_pids。均由操作员生成和核验，用户无需填写。

文件SHA只防误选，不能认证人类或完整进程停写；旧PID存活检查也不能证明没有其他Writer。SQLite WAL事务不阻断旧Reader，advisory lock仅防本收据目录重复安装；真实进程协调由原发布机制承担。code_sha由操作员核实安装收据后绑定，不假称工具自行认证了GitHub/进程版本。

Schema验证后才沿原配置机制设置`WEALTHPILOT_POLICY_DB_PATH`、`WEALTHPILOT_POLICY_SCOPE`及`WEALTHPILOT_POLICY_SHARED_CONTRACT`。先备份现役配置，在同一批次将Web/Backend、MCP及其Business/Analysis Worker全部绑定**同一共库、scope和契约**，保留原环境白名单转发；受控重建相关进程/连接后，最终用户确认前逐消费者验证NOT_ACTIVE。不得只给操作员CLI设置变量就声称服务已接入；原读取和交易权限不扩大。配置与进程仍沿原机制、不新增服务。以下沿既有CLI执行，request_id、版本与CAS从实际读回生成；stdout可能含私有原文，只定向私人文件：

```sh
"$EXISTING_PYTHON" -m backend.services.investment_policy.cli \
  --db "$BUSINESS_DB" --shared-contract "$OWNER_CONTRACT" stage \
  --scope "$SCOPE" --version "$NEXT_VERSION" --expected-head-version "$CURRENT_VERSION" \
  --philosophy "$APPROVED_PHILOSOPHY" --policy "$APPROVED_POLICY" \
  --approval-manifest "$PRIVATE_SOURCE_HASH_MANIFEST" --request-id "$STAGE_REQUEST_ID" \
  > "$PRIVATE_HANDOFF/staged.json"
"$EXISTING_PYTHON" -m backend.services.investment_policy.cli \
  --db "$BUSINESS_DB" --shared-contract "$OWNER_CONTRACT" validate \
  --scope "$SCOPE" --bundle-id "$BUNDLE_ID" --request-id "$VALIDATE_REQUEST_ID" \
  > "$PRIVATE_HANDOFF/validated.json"
```

source manifest为`{"source_hashes":{"philosophy":"批准理念Hash","policy":"批准政策Hash"}}`，由操作员使用本页固定Hash生成。集中展示后等待用户一次明确确认；以下两个授权文件都引用**这一条确认**，操作分别为APPROVE_STRUCTURED和ACTIVATE，字段遵守[原Publisher契约](INVESTMENT_POLICY_P3B.md#2-正式发布契约)，不修改其安全要求。

```sh
"$EXISTING_PYTHON" -m backend.services.investment_policy.publish_cli \
  --db "$BUSINESS_DB" --shared-contract "$OWNER_CONTRACT" --environment PRODUCTION approve \
  --authorization "$STRUCTURED_APPROVAL" --confirm-authorization-sha256 "$STRUCTURED_APPROVAL_FILE_SHA" \
  --request-id "$APPROVE_REQUEST_ID"
"$EXISTING_PYTHON" -m backend.services.investment_policy.publish_cli \
  --db "$BUSINESS_DB" --shared-contract "$OWNER_CONTRACT" --environment PRODUCTION release \
  --authorization "$ACTIVATE_APPROVAL" --confirm-authorization-sha256 "$ACTIVATE_APPROVAL_FILE_SHA" \
  --installation "$ACTUAL_CONSUMER_INSTALLATION" --request-id "$ACTIVATE_REQUEST_ID"
```

实际Installation须绑定当前完整code SHA及web/backend/peer/allocation/discipline/mcp六消费者的目标Bundle Hash。确认后若Bundle/数据库/版本变化，不得沿用旧确认。最后GET原`/api/discipline/policy-context`与现有MCP政策只读工具并比较PolicyReference，再核对原18项工具；不以health替代这些只读结果。不调用真实分析或交易。

## 4. 明确失败时的最短恢复

| 失败点 | 处理 |
|---|---|
| 备份/plan/权限/身份不符 | 不执行DDL，停止并报告；不关闭FK或放宽源写保护 |
| DDL事务内异常 | 整笔回滚，旧Schema/契约不变；保留prepared，不覆盖数据 |
| DDL提交后契约写失败 | 全部客户端保持停止，原契约fail-closed；不得恢复旧库或丢政策表 |
| 契约恢复 | 对原prepared/plan绑定操作员RESUME_POLICY_SCHEMA记录，增加prepared_sha256；有有效批准时将apply改recover，核验完整数据及精确Schema后仅向前更新契约 |
| 原批准过期的故障恢复 | 保留原plan/备份不倒填时钟；根据仍有效的批次范围记录新的恢复授权。超出批次范围才请求用户，不为每个技术动作重复申请。恢复授权可续期，但原prepared及现场摘要不放宽 |
| 客户端已恢复写入/现场已变 | 拒绝旧恢复计划，不覆盖任何后续合法记录；停止并报告 |
| APPROVE或ACTIVATE失败 | 沿原CAS/幂等读回确定状态，不新request_id盲重放；ACTIVE后如需回滚，须明确目标及用户授权，不能通过清空配置降回旧规则 |

prepared在DDL前durable落盘；DDL COMMIT后契约0600临时文件fsync→原子replace→目录fsync→原Owner verify→completed。重复同plan/原授权读回原收据，无重复对象；成功后政策三表0行。只要还没有Stage/Validate，空Schema不会变成ACTIVE。原业务/E1、订单与政策历史均不可用旧备份覆盖。

## 5. 本轮验证与交付口径

[新增测试](../../tests/test_policy_schema_install.py)只针对安装器、Owner契约、事务/跨提交故障恢复、受限连接和显式CLI；R2 Publisher/多消费者/历史迁移结论继续复用，不重新制造已通过的真实历史验收。对R2冻结备份的隔离副本用同一新入口完成正向、重复和故障恢复，只证明新实现兼容，**不是本轮生产备份或生产安装成功**。

本地报告记录实际测试/独立Review/commit及未执行项。下一次只生成一份端到端上线报告，包含部署SHA、备份/Schema与Owner切换、源Hash、Bundle、用户最终确认、审批/激活与只读验收。无真实交易权限；本轮不Merge、不部署、不安装生产Schema、不创建正式Bundle或激活。
