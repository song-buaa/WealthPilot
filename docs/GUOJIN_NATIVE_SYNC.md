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

## 7. 每日/按需原生导出编排（开发候选，尚未启用）

### 定向 Gap 与权威边界

本批从 main `9387e6d3333008be4b28af4dab1901924141277a` 开发；生产版本由用户确认，本轮不探测生产。风险 L3（未来操作真实券商 GUI/发布持仓），本轮只做隔离开发和 PR。

| 分类 | 现有入口/证据 | 本批最小连接 |
|---|---|---|
| EXISTING | 原 `native_exports.py` / GuojinSyncService / Adapter / Repository / PositionUpsertService | 三文件解析、人民币金额对账、Owner、旧行订正、claim/fence 和 Last-good 完全复用 |
| EXISTING | `backend/main.py` 22:00 同步 | 国金旧 MiniQMT 已退出；Tiger/Futu/snowball 不变，不把 GUI 工作塞进 Backend 生命周期 |
| EXISTING | Mini 既有 Codex 单次任务和 `work/win_ps.py` | 复用 `prlctl exec <VM> --current-user powershell.exe -NoProfile`；正式启动器使用私有 BOM PS1 的 `-File`，不新建 scheduler/服务 |
| EXTEND | [daily_sync](../backend/services/broker_sync/guojin/daily_sync.py) / [CLI](../backend/scripts/guojin_daily_sync.py) | 定时/按需共用一次有界执行、私有状态、跨进程锁、重启续接已封存批次 |
| EXTEND | [Windows 原生适配层](../backend/services/broker_sync/guojin/native_export.ps1) | 当前会话/窗口/账户门、user32 原生输入、来源时间/独立行数/完整查询证据、原生保存、私有截图及哈希 |
| EXTEND | 原 `load_batch(config, candidate=...)` | 候选验证不切换 ready；通过后同文件系统 fsync + replace 原子切换，再进原 CLI/Executor |
| FIX | 原发布 CLI 的 `--reuse-published` | 丢失发布响应后，仅复用同 Owner/同 batch 指纹且当前持仓仍匹配的最近成功 Run，不重复发布；事实变更不能假称已恢复 |

不新增 Schema、Digest、依赖、账户库、收益/现金计算、持仓状态机、通用任务队列或自动登录。`decision_engine`、交易路由及其他券商不变。

### 已证实与真实待验收

只读读取 Mini 聊天记录 `国金 QMT 接入` / `4.1 部署` 后确认：2026-10-20 原生 user32 刷新确实将查询时间从 17:49:37 改为 19:46:35，但返回 **120022**，所以时间更新不等于成功。该次宽度是 guest 1920 / Mac 图像 3840，固定 HWND 和截图坐标不能重用。2026-10-21 06:44 准备记录仅证明交互会话/既有登录，完整新三文件为 NOT EXECUTED，17:30 单次验收仍由 Mini 原任务负责。

本实现不复制历史坐标、账户、资产金额、成功标志。GUI profile 必须由真实验收记录填写：每个导航/刷新/导出点的当前窗口尺寸、前置页面正则；query/rows/success/total 的唯一 UIA 控件身份；原生保存对话框及“否”按钮身份。**若无法读取独立完整行数或独立查询成功指示，保持 `AUTO_GUI_CONTRACT_UNVERIFIED`，不得拿导出行数、刷新时间、文件 mtime 或无报错代替证据。** 这类 profile 的真实可用性和 Windows 实际操作仍须在 Mini 验收，不由 Mock 或 PowerShell 解析器 PASS 证明。

### 私有配置与 Windows 合同

原 `guojin.native.v1` 配置增加可选 `automation`；原消费者忽略此键，无配置或 `enabled != true` 均不启动 GUI。全部保留仓库外，原 inbox 0700、profile/config/status 0600。示例仅合成路径：

