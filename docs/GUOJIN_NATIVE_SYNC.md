# 国金全能行原生文件同步

本 PR 候选；开发基线为 `ff265cb3fcaea229610e91ccb23f64b9835dad34`。代码/隔离验证不代表生产已启用。工程流程仍以 [PROJECT_GOVERNANCE](PROJECT_GOVERNANCE.md) 为准。

## 1. 原入口与边界

- 原 [GuojinSyncService](../backend/services/broker_sync/guojin/sync_service.py) 改为三文件来源，复用 [GuojinAdapter](../backend/services/broker_sync/guojin/adapter.py)、原 SyncExecutor、SnapshotRun claim/lease/fence、Repository、Upsert、FX、Portfolio/Wealth 和 MCP。没有新服务、队列、账户库或持仓库。
- **零 Schema 列、零索引、零表**。批次水位、来源及订正收据使用现有 Run.context_json，原严格 SQLite Digest 不变。
- 已退出 MiniQMT HTTP 拉取，旧 URL/Secret 不再用于国金同步。完整后端 22:00 只保留 Tiger/Futu/snowball 的原任务；本次不启用任何国金 GUI 定时器。manual/all/restricted/显式 cron 仍走原 Executor；国金只消费已准备的原生批次。
- 导出与入库解耦：用户必要时解锁 → 原 Parallels 操作查询/导出 → 生成私有批次收据 → 切换 ready 指针 → 调原同步。以后无人值守导出也只复用此流程，不在服务里保存密码或驱动 GUI。
- 不增加 MCP 写工具或上传页面，不触碰 decision_engine、下单、交易授权及实盘开关。

## 2. 原生格式与业务口径

[解析器](../backend/services/broker_sync/guojin/native_exports.py) 依据实际导出表头：GB18030、TAB 文本；`.xls` 不是 Excel 二进制。只接受已识别列和市场，拒绝缺列、重复身份、非有限数字及错误格式。

| 文件 | 覆盖与验证 |
|---|---|
| stock.txt | `标题：资金股票`、账户资金头、A 股表及保存时间尾；核验股东帐户白名单、市场、数量、成本、市值、参考盈亏。 |
| hgt.xls | `沪HK`，完整沪港通明细、人民币成本/市值/参考盈亏、港币参考成本与报价。 |
| sgt.xls | `深HK`，完整深港通明细；与沪港通的同代码保持独立。 |

三份必须齐备。证券市值之和核对 TXT 的股票市值，证券加资金余额核对总资产，容差为 0.02 CNY；还要与导出时 GUI 总资产及各市场行数一致。资金余额为账户现金；可用/可取/冻结余额不是另一笔资产，不再次加减。零/负资金余额仍沿用原 CASH 合同。

- 港股通持仓键如合成示例 `2015.HGT:HK` / `2015.SGT:HK`，raw_symbol 保留原五位代码。账户/Portfolio/source 不拆成伪子账户。原 symbol 工具在 QNX_NATIVE_POSITION 来源标记和渠道身份均匹配时才投影为 `2015:HK` / HKD 证券身份，不改底层行或交易指令；与其他券商 HKD 报价的同一证券可以合并敞口，估值币种仍为 CNY。
- 所有导出市值、总成本、参考盈亏按 **CNY** 入库，不再次执行 HKD/CNY 换算。业务单价为人民币市值/数量，成本单价为人民币总成本/数量，`price_basis=BROKER_CNY_VALUE_PER_UNIT`；不是实时港币报价。原币港币报价/参考成本、渠道及人民币原始事实保留在原 Snapshot.raw_data_json。
- 券商参考盈亏和成本是独立事实，不强迫满足市值减成本。原聚合器对国金原生行使用已存 CNY 成本，其他来源保持原口径。股票/发行人敞口只合并 CNY 市值，成员行的数量与成本仍独立；不做 ADR 换算或 ETF 穿透。
- HK 文件本身**没有账户号或估值日期**；TXT 的保存时间仅为导出时间。GUI 查询时间、导出时间、行情估值时间分别保留；本来源不具备价格时间证明时返回 partial、SOURCE_VALUATION_TIME_UNKNOWN/PRICE_TIME_UNKNOWN，U1 不授予精确调仓资格，不拿 mtime 当实时行情。

