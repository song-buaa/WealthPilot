# 4.3 投资政策 P1：未激活的统一 Owner

状态：**LOCAL_CANDIDATE / NOT_ACTIVE**。内容批准、结构化校验、结构化审批、部署、生产激活分别留证。P1没有生产发布/激活、Web/PEER/MCP接线或新交易权限。仅用两份用户批准的私有Markdown作为内容源；不在仓库保存真实政策正文或个人目标。

## 1. 复用与范围

以原[纪律服务](../../backend/services/discipline_service.py)、[配置Owner](../../app/discipline/config.py)、[理念服务](../../backend/services/philosophy_service.py)及[AllocationService](../../backend/services/allocation_service.py)为业务归属，在同一`backend/services`下增加[investment_policy](../../backend/services/investment_policy)。复用既有SQLite快照/append-only与确定性来源hash模式，无独立服务进程、索引、LLM、Agent或运行Registry。

旧路径继续运行，P1没有将任何消费者切到候选政策。生产理念全文、custom/official实际选择、JSON值、静态缓存及Chroma资格仍UNKNOWN。文件存在、源码默认值、历史部署收据均不能替代正式私人配置快照。

## 2. 双文档与确定性派生

[parser](../../backend/services/investment_policy/parser.py)支持当前批准版的编号段落、表格和有界公式/句式。调用者传入从人工批准清单得到的两份SHA256；原始bytes先验hash，再按明确语法派生。数值全部取自正文，代码不保存个人资产比例或阈值默认值。源文件不增加机器区块、不重写、不规范化落盘。

解析产物包括：统一分母、中性资产预算、ETF内部结构、直接个股表、弹性预算与条件性未用预算承接、现金占用稳健预算、带来源的风险规则。公式闭合、严格/非严格运算符、特殊发行人覆盖及动作语义由[contracts](../../backend/services/investment_policy/contracts.py)校验。每个字段组/规则都保留源文SHA、章节、行范围及原句。定性约束、历史规则废止、债务/杠杆/产品准入、人工复核期限、过渡预算与绩效局限保留为完整章节，不被误转成额外数值或自动交易规则。

这是**指定格式的确定性解析器**，不是通用自然语言政策编译器。缺行、歧义、未知结构、闭合失败、hash不符拒绝派生；未来正文修改须重新批准hash并复验解析器。若新格式无法支持，返回UNSUPPORTED，不由LLM猜值或要求用户另维护规则JSON。旧手册中的自由提取与默认配置不作为新版回退。

结构化JSON、批准manifest和SQLite均为受控快照/收据；manifest只有两份文件hash，不含第三套可编辑比例。读回/验证时从冻结源bytes重算，JSON即使重新计算自身hash也不能覆盖Markdown。

## 3. Bundle与SQLite迁移候选

[store](../../backend/services/investment_policy/store.py)只接受显式、仓库外私有路径；初始化拒绝已存在文件，不打开当前业务/E1库，无默认配置加载。目录0700、文件0600，读连接`mode=ro`和`query_only`；无配置或缺文件不创建数据库。

| 表 | 职责 |
|---|---|
| `policy_bundles` | scope内递增版本、bundle_id、请求幂等摘要、两份冻结bytes/身份/hash、确定性snapshot/hash、创建及内容批准日期、前版本引用 |
| `policy_events` | 追加DRAFT→VALIDATED→APPROVED收据、结构化审批引用及时间；CAS/幂等、禁止更新删除 |
| `policy_releases` | 供后续授权发布者追加的部署/生效/回滚记录；P1没有写此表的服务或CLI入口 |

三表都有禁止UPDATE/DELETE的触发器；写事务使用BEGIN IMMEDIATE和版本/状态CAS。相同request_id同payload返回原结果，改变payload拒绝。旧版本不能覆盖新版本，历史源文件即使在编辑目录变化也不会改写旧快照。

Bundle包含`bundle_id/bundle_version/scope/source_files/source_hashes/structured_sha256/snapshot/created_at/content_approved_on/content_status/deployment_status/effective_at/predecessor_version`。具体结构化审批时间通过`policy_events`查询，不冒充源文的内容批准日。版本/发布历史通过`history/release_history`读回。

ACTIVE指向由经过独立发布授权的`policy_releases`定义；替换后旧ACTIVE成为历史SUPERSEDED，不覆写原bundle。回滚应追加`ROLLBACK`收据引用确切旧bundle/hash，不能复制旧文件覆盖新库。P1只有合成fixture模拟此过程；真实批准内容仅到VALIDATED，未获得结构化审批或激活资格。