```json
{
  "automation": {
    "schema": "guojin.automation.v1",
    "enabled": false,
    "vm": "Windows 11",
    "prlctl": "/usr/local/bin/prlctl",
    "guest_root": "D:\\private-guojin-export",
    "shared_root": "/absolute/existing-shared/guojin-export",
    "profile": "/absolute/private/verified-gui-profile.json"
  }
}
```

`guest_root` 与 `shared_root` 必须是既有 Parallels 共享映射的同一位置；不安装文件网关。每次随机 batch 名，在 guest 新建独立目录，已存在则拒绝。导出先落共享候选；Mac 复制至私有 inbox/batch（文件 0600）并校验，再由原解析器读取。原件和 screenshot 只在私人范围；公共 stdout 不输出账户、路径、原始 SDK 错误或财务 payload。Windows worker 压缩传输至私有临时 PS1（UTF-8 BOM 兼容 Windows PowerShell），guest parent 完成/超时后清理临时脚本及输出；不安装额外程序。

Profile `schema=guojin.gui-profile.v1`，`verified_readonly=true` 和 `evidence_ref` 仅在真实步骤复核后设置。它是国金特定的声明式 GUI 合同，不接受 PowerShell/命令/通用键盘宏，不是 RPA 框架。必要字段：

- `window_pattern`、`account_pattern`（命名捕获 `account`，与原私有 Owner 精确匹配）、`width/height`（Windows guest 当前坐标系）。窗口按当前 xiadan PID/会话/可见标题枚举，不能复用隐藏 MainWindowHandle 或历史 HWND。
- `markets` 恰好为 stock/hgt/sgt。各项 `open[]`、`refresh`、`export[]` 只存已核验的只读点击：`x/y` 相对窗口坐标、`expect` 必须匹配当前页面、可选 `button=right`。每个点击还必须有 `target={"id":"真实AutomationId","class":"真实ClassName","name":"真实控件名称"}`，来自真实只读控件证据；缺失时不操作。移动必须成功、GetCursorPos 实际坐标必须一致，点击前重新核对窗口/Owner/页面及 UIA FromPoint 命中的目标身份、可见/启用和所属进程；失败/裁剪/焦点变化/命中不符均不发送鼠标事件。不允许密码或任意键盘输入。
- 各项 `page_pattern`、`query`、`rows`、`success`，stock 另有 `total`。读数 selector 为 `id`（AutomationId）+ `class`（ClassName）+ `pattern`（命名捕获 `value`），必须唯一且可见。query 为中国时区 `yyyy-MM-dd HH:mm:ss`；rows 是完整结果数；total 是独立完整账户总资产；success 必须是本次查询成功的独立来源证据并捕获为 `SUCCESS`。不能选择固定标签冒充本次完成结果。
- `save_title` 精确原生保存窗口名、`filename`（唯一 id/class）、`save_button`（id/class/name）；仅文件名 Edit ValuePattern 可输入当前导出路径，保存前重读确认。`excel_prompt_title` 与 `excel_no`（id/class/name）仅匹配已观察的“是否打开 Excel”提示并选否。未知弹窗/登录/柜台错误停止。

Windows 输出 `observations.json`（`guojin.gui-observations.v1`）包含 batch、status、独立 `account_total_assets_cny`；三 market 各有 account_id、complete、query_before、queried_at、row_count、exported_at、文件 sha256、evidence_sha256。配套 stock.txt/hgt.xls/sgt.xls 与 stock.png/hgt.png/sgt.png。不得在 GUI 缺证据时自行补 complete/账户或时间。脚本先检测实际刷新，再保存真实页面截图；原生文件时间不升级为估值时间。Mac 校验来源 batch、实际起止区间、三市场身份、行数和文件/截图哈希，再生成原 `batch.json`，调用原 `load_batch` 验证全部会计口径。

### 一次执行、互斥与恢复