## 3. 私有配置与就绪批次

配置/批次/证据均在仓库外，目录 0700、文件 0600，拒绝 symlink。配置由已有账户映射核实后外置到 `GUOJIN_NATIVE_CONFIG`；不是 HTTP/MCP 参数。示例内容均为合成数据：

```json
{
  "schema": "guojin.native.v1",
  "portfolio_id": 7,
  "account_id": "fixture-owner",
  "inbox": "/absolute/private/guojin/inbox",
  "shareholder_accounts": ["SYNTHETIC-SHAREHOLDER"],
  "legacy_positions": []
}
```

完整 Backend 在原外置环境提供该路径；restricted Web 通过原 `config/web-brokers.json` 显式传入 `GUOJIN_NATIVE_CONFIG`，旧网关凭证不再继承。`GUOJIN_ACCOUNT` 若仍设置，必须与新配置一致；Portfolio 也须与原同步入口一致。冲突/配置无效视为没有可证 Owner，不能取最近账户兜底。其他券商不受国金配置失败影响。

inbox 下每次使用独立子目录（名称仅字母/数字/下划线/横线，最长 80），包含原三文件及 `batch.json`。生成者在三次 GUI 查询时记录实际账户、查询时间、独立行数和核对结果，导出后计算哈希，最后原子写 `ready.json`：

```json
{"batch": "batch_example"}
```

`batch.json` 合同：

```json
{
  "schema": "guojin.native-batch.v1",
  "portfolio_id": 7,
  "account_id": "fixture-owner",
  "account_total_assets_cny": "1490.00",
  "files": {
    "stock": {
      "account_id": "fixture-owner",
      "account_verified_in_gui": true,
      "evidence_ref": "private-query-evidence-reference",
      "queried_at": "2026-10-19T09:40:00+00:00",
      "row_count": 1,
      "sha256": "replace-with-actual-sha256"
    },
    "hgt": {},
    "sgt": {}
  }
}
```

上例 hgt/sgt 省略展示，每项实际必须具备与 stock 相同的字段，不能用空对象通过。行数来自 GUI 完整查询，不是用截断文件自证完整；空仓也必须有账户/零行/资金/总资产证据。跨市场查询窗口最多 15 分钟、查询距读取最多 24 小时、未来时钟最多容忍 60 秒；TXT 保存时间与该股市查询须在 15 分钟内。保存时间以客户端中国时区解释，仅校验批次一致性，仍不作为估值时间。

收据是**已有受信任导出者的来源声明**，不是 XLS 自带的账户证明，也不能防范同一 OS 用户蓄意伪造私有文件。必须先通过真实 GUI/账户映射核验才能标记 true；不得仅因三个文件金额相符就补造账户或查询时间。原件和 GUI 证据留在私人范围，Run 只保存来源指纹、时间、数量、金额及脱敏原因。

发布前在原短写事务内比较同 Owner 最近成功原生批次水位，另一批次必须三个渠道的查询时间都更晚；相同收据可重复发布但不增加持仓行，旧批次不能覆盖新结果。原 claim/fence 继续阻断并发/过期 worker。

## 4. 旧持仓订正

