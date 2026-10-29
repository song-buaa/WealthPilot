# WealthPilot 4.3｜E1-B3-2 定期与正式事件复核发现

2026-10-29。本地候选分支 `codex/e1-b3-2`，基线 `a2def5d09ba4968845c3733735643523dee44d66`。状态：`LOCAL_IMPLEMENTED`；生产调度 `OFF`，主动通知 `NOT_WIRED`，未push/merge/deploy。

## 最小闭环

Owner仍是E1私人研究域。`ReviewMonitorStore`只读取不可覆盖Thesis/Review，并在**同一个私人E1 SQLite**中追加监控事件与待办收据。显式`init`将schema v1迁移为v2；三张原研究表、内容哈希和不可覆盖触发器不重写。核心研究读写继续兼容v1/v2，监控命令仅接受v2。

明确opt-in必须绑定：当前Thesis revision、已完成Review、Review中已有的`next_review_at`/`next_review_event`、发行人ID和允许的正式事件类型。Thesis推进、关闭/失效、监控取消、时点无时区或条件不完整时不触发，要求基于新版本重新Review并再次opt-in。

手动扫描使用注入`as_of`。到期键由`thesis_id + revision + due_at + DUE`确定；事件键由`thesis_id + revision + economic_event_id + EVENT`确定。同一扫描、进程重启或同一经济事件的不同消息不会生成第二个待办。状态用append-only事件表示`PENDING / NOTIFIED / DEFERRED / HANDLED`；本地`NOTIFIED`只允许`SIMULATED_LOCAL`，不宣称用户收到生产通知。

待办只给出Thesis、当前版本、触发原因/事件证据、最近Review和下一步路径：读原记录 → 按需调用原`start_position_analysis`/`get_position_analysis_result` → 预览新Review → 等待用户当次明确确认 → 保存。扫描不运行PEER、不自动写Thesis/Review、不刷新资产、不生成数量或订单。

## 事件资格

本地冻结fixture使用PDD Holdings正式SEC 6-K：CIK 1737806、accession `0001104659-26-100534`、接受时间`2026-08-25T07:20:02-04:00`、原件SHA256 `162f5c2578869f4870fe24cc1ecb1518273447f3e21e0ba4ace1dfbd8cdc8b42`。fixture来自已合入4.2 SEC原件链，不执行网络请求。

事件必须同时具备精确symbol/issuer、允许类型、安全原始URL、原件哈希、published/known/fetched三个有时区时钟，且在扫描`as_of`前已经公开、可知并取得。身份不匹配、未来信息、字段缺失或未opt-in类型进入拒绝清单；没有合格事件时返回`NO_ELIGIBLE_EVENT_SOURCE`，不包装为自然触发成功。

## 手动入口与生产启用边界

开发环境使用显式私人测试库：

```bash
python -m backend.services.investment_thesis.monitoring_cli --store <e1.db> --portfolio-id <scope> init
python -m backend.services.investment_thesis.monitoring_cli --store <e1.db> --portfolio-id <scope> enable --input <monitor.json> --request-id <id>
python -m backend.services.investment_thesis.monitoring_cli --store <e1.db> --portfolio-id <scope> scan --as-of <ISO8601> --events <frozen-events.json>
python -m backend.services.investment_thesis.monitoring_cli --store <e1.db> --portfolio-id <scope> list --as-of <ISO8601>
```

现有完整backend确有APScheduler，但本轮没有注册B3-2 job，也没有启动完整backend。未来一次性4.3集成应在稳定main上回归后，复用Mac Mini原运行架构增加默认OFF开关和单个有界扫描job；先只生成待办并保留`NOT_WIRED`。若要向ChatGPT主动通知，须先确认合法通道和另行生产授权，不能用工具注册或本地模拟代替。

## 验收与限制

定向测试覆盖：未到期/到期、重复扫描、处理后抑制、延期、取消、关闭、版本推进、scope隔离、重启持久去重；正式事件精确匹配、不匹配、未来、缺身份/哈希及同经济事件去重；原Thesis/Review前后完全一致。测试入口：[B3-2](../../tests/test_e1_b32_monitoring.py)、[原E1](../../tests/test_investment_thesis.py)、[保存桥接](../../tests/test_e1_save_bridge.py)。

E1-I1线上TEST ONLY Thesis仍不构成真实投资判断。B2三例PEER仍为partial，真实仓位`CANNOT_QUANTIFY`、个人上限`UNKNOWN`。本轮不迁移B2、不修复这些缺口、不启用自然调度、真实事件拉取或生产提醒。