在未来另行获准的生产环境，同一个 CLI 由原 Codex 定时任务或用户按需调用：

```bash
# 定时：未来获准后由原Mini机制在11:45、17:30调用同一命令；本PR不修改/启用任何任务。
python backend/scripts/guojin_daily_sync.py --trigger cron --publish \
  --config /private/native-config.json --database /private/wealthpilot.db --contract /private/database-contract.json

# 按需：用户要求“同步国金账户”，完全相同管道。
python backend/scripts/guojin_daily_sync.py --trigger manual --publish \
  --config /private/native-config.json --database /private/wealthpilot.db --contract /private/database-contract.json
```

仍需原 `WEALTHPILOT_ENABLED_SYNC_BROKERS` 显式含 guojin。严格 Digest/文件身份/Portfolio 合同由原发布入口核验，不执行 DDL。CLI 不启动 Backend lifespan、不连接其他券商、不提供任何交易动作。默认不加 `--publish` 时只生成/验证候选（VALIDATED），不切换 ready、不访问数据库；生产前真实 GUI 只读验收使用这一默认模式，确认授权后才加 `--publish`。

- cron 使用原 D1 exchange-calendars 的 XSHG/XHKG，至少一市场当日开市才执行；超出日历范围 fail closed，不按周一至周五猜节假日。北京时间 **11:45–12:15（midday）**、**17:30–18:00（post_close）** 两个独立窗口内执行，起点包含、终点排除，按已成功发布的对应来源窗口持久化去重；机器/应用恢复后在当前窗口重新调用可续接，错过窗口跳过，下一窗口/交易日再运行，不是常驻补偿队列。按需仍为同CLI，交易日09:00–18:00；两种触发均沿原日历，非交易日不操作GUI。120022、登录失效或来源不完整停止当前尝试，不在清算窗口循环重试。
- Mac inbox 内内核 flock，Windows 同会话 named mutex，双层互斥。锁不删除/换 inode；Mac 崩溃/重启释放内核锁。被放弃的 Windows mutex 可恢复。VM 仅 start/resume 已配置 VM，不重启客户端，不解锁/登录、不改变设置。
- guest worker 单次 deadline 600 秒，guest parent 610 秒强制结束**自己创建的 exporter child**，Mac transport 640 秒；不会终止 xiadan、共享券商任务或其他进程。VM 启动/状态查询另有 60/15 秒上限。原同步发布 child 90 秒上限；事务回滚/过期 claim 沿原 Executor 恢复。未提供后台无限续跑或无限重试。
- `automation-status.json` 保存 RUNNING/SUCCESS/FAILED、trigger/batch/起止时间、安全 reason_code、最后成功时间/日；成功有 run_id、证券数和账户总资产。每个候选目录另留每次尝试独立的私有 run_<attempt_id>.json 结果，重试不会抹去前次失败记录；公共日志仅安全字段。不是资产权威或第二个持仓库。
- crash 时保留候选。自动恢复只允许三市场原始 `queried_at` 均在 15 分钟内（沿用原批次跨市场15分钟窗口作为最小有界恢复上限；保留原60秒时钟偏差），且仍为同日；cron及命名窗口内的manual恢复还要求三市场来源均位于当前窗口；不能把窗口前/午间来源复用于另一窗口。年龄不取 started_at，也不因重启/重新封存重置。超龄必须新建批次重新查询；新导出失败不发布旧来源，Last-good 不变。近期同步失败/ready切换后崩溃/响应丢失仍复核封存批次。显式 `--retry-batch <原名称>` 是历史来源重放，不是一次新刷新（原24小时解析容限不变）；结果及CLI输出保留 `source_mode=explicit_retry`、原 `queried_at` 和来源年龄，不复制旧证据进新批次。半成品新建独立批次重取，不认领部分文件。
- 两个完成收据保存在原私有状态 `completed_windows.midday` / `completed_windows.post_close`。每项含 `window`、三市场原 `queried_at`、`published_at`、`native_batch_id`、原 `run_id`；仅来源和成功发布时间均属于同一交易日/同一窗口且发布时来源未超15分钟才成立。11:50合格manual可满足午间cron，但不能消耗下午资格；17:35合格manual同理。重复cron返回 `SKIPPED/ALREADY_PUBLISHED_WINDOW` 并给出 `schedule_window`，不会查询/发布。重启读取同一收据；仅保留两个窗口最近一次成功，不无限增长历史状态，历次批次/Run证据仍保留。下午失败不覆盖午间完成收据/Last-good。旧PR #32单个 `completed_window` 仅兼容为post_close证明，不推断午间完成；新成功写入对应map槽，`last_success_day`仅作历史展示。
- sealed 候选验证通过才原子 replace ready；新批次三个渠道时间必须全都更晚，不能倒退 ready。随后原 Executor 发布，DB 再做 Owner/claim/fence/旧持仓 CAS。同步失败可能留下有效 ready，当前 Last-good 持仓不变；安全重试同一 ready。已成功且事实仍匹配时原 CLI 返回 reused，不再次写 Run/持仓；事实变更则不假称复用成功。