P1候选库是隔离迁移演练，不是第二套生产政策数据库。P2先决定接入原SQLite的迁移位置/事务，再沿原Owner运行已审阅的三张前缀表迁移，不将此候选库自动挂到生产或替换业务库。

## 4. 只读服务与诊断契约

[PolicyService](../../backend/services/investment_policy/service.py)提供：

```python
context = PolicyService(explicit_store_or_none).get_active_policy_context(
    scope, as_of="2031-01-02T00:00:00+00:00", expected_bundle_sha=optional_reference)
```

返回`NOT_ACTIVE`（未配置/无有效发布）、`UNAVAILABLE`（hash/派生/版本/资格失败）、或有效发布的`ACTIVE`。有效上下文包含`PolicyReference`、两份文档身份/hash、配置目标、规则及条款、发布/生效时间、数据质量与限制。读回无静态缓存，每次校验源/派生一致；历史as_of不读取未来生效版本，旧expected hash拒绝为stale reference。它不向任何现有链路注入新版政策，也不把原默认值改称新版ACTIVE。

`PolicyReference`仅是未来分析/E1可使用的独立类型；不修改原E1的字段、历史记录、监控或用户保存确认。

[evaluation](../../backend/services/investment_policy/evaluation.py)是**孤立快照诊断**，不是第二套交易引擎。输入明确资格/时点/最大年龄的数值，不抓持仓、FX、行情或ETF成分；特殊发行人需调用者提供已核实身份。穿透不完整可凭合格已知下限触发风险，不能据未触发证明安全。WARNING不变HARD_BLOCK，HUMAN_REVIEW不变订单；明确禁止行为和传入的原独立安全阻断保留。所有输出`auto_trade_allowed=false`，不输出可买数量。

`budget_preview`只演示正文允许的未使用主动预算条件承接，不永久改目标；临时现金扣减稳健池，总和仍为1。正文未决定现金应从哪只稳健产品减时返回UNKNOWN，不擅自创造产品拆分规则。

## 5. 本地候选入口

仅开发/审查操作，路径均为用户提供的私人目录；JSON输出含私人原句，应保存到该目录，不粘贴公共CI或Git。

```bash
python -m backend.services.investment_policy.cli --db /private/path/policy-candidate.sqlite init-candidate
python -m backend.services.investment_policy.cli --db /private/path/policy-candidate.sqlite stage \
  --scope PRIVATE_CANDIDATE --version 1 --expected-head-version 0 \
  --philosophy /private/path/philosophy.md --policy /private/path/policy.md \
  --approval-manifest /private/path/approval_manifest.json --request-id local-stage-1
python -m backend.services.investment_policy.cli --db /private/path/policy-candidate.sqlite validate \
  --scope PRIVATE_CANDIDATE --bundle-id RETURNED_BUNDLE_ID --request-id local-validate-1
python -m backend.services.investment_policy.cli --db /private/path/policy-candidate.sqlite show \
  --scope PRIVATE_CANDIDATE --bundle-id RETURNED_BUNDLE_ID
python -m backend.services.investment_policy.cli --db /private/path/policy-candidate.sqlite context --scope PRIVATE_CANDIDATE
```

`approval_manifest.json`只含`{"source_hashes":{"philosophy":"APPROVED_SHA256","policy":"APPROVED_SHA256"}}`。manifest不自行授予内容或发布权限。CLI没有approve/activate、网络、索引、生产迁移或交易命令。最后一步在P1真实候选上必须NOT_ACTIVE。

## 6. 旧规则迁移映射及实际调用路径

以下是源码机制，不是生产值：[migration](../../backend/services/investment_policy/migration.py)只接收显式快照生成差异，绝不从旧默认配置补数或合并旧规则到新版。

| 决策 | 旧路径与机制 | 新政策处理 |
|---|---|---|
| D01 | [config](../../app/discipline/config.py)单仓项；[旧rule_engine](../../decision_engine/rule_engine.py)按硬上限拦截；[行动risk](../../backend/services/action/risk_engine.py)另有集中度提示 | 发行人预警/人工复核，禁止按新复核值替换旧硬限 |
| D02 | [Allocation defaults](../../app/allocation/defaults.py)读取动态ranges；[旧risk](../../app/discipline/risk_engine.py)另评偏离 | 目标闭合和一级大类人工复核，不强制交易 |
| D03/D04 | 账户熔断/底仓项由[旧risk](../../app/discipline/risk_engine.py)、[decision](../../app/discipline/decision_engine.py)消费 | 不迁入新版数值规则 |
| D05 | 旧决策路径的事件及情绪输入 | 保留事实/反证/人工研究，不按利空自动加仓 |
| D06/D07 | 流动性、杠杆档位、工具禁项；[discipline_service](../../backend/services/discipline_service.py)组装状态 | 偿债/原币现金/独立准入、明确禁项；不移除原交易安全门 |
| D08/D09 | 成本回撤、单次加仓/批次/间隔和[psychology](../../app/discipline/psychology_engine.py)静态RULES | 不迁入旧机械阈值；定性执行检查保留 |

