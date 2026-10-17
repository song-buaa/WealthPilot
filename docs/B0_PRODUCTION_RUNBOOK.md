# WealthPilot 4.1 B0 生产迁移与回滚 Runbook

状态：开发候选的离线操作说明。**本轮未获生产迁移、启停、部署或真实交易授权；以下步骤均未在生产执行。** 通用发布权限与质量门以 [治理规范](PROJECT_GOVERNANCE.md) 为准；实现与 Schema 清单见 [B0 实现说明](B0_IMPLEMENTATION.md)。

## 1. 发布前独立验收条件

- 固定评审通过的完整 Git SHA，以及该 SHA 的四项 CI；在 ARM 候选环境验证 `ib_async==2.1.0` / `tigeropen==3.5.8`。不原地升级已运行的生产环境。
- 私下盘点 Web/Core/MCP 的实际组件 SHA、启动入口、数据库和契约路径、UID、0600 文件/0700 目录、inode、WAL、Portfolio 和物理索引。当前生产实际状态 UNKNOWN，不用开发机结果替代。
- 确认一个完整券商账户只有一个 canonical Portfolio。订单 broker=ibkr 对应持仓 broker=snowball、source_channel=ibkr、实际同一账户。两套配置、多 Portfolio 归属、旧 run.owner=NULL 或证券身份碰撞均先人工核实，不能按最近一次 run、U 前缀或 Portfolio 1 猜测。
- 私下列出历史 UNKNOWN/SUBMITTED、缺 ref/owner、已确认但无订单关联的旧策略/Batch。新代码拒绝这些不明授权重放。历史 NULL 字段不批量生成 submission_key；缺失真实 ref 的历史请求无法自动恢复，必须券商核实后重新发起独立人工意图。
- 在单独授权的只读验收中验证 Gateway 的非零 clientId、账户过滤、Open/Completed/Executions 可见性、保留窗口、权限和重连。clientId=0 连接可能绑定手工单，B0 拒绝自动连接；不自动改 Master/clientId/Gateway Read-Only。
- 对克隆库先执行本节以下全部流程，包括 DDL 中断恢复、身份订正和 prewrite restore。禁止把 CI 或 Mock 成交视为真实订单验收。

## 2. Schema 与历史数据策略

新增 5 个 nullable 列、1 个新唯一索引、0 张表：

| 表 | 字段 / 索引 | 必要性 |
|---|---|---|
| order_records | portfolio_id INTEGER | 固定认领时的 Portfolio，无 DEFAULT 1 |
| order_records | broker_account_id VARCHAR(100) | 固定实际执行账户，不能用 MCP opaque account_ref |
| order_records | submission_key VARCHAR(64) | 稳定幂等认领键；Batch 键不含确认版本 |
| order_records | submission_payload_json TEXT | 不可变的授权、epoch、量价/TIF、合约、风险与发送期限 |
| position_snapshot_runs | portfolio_id INTEGER | 从 reservation 到成功/失败/质量查询均绑定 Portfolio |
| order_records | uq_order_records_submission_key（非 NULL partial UNIQUE） | 跨进程/重启拒绝同一认领生成第二条记录 |

原 `uq_order_records_batch_submission(batch_id,confirmation_version,batch_leg_id) WHERE batch_id IS NOT NULL` 早已在数据库初始化代码中定义。本次在 ORM 同义声明并校验物理定义；生产缺失时才由显式迁移补齐。错误同名索引、重复键、部分 B0 Schema 或不明变更均阻断，不能用 IF NOT EXISTS 掩盖。

Schema 定义在 [app/b0_schema.py](../app/b0_schema.py)，工具在 [migrate_b0_safety.py](../scripts/migrate_b0_safety.py)。Shared 模式启动只验证，不自动 create_all/ALTER/创建默认 Portfolio，不放松 `app/shared_sqlite.py` 的精确 sqlite_master digest。

历史 PositionSnapshot、AuditLog、已发订单保持原样。仅对当前 Position 进行显式列举的 LSE 订正：实际 Portfolio/broker/account/source + stored conId/currency/ISIN 证据 + 整行指纹 CAS；只改 symbol/ticker，保留 ID、数量、成本及用户字段。CBU3 的 primaryExchange=EBS 仍按已验证 conId 对应 LSE/USD 上市身份。碰撞、不明 owner 或无证据行阻断，不能合并数量/删除一边。同步遇到这种待订正旧行也失败，不能先新建再 stale-delete 旧行。

## 3. 获得生产发布授权后执行

以下变量由操作员显式指定，所有路径位于生产代码外的私有目录；不要把其实际值或财务 payload 写入 PR。