### 生产启用、17:30 合同接入与回滚

本轮不合并、不部署、不修改正式 profile/ready/config、不执行真实同步、不启用每日任务，也不改变现有 2026-10-21 17:30 单次任务。

生产前只需：固定获准合并 SHA/CI；保留原服务/Owner/严格 Digest/权限/实盘开关；将 17:30 报告的真实窗口、独立成功/行数控件、保存对话框、共享路径填入私有 profile。先在独立 inbox 做同一 CLI 和真实 Windows 只读导出/原验证器验收（不加 --publish，不切换正式 ready/写库）；差异只修原 `native_export.ps1` 对应适配/私有 profile，不重写解析/同步。若独立成功或完整行数尚无法取得，应明确阻断正式启用，不填造证据。没有新增数据库迁移、依赖安装或服务。

获得生产持仓写入授权后，按第 6 节完成 Owner/旧行只读核对、备份/单次发布及 Web/MCP/新 U1 视图一致性，旧固定视图保持不变；再单独授权将**原 Mini 定时机制**改为交易日11:45和17:30调用同一CLI。Mac 重启后 Codex/原任务必须恢复，只在11:45–12:15或17:30–18:00当前窗口内调用恢复，应用未运行不伪称可调度；生产需验证这一设备条件。按需和定时不能分别维护两套脚本。

回滚先停原每日触发、设 automation.enabled=false，保留既有 ready/原件/运行收据及 Last-good；恢复已确认旧组件 SHA。无 DDL/Digest回滚，不用旧数据库覆盖新事实，不重新启用 MiniQMT/22:00国金拉取；Tiger/IBKR及原交易授权不变。

回归入口：[编排/故障注入](../tests/test_guojin_daily_sync.py)、[原国金安全回归](../tests/test_guojin_native_sync.py)、[PowerShell实际函数Fake安全断言](../tests/test_guojin_native_input.ps1)（含移动失败/坐标裁剪/焦点变化/命中不符，CI沿原Backend job执行，无真实GUI）。Windows 的真实控件/锁屏/导出可用性、实际交易日柜台窗口及每天长期稳定性为 production-runtime-pending，不能用隔离 fixture 或 CI 代替。


### PR #32 定向修订验收边界

R01/R02/I01仅修改来源恢复、原生点击保护及调度去重。Registry入口/职责未变，无需另增能力条目；本文件更新具体合同。无Schema、Digest、项目依赖或交易权限变更。原私有profile必须补齐每个只读点击的target控件身份，不接受旧坐标配置自动升级。Windows真实AutomationId/ClassName/名称及UIA命中可用性仍须Mini原17:30验收报告证明；如控件无可信身份则保持AUTO_GUI_CONTRACT_UNVERIFIED，不能靠Fake或填造标识放行。不修改、不重复安排原单次任务。