`config.RULES`是导入时快照，`get_rules()`是动态读取；[PEER data_loader](../../decision_engine/data_loader.py)投影旧参数；[手册API](../../backend/services/discipline_service.py)custom优先、Markdown保存与规则JSON更新分离；[philosophy](../../backend/services/philosophy_service.py)写文件会触发索引。P1**没有调用这些写操作，也没有修其旧行为**。

`check_consumer_versions`为P2验证Web/PEER/MCP/Allocation/discipline同一scope、bundle版本和hash的纯探针；旧版、缺项、混合缓存一律UNAVAILABLE。此为未来接线验收，不宣称所有当前入口已统一。

## 7. P2接入与生产迁移Runbook草案（非执行授权）

1. 另获个人Mac Mini指定连接、只读盘点与后续迁移/激活授权。禁止公司sshm、跳板机、公司配置/Key。
2. 安全取得实际理念、custom/official选择、规则JSON、进程导入缓存、Allocation值及Chroma版本资格；记录文件身份/hash、scope、加载时点。UNKNOWN不能当默认值通过迁移。
3. 用既有SQLite Backup API做一致性备份，核对integrity_check、原表行数及canonical hash；冻结旧源文/配置/索引与运行入口快照，不复制活动WAL、不以旧库覆盖后续已保存记录。
4. 在备份隔离副本预演表迁移、正文/派生重算、D01—D09动作差异和新旧消费者一致性；单独测试账户/授权/合同/报价TTL/资金/费用/券商安全门未放松。新版不得先在PEER使用而Web仍用旧阻断语义。
5. P2最小接线：原理念/discipline统一入口；Allocation采用预算而非区间中点；data_loader/原Skill传确切PolicyReference；Web/MCP薄适配同一只读context。无需新MCP写权限、Agent、策略或全局Registry。检索只引用同源hash，索引失配不代替有效政策。
6. 人工审查派生diff及结构化确切bundle/hash后批准；PR/全部CI/已授权部署验收完成，再另经明确激活授权，以原子事务CAS追加发布收据及生效时间。部署不是激活，内容批准不是结构化/发布批准。
7. 验收全部入口版本一致、历史决策关联不改写、数据不合格UNKNOWN、定量动作无自动交易；新E1引用仅在单独兼容改动中加入，旧记录不回填。
8. 激活失败：关闭新版路由、保留追加历史，在已核准兼容性和独立授权下追加回滚指针/收据；恢复代码入口而不覆盖业务/E1新记录。旧阻断与新版规则不能同时生效，也不能两者都失效。禁止盲目复制旧SQLite回滚。

P2才实施上述接线与发布者；本轮到本地审查交付停止，不在P1消耗生产授权或预先认可结构化候选。

## 8. 测试与限制

[定向测试](../../tests/test_investment_policy.py)只含显式合成源，数值与私人政策不同，验证闭合/边界/特殊覆盖/缺源/时点/历史/幂等/私有库/回滚fixture/版本一致性。真实两份正文的hash、实际预算及任务18项场景在仓库外受控目录运行并留收据，不进入Git。

本地完整pytest、独立MCP协议、Offline M5、frontend lint/build及文档checker按[现有治理](../PROJECT_GOVERNANCE.md#6-质量门与当前基线)执行；实际结果在私人交付报告。没有push，远端CI为NOT_RUN。测试合成激活/回滚不代表已批准源文被系统激活，也不代表生产交易行为验收。


## P2本地接入补充

[P2消费者接入](INVESTMENT_POLICY_P2.md)沿用本Owner；默认LEGACY、ACTIVE仅合成发布验证，未部署或真实激活。P1独立复核补强SQLite防REPLACE/UPSERT历史覆盖；旧schema缺少约束时只读可用、写入拒绝，不自动迁移原候选库。本页P1当时的“未接入”描述为历史阶段边界，当前本地实现以P2说明为准。