- 完整批次只替换同 Portfolio/guojin/账户/API 范围：旧 HKCONNECT 汇总和已卖出 API 明细退出当前投影；历史 Run/Snapshot 不删不改。
- 同 Portfolio 国金平台上无完整 Owner 的截图、导入或其他来源行，必须逐行确认。未确认就 NATIVE_LEGACY_REVIEW_REQUIRED，整个批次不发布；不会先写新明细再留下重复总额。
- 配置 `legacy_positions` 的每项：`position_id`、原行全字段 `fingerprint`、`action=retire|retain`、私有核实 `evidence`。指纹由原服务 `position_fingerprint()` 生成；任一行已变更则拒绝。已确认同笔重复才 retire；独立手工资产 retain。订正删除与新持仓发布同事务，失败全部回滚。
- 订正收据写原 Run.context_json，仅重用已成功提交的旧行指纹；SQLite 重用旧 integer ID 不构成再次删行授权。新无归属行仍须新判断，不能按 ticker、名称、平台或刚产生的同步历史自动认领。
- 已知其他账户/券商/Portfolio 不修改。现金沿用 U0 碰撞保护，必要时使用原显式 cash_owner 订正，不借此绕过校验。

## 5. 验证与调用

原脚本已替换失效网关探针，不再硬编码真实账号：

```bash
# 仅解析/金额对账；没有账户/查询证明，绝不发布。
python backend/scripts/guojin_sync_e2e.py --files /private/copied-native-files

# 核验私有来源绑定/完整批次，默认不写库。
python backend/scripts/guojin_sync_e2e.py --config /private/native-config.json
```

正式启用后，可调用现有 `/api/broker-sync/trigger`（broker=guojin）并查询原 status；或在已授权环境明确执行原脚本的 `--publish --config ... --database ... --contract ...`。CLI 发布必须匹配原严格共享 SQLite 契约及 Portfolio，且 `WEALTHPILOT_ENABLED_SYNC_BROKERS` 显式包含 guojin；沿原 SyncExecutor 认领，不另开入库路径。返回 Run/status、证券条数、现金、账户总资产、批次/查询时间和质量。Run.position_count 含 CASH 行，与 security_count 分开。

测试入口：[合成原生批次/隔离](../tests/test_guojin_native_sync.py)、[原 restricted](../pocs/poc03_mac_mini/test_restricted_web.py)、[原 U0](../tests/test_u0_sync_safety.py)；浏览器通过原财富/投资页面读取，MCP 保持只读。真实原件不进 Git、公开报告或 CI。

## 6. 生产启用与回滚输入

本轮仅开发/PR，**不合并、不部署、不触发生产同步**。生产后续单独授权：

1. 固定合并 SHA/四项 CI，核对原共享库版本、严格 Digest、Portfolio/账户映射和旧 running/claim；无租约的历史 running 不自动夺取，按原治理核实处理；本功能不迁移 Schema。按原发布流程备份/切换组件，不动交易配置。
2. 只读列出国金 API/汇总/截图/导入/手工现金并逐行核对；必要订正写私有 CAS 决策。不能用测试快照金额代替生产账本。
3. 在固定私有目录准备真实新批次、GUI 来源记录及哈希，先 validate；确认 Owner、三市场/行数、总资产、参考盈亏及价格时间披露。
4. 配置原 allowlist/原生来源，只触发一次国金；核对四类证券明细及独立通道、现金/总资产、旧汇总不重复、其他来源行不变；重复同批次确认幂等。
5. 原 Web、MCP 与新 U1 视图金额一致，旧固定视图不变；缺时间则 partial。真实 ChatGPT 查询仍需独立用户场景验收。
6. 文件缺失/错账户/不平/过期/未知旧行时保留 last-good，检查原 status 的安全 reason_code 后修正输入；不手工伪造成功。

失败先禁用国金的新同步触发，保留当前持仓和历史证据，其他券商继续工作。退回旧代码前须保持 guojin 不在 allowlist 且其旧 22:00 MiniQMT 拉取禁用；不能因回滚代码重新覆盖明细。原始文件/旧行证据用于受控前向订正；若已有新业务事实，不能用旧 DB 备份覆盖。未来按需或定时 GUI 触发方式仍由真实测试后单独决定。