### owned 原生窗口定位

真实 Mini 只读调查发现：可见 MFC 交易窗口可由另一进程的窗口拥有，因而未出现在 UIA 根节点直接子窗口中；`xiadan.MainWindowHandle` 也可能指向零尺寸隐藏 IE 辅助窗口。定位改为每次 `EnumWindows` 核实当前 xiadan PID、可见性、非零窗口矩形及已验证标题，再以 `UIA.FromHandle` 读取该确切 HWND，并重新校验 UIA PID/HWND、可见性、标题和 Win32 进程归属。候选不唯一或 UIA 无法证实时返回 `AUTO_GUI_CONTRACT_UNVERIFIED`，不据此宣称登录失效，也不接受 owner 进程的 UIA peer。

此修复只改变窗口发现，不放宽点击目标/FromPoint、独立柜台 SUCCESS、完整行数、来源时间、保存对话框或原严格对账。没有可信 profile 时仍不发送输入、不导出、不发布。Registry 入口和职责未变，无需更新。

现场另发现 Windows PowerShell 的有效执行策略为 `Restricted`，原 `-File` 临时导出脚本被系统以 `UnauthorizedAccess` 拒绝。真实只读 Win32 诊断使用已有交互命令及已有 Windows Python 的 ctypes，未改变策略；它不代表正式 PS1 worker 获准运行。正式启用前须有符合现有系统策略的受信任脚本执行方案；本实现不设置执行策略、不使用 Bypass/Unrestricted、不通过 IEX 等方式绕过脚本限制。真实 GUI 三文件及 SUCCESS/行数合同尚未通过时，保持 runtime-pending，不用窗口发现或隔离测试替代来源验收。

### owned 保存对话框及真实合同边界

后续 Mini 验收在明确用户批准后，仅将 Windows `CurrentUser` 从 `Undefined` 改为 `RemoteSigned`，其他 scope/组策略不变；本地无券商操作的 `.ps1 -File` 探针启动并正常退出。该批准不是安装器自动调整策略的许可。独立隐藏进程中的有界 MTA 只读诊断能读取实际控件；交互 `prlctl` 启动的终端曾遮挡 FromPoint 目标，前台或命中不符必须继续拒绝。

实际“另存为”是当前交易窗拥有的 `#32770` 窗口，同样可能不在 UIA 根直接子节点中。保存路径改用原 native 窗口发现，严格匹配配置的字面标题、唯一 HWND、相同 PID/UIA 身份、可见性及当前交易窗的原生 owner，再读写文件名；不持久化现场 PID/HWND。真实诊断已证明文件名 ValuePattern 读回和取消有效，未执行保存或导出。这不代表其他错误/Excel 提示窗口、完整 GUI 流程已经验收。

真实持仓表为自绘控件，现场未提供 GridPattern/完整行数；状态栏的 GridPattern 行数只是状态栏布局，不能作为持仓数。“完成/已连接”、查询时间及日志中的网页 DOM ready 均不能充当独立柜台 SUCCESS。尚无完整来源合同，不生成 verified profile/批次，不刷新、不导出、不更新 ready 或持仓。此修订只修复已证明的保存窗发现，Registry 入口、数据 owner、解析/对账、Schema、依赖及交易权限无变化，无需更新 Registry。

### 双次调度候选与Mini启用输入

本次仅在原daily_sync/CLI扩展两个窗口及原私有JSON收据，无新scheduler/GUI平台/服务/表/Schema/Digest/依赖；同一flock、Windows mutex、ready、Owner、原严格验证器和SyncExecutor不变。生产SHA由用户报告为e25fd8f，本轮不探测生产、不修改原配置或任何定时任务。