1. 固定候选版本，完成组件/SDK/契约预检。在克隆库完成演练，记录计划、backup manifest 和 receipt。
2. 停止**全部**写入方（完整后端 lifespan 的 scheduler、poller、触发器、Web、restricted writer 及人工脚本）；协调 MCP 读连接。确认没有并发写入。`--writers-stopped` 是操作员断言，工具不负责停服务，也不自动更改配置。
3. 保持原文件 inode，使用 SQLite Backup API（工具复用现有 `snapshot.backup`）生成一致性备份和不可变 manifest。不能复制活动 WAL 文件冒充备份。
4. 创建私有迁移计划，核对旧/目标 digest、DDL 和数据 inventory；身份订正请求文件必须 0600，每项只含 `position_id`、`expected_fingerprint`。指纹通过工具 `row_fingerprint` 的整行规范 JSON 生成，先人工核对证据，不手改目标字段绕过检查。

```bash
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py plan \
  --database "$DATABASE" --contract "$CONTRACT" --plan "$PLAN"
# 需要有证据的身份订正时，在 plan 命令增加 --corrections "$CORRECTIONS"。
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py apply \
  --database "$DATABASE" --contract "$CONTRACT" --plan "$PLAN" \
  --backup "$BEFORE_BACKUP" --receipt "$RECEIPT" --writers-stopped
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py verify \
  --database "$DATABASE" --contract "$CONTRACT"
```

工具在内存克隆上演练相同 DDL/CAS，apply 前重验原契约、整库 inventory 和计划，BEGIN IMMEDIATE 内执行并校验目标 inventory、索引和 integrity_check。完成后原地保存数据库，原子 0600 + fsync 更新**本实例已演练的精确 digest**和 receipt 引用；原契约中的历史迁移 reference 不重写。已迁移的库重新 plan 得到空 DDL；重复 apply 同 receipt 拒绝，不覆盖证据。

5. 若 DDL 已提交但契约未更新：保持所有服务停止。仅在 receipt 的目标 digest/inventory、原文件身份和完整契约均匹配，且从未重新启用 writer 时，显式执行：

```bash
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py resume-contract \
  --database "$DATABASE" --contract "$CONTRACT" --receipt "$RECEIPT" --writers-stopped
```

不明部分状态不自动补 DDL、不签任意新 hash；停止并人工审查。**恢复契约不等于可以启动旧完整后端。**

6. 在任何 writer 启动前先标记不可退回数据的边界：

```bash
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py mark-writers-enabled \
  --database "$DATABASE" --contract "$CONTRACT" --receipt "$RECEIPT"
```

此步骤先于启动，哪怕之后启动失败也保守视为写入可能发生。只启动匹配 Schema 的固定候选；依次验证严格契约、只读数据、来源/账户/Portfolio、一笔请求恢复及 Web 人工授权护栏。真实下单、撤单、同步和持仓写入仍需分别授权。本轮不执行这些步骤。

## 4. 回滚与停止条件

- **writer 从未重新启用且 target inventory 完全未变**：经单独恢复授权，可执行 `restore-prewrite`。工具验证原 backup manifest，先 Backup API 保存当前迁移态，再通过 SQLite Backup API 原地恢复，保持 inode/0600、重设 WAL、校验原 schema/inventory 并恢复原契约。

```bash
PYTHON_DOTENV_DISABLED=1 "$PYTHON" scripts/migrate_b0_safety.py restore-prewrite \
  --database "$DATABASE" --contract "$CONTRACT" --receipt "$RECEIPT" \
  --backup "$PRESERVED_MIGRATED_BACKUP" --writers-stopped
```

- **writer 曾重新启用、存在任何新写入，或事实不明**：禁止旧库覆盖、反向 DROP COLUMN、盲目恢复契约。保留订单认领、成交和审计，停止 mutation，优先 forward fix 或核实兼容的只读降级。数据库回滚不能撤销券商已接单。
- 不把旧完整后端作为安全代码回滚：旧孤儿扫描可能误撤销，旧同步可能跨 Portfolio 删除；关闭实盘开关不能阻止这些本地写入。
- 原 `snapshot.restore` 会替换 inode 并设 0400，不能直接当共享 WAL 的 B0 回滚；不得绕开路径/属主/inode/digest 校验。
- 任一账户多义、索引/Schema 不符、契约变化、备份不一致、UNKNOWN/NOT_FOUND、超出券商历史窗口、SDK/权限未核实或证据冲突：停止相应写入/交易，保留 UNKNOWN/ATTENTION_REQUIRED，人工核实。不得自动补单。