未来固定PR合并SHA部署获准后，沿原Mini定时机制在北京时间11:45及17:30各触发一次原CLI（命令相同，窗口由源时间判定，不新增强制窗口参数）。启用前确认真实GUI合同、Owner及共享路径；先独立inbox默认validate，再另获持仓写入/每日任务启用授权。窗口边界内错过时只做原有有界恢复，不新增后台无限补偿。回滚先停新触发/automation.enabled=false，保留ready、两个窗口收据及Last-good；无数据库回滚/DDL，不恢复MiniQMT。

用户报告UIA当前无法识别xiadan可见窗口：该问题保持真实运行PENDING，不因调度修改消失。本批不改native_export.ps1、不伪造目标id/class/name、不关闭任何鼠标或账户检查。真实GUI合同须由Mini独立提供证据后另做必要适配；不等待、不取消或干扰原2026-10-21 17:30单次验收。

### 最终上线政策：估值 WARNING 与可观察来源（当前合同）

本节按用户最终上线授权更新前文的金额容限和来源要求；旧v1收据继续读取，但不得把旧GUI完整性要求或旧两分估值容限当成新v2来源已具备的证据。

- 原明细与资金页独立总资产精确一致为PASS；差异同时满足独立总资产的0.5%及2,000 CNY上限为WARNING，超限仍NATIVE_RECONCILIATION_FAILED。比较使用未舍入Decimal，两个上限同时包含边界；总资产非正时不能以比例放行非零差异。资金页自身“股票市值+资金余额=总资产”仍保留两分原生舍入校验。
- 原始证券行、人民币市值/成本/盈亏、资金余额和资金页总资产均不改动；`reconciliation`记录明细合计、独立总资产、带符号差额、绝对差额、比例及政策版本。WARNING只属于估值金额；错误Owner、证券/通道、文件/哈希、时间或证据仍失败。现金不补平，可用/冻结余额不再扣除。
- 新`guojin.gui-profile.v2`复用原worker及PR34/35窗口/owned保存窗保护，使用已核实的只读导航/刷新/导出动作。A股先打开已验证的原生菜单，再只允许唯一`Item 32793 / 保存 / ControlType.MenuItem`进入TXT保存路径；其他菜单项、外来PID、模糊目标均拒绝。前台、FromPoint、鼠标实际坐标、原生HWND/PID及DPI检查不放宽。
- 自绘页面可用Mini已验证的Windows.Media.Ocr仅核对可见账户/股东身份和页面；OCR不是金额、柜台SUCCESS、完整行数或查询完成证明。Owner仍来自原配置及其明确股东allowlist，已识别出不符资金账户时不允许再用股东匹配绕过。OCR输出和截图只在私有批次目录，不进入公共日志。
- v2 profile显式`complete=false`、`counter_status=UNKNOWN`、`source_time_quality=UNKNOWN`，不再要求不存在的query/rows/SUCCESS字段。`guojin.gui-observations.v2`和`guojin.native-batch.v2`保留`queried_at=null`、真实`query_action_at/observed_at/exported_at`、HWND/PID/DPI/页面/账户/只读动作证明及文件/截图哈希。行数明确为EXPORTED_ROWS，由原严格解析器计算，不用导出行数自证账户完整。资金页总资产取原TXT资金头，不伪造GUI数值控件。
- 查询动作时间只用于批次排序/15分钟恢复/双窗口去重，不冒充券商查询、持仓、行情或现金估值时间。旧24小时导入容限不变。完成收据继续保存两槽，并明确`publication_mode=observed_rows`、`complete=false`和原动作时钟；它只证明本窗口已发布可观察事实，不证明完整账户刷新。
- 未证明完整性的v2批次只更新已观察到的正数量/正金额事实，仍走原SyncExecutor、Run、claim/fence、Upsert短事务。没有观察到的证券、零值证券/现金和既有Last-good记录保持；不删除旧持仓，不执行待退役旧汇总的删除决策，不把不完整来源替换成完整账户。没有任何可更新正事实时拒绝空发布。Run保留`complete=false`及SOURCE_COMPLETENESS_UNKNOWN等partial质量，U1不能据此授予完整数据资格。
- 唯一必要通用入口调整是原Repository增加默认关闭的`observed_rows_only`参数，只允许国金原生来源配合原显式reconcile使用；其他券商不启用且仍拒绝不完整来源。没有新表、Schema、Digest、依赖、同步引擎、持仓/现金算法或任务平台。
- 正式PS1仍用既有guest parent/child看门狗和当前执行策略，不设置Bypass。为避免扩大原生命令行，压缩worker通过已有Parallels共享目录的私有临时文件传送，guest核对SHA-256后写本地UTF-8 BOM PS1并执行；原有有界超时、仅清理自身child和临时文件不变。无需文件传输服务。

部署与启用：先固定合并SHA及四项CI，沿原Runbook停写/一致性备份/安装并统一相关组件，无DDL；保留Owner、allowlist、ready、Last-good及交易权限。先在独立inbox运行原CLI默认validate取得真实三文件/身份/差额/partial证据。展示真实证据与精确执行计划后，分别取得生产持仓写入和双次任务启用确认，再进行原受控同步和Web/MCP/固定视图/幂等验收。确认后才启用原机制11:45、17:30两个触发并暂停旧17:30单次验收；manual仍共用CLI。回滚先停新触发，保留原件/ready/收据/新事实并恢复已确认组件，不用旧DB覆盖新事实。

### 正式 worker 的已验证原生路径适配

`guojin.gui-profile.v2` 可显式选择 `native_readonly_path=qnx_verified_readonly_v1`。
这只复用 Mini 已验证的 CCustomTabCtrl 通道、SysTreeView32 只读查询节点、
A股 CVirtualGridCtrl 右键刷新、TitleBar 系统菜单保存及港股网格右键保存；不能在该模式追加任意坐标步骤。
该路径的窗口几何合同为 3866×1774、DPI 192，变化即停止。空名称/空 Class 作为真实
原生属性保留，通过当前 PID/HWND、账户 allowlist、只读树父节点、精确菜单 ID/名称、
边界及 FromPoint 命中联合核验，不填造控件身份。UIA 原生树/菜单与保存操作在有界 MTA
线程内执行；超时取消本地后续输入，原 guest watchdog 仍负责进程收尾。
港股保存后的 owned 提示复用真实证据中的 `1040/Static` 导出成功及打开 Excel 文案，
只允许 `7/Button/否(N)`；不以泛化“提示”标题接受其他对话框，不为未知窗口伪造名称。

此适配不改变来源 v2 的 UNKNOWN 时间/完整性、不改变 PR #36 双上限 WARNING、
ready/SyncExecutor 或部分事实仅 Upsert、不清仓的政策。真实三文件导出和候选验证应使用
私有测试配置及独立目录，不能切换生产 ready；代码合并不等于生产持仓发布或定时启用。

[原生辅助函数隔离探针](../tests/test_guojin_native_gui.ps1)编译实际 C# 函数并使用
Fake UIA/user32 核验正常只读点击及移动/坐标/焦点/账户/树节点/命中/布局异常。
Linux/macOS CI 的 `-PortableFake` 仅在合成程序集内省略 COM apartment 设置，
不能证明真实 Windows MTA、原生控件或三文件导出成功。2026-10-22 本轮真实 worker
验收尚未完成；恢复屏幕共享后须在独立候选目录完成正式 worker 导出与原验证器校验，
不能用历史辅助脚本成功或这些 Fake 探针替代。

2026-10-22 启动超时复现：10:56:32 至 11:07:13（约 641 秒）触发 Mac 通信
640 秒上限，Windows 批次目录未创建。普通 `-File`/短 `-EncodedCommand` 查询正常；
原启动器配合无 GUI 的极简子脚本，以长 `-EncodedCommand` 仍超时，相同内容改为
私有 UTF-8 BOM PS1 的 `-File` 在 1.84 秒完成。正式入口只改变启动器传递方式，
保留 worker 哈希、600/610/640 秒上限及自身子进程收尾；不推定具体桥接参数长度上限。
该对照仅证明启动通信修复依据，真实三文件和 WARNING 候选验收仍须单独通过。

2026-10-22 首次点击合同核对：真实频道 ClassName 为 `CCustomTabCtrl`，原 worker
误写为 `CustomTabCtrl`，导致枚举数量 0（预期 1）。按既有成功操作记录修正字面值；
仍要求唯一控件、ID 1001、当前 PID、可见/启用、精确矩形 1,1623,340,80 和最终
FromPoint 命中。私有 failure.json 保留实际/预期字段，不能由隔离探针推定真实导出通过。


### 2026-10-22 全目标实测校准（PR #37）

先在同一 Mini/Windows 会话采集三个市场的树、网格、菜单、owned 保存窗和导出提示，
再统一修改原 helper。私有 `contract-survey-full` 的 `inventory.json`、
`inventory-hk-complete.json`、`inventory-sgt.json` 与 `target-matrix.json` 保留实际
ClassName、Name、AutomationId、祖先路径、动态 HWND、可见性及观察阶段；不可读属性为
UNKNOWN，原生空字符串和虚拟节点 HWND=0 保留原值。运行句柄不写入 profile。

| 目标 | 已验证的字面身份 / 差异 |
|---|---|
| 初次 UIA 读取 | 先在原有有界 MTA helper 读取 desktop/frame，再允许 PowerShell pipeline 访问；仅改 child 为 MTA 不能修复树缺失 |
| 通道 | `CCustomTabCtrl / 1001`，原精确几何、FromPoint 和账户校验不变 |
| A股 | `查询[F4] → 资金股票`，树叶 Class/ID 为空、HWND=0，原 SysTreeView32 祖先校验 |
| 沪港通 | `沪港通 → 沪港通查询 → 资金股票查询`，不是 A股的“资金股票” |
| 深港通 | `深港通 → 深港通查询 → 资金股票`；先显式打开只读市场父节点，不能假定子树已经展开 |
| A股刷新 | `CVirtualGridCtrl / 1047 / Custom1` 右键 → `Item 32833 / 刷新` |
| A股 TXT 保存 | 原 TitleBar 菜单 → `Item 32793 / 保存` |
| 港股查询动作 | 市场父节点、只读查询分类、查询叶逐一命中；重新打开查询页作为实际查询动作，不声称不存在的独立 refresh/SUCCESS 控件 |
| 港股 XLS 保存 | `CVirtualGridCtrl / 100 / 空 Name` 右键 → `Item 32827 / 保存(S)...`；菜单无“刷新” |
| 三市场保存窗 | 原当前 frame owned `#32770 / 另存为`；`Edit / 1001` 与 `Button / 1 / 保存(S)`，同 PID，唯一目标 |
| 港股导出提示/关闭 | 原 owned `#32770`；`Static / 1040 / Excel导出成功…是否需要直接打开Excel?`，仅 `Button / 7 / 否(N)` |

所有输入继续验证当前前台、账户 allowlist、PID/HWND、只读页面/祖先、精确目标、
DPI、实际鼠标坐标及 FromPoint；不点击交易节点、不保存密码。港股动作时间只记录
真实导航/查询动作，`queried_at=null`、完整性 UNKNOWN、不清仓政策不变。
三份校准导出只用于枚举保存/提示目标，不替代正式 `daily_sync.run(publish_assets=False)`
三文件与 PR #36 WARNING 候选验收。后者结果另记交付证据，不切换生产 ready。

Registry 无需更新：原能力入口、数据 owner 和同步职责不变，仅修正既有 GUI 合同。
