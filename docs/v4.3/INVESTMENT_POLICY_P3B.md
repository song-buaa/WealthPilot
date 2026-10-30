# 投资政策 P3-B：正式发布者与隔离迁移预演

状态：**LOCAL_CANDIDATE / NOT_DEPLOYED / PRODUCTION_NOT_ACTIVE**。本阶段依赖未合并的P3-A候选，不更改其Draft PR。生产Schema/一致性备份/真实副本预演的实际状态单独记录在私人报告，不能由合成测试或本页推定通过。部署、内容批准、结构化审批、正式激活和具体交易授权相互独立。

## 1. 复用与最小实现

继续使用[P1](INVESTMENT_POLICY_P1.md)的三张policy前缀表、九项防覆盖触发器、确定性parser和PolicyStore；沿用[P2](INVESTMENT_POLICY_P2.md)的PolicyService、PolicySession及原Web/PEER/MCP/Allocation/纪律。没有新数据库模型、Agent、索引、调度器、HTTP/MCP写工具或交易权限。

- [formal_publisher](../../backend/services/investment_policy/formal_publisher.py)：明确人工审批、安装收据、CAS、发布与回滚；不安装代码、不修改源文或旧规则。
- [publish_cli](../../backend/services/investment_policy/publish_cli.py)：可信本地操作员入口，读取0700目录内0600单链接授权文件，必须显式确认其原字节SHA256。不会由现有应用自动调用。
- [schema_rehearsal](../../backend/services/investment_policy/schema_rehearsal.py)：仅在独立、私有、关闭的备份副本安装政策对象，源备份只读；不是生产迁移入口。
- [policy_backup_readonly](../../scripts/policy_backup_readonly.py)：校验现役snapshot/common源码Hash后复用原SQLite Backup API，仅元数据、行摘要及私有备份，无业务初始化或Provider。生产执行须有单独授权和OS源写/网络禁止保护。

## 2. 正式发布契约

先以P1 CLI完成原文Hash校验、确定性派生、stage与validate。两份批准原文不改字节；结构化内容由同一冻结源重新计算。审批清单不含可编辑政策比例。

`Authorization`包含：decision=APPROVED、operation=APPROVE_STRUCTURED/ACTIVATE/ROLLBACK、environment、approval_id/approved_by/approved_at、human_confirmation=true、scope、store_identity、bundle_id、bundle_sha256、structured_sha256、expected_sequence、expected_active_bundle_sha256。ACTIVATE/ROLLBACK还须绑定installation_sha256。

`Installation`包含：receipt_id、environment、installed_at、完整code_sha、scope、bundle_sha256及web/backend/peer/allocation/discipline/mcp六个同Bundle引用。它是已完成安装/版本检查的操作员收据，Publisher不会声称自己执行或认证了部署。

授权文件是**受信任操作员录入的人工批准证明，不是密码学签名或人类身份认证服务**；SHA确认防止选错/改变批准对象，不能证明谁在键盘前。P3-C必须保存用户原始授权与相符收据，在受控个人终端执行；不能把任务总授权、两个源文件Hash、模型输出或随意生成的JSON当作真实激活批准。ISOLATED_REHEARSAL中的模拟审批不赋予PRODUCTION权限。

结构化审批追加到policy_events的原payload，不扩表、不改旧事件。正式发布追加到policy_releases：绑定完整授权、安装收据、请求摘要、前驱序列、记录/安装/实际生效时间；激活时钟由执行时获取，不接受回填effective_at。相同request_id和输入返回原收据；同ID换内容拒绝；BEGIN IMMEDIATE保证并发CAS只有一方成功。

PolicyService只读复核正式审批、安装SHA、前驱及作用域；缺证UNAVAILABLE。原publish_synthetic仅供真实标记的合成源；读侧也核验两份冻结源前缀，不能把真实政策伪标为synthetic绕过正式审批。政策生效不提升资产资格或订单权限。

## 3. 生命周期与回滚

DRAFT→VALIDATED→APPROVED由追加事件描述。ACTIVE/SUPERSEDED/ROLLED_BACK由不可变发布历史投影，不UPDATE旧Bundle。ROLLBACK必须另有确切目标/安装收据的人工批准、当前序列和原ACTIVE Hash，目标必须曾正式发布；恢复的是批准版本，不能通过关闭配置悄悄恢复LEGACY旧阈值。

激活前失败仍无发布记录、原LEGACY不变。事务中失败整笔回滚，不返回成功。激活后恢复追加ROLLBACK收据并重新核对六消费者；不得用旧备份覆盖后续合法政策/E1历史。历史as_of继续按原生效/可知时钟读对应版本。

## 4. 备份及隔离迁移

生产只读连接使用mode=ro/query_only，外层OS沙箱禁止源目录写入和网络，仅允许确切私人备份目录写入。WAL只读访问/共享内存不可用时，保留BLOCKED；不添加immutable=1忽略WAL、不checkpoint、不放松源写保护、不复制活动库替代一致性备份。读取失败输出阶段、异常类型和SQLite错误名，不打印私人SQL/记录/凭据。

沿原poc03.snapshot.backup，先BEGIN固定源快照，读取表行多集合摘要，再使用SQLite Backup API导出并校验integrity和摘要。不同数据库分别备份不宣称跨库原子快照。备份及schema/摘要收据仅在私人目录，真实数据库和资产行不进入Git、公开CI或公共交付包。

预演目标与备份必须不同inode；显式拒绝符号链接、硬链接和目录穿越，规范化实际路径后核对隔离目录、属主/权限。源备份Hash、完整性、行摘要及源/目标既有Schema均先校验；只允许权威policy对象增量。DDL逐句运行在一个事务内，不用会隐式提交的executescript。已有业务表、索引、trigger、view及历史行必须不变；重复迁移无新增对象，异常整体回滚。恢复只在离线副本进行。

## 5. 本地操作入口

以下为路径占位符。私人授权及原文不得复制到公共日志。此示例不是生产授权。

```bash
python -m backend.services.investment_policy.publish_cli \
  --db /private/isolated/policy.sqlite --environment ISOLATED_REHEARSAL approve \
  --authorization /private/isolated/structured-approval.json \
  --confirm-authorization-sha256 HUMAN_REVIEWED_FILE_SHA256 --request-id UNIQUE_APPROVAL_ID
python -m backend.services.investment_policy.publish_cli \
  --db /private/isolated/policy.sqlite --environment ISOLATED_REHEARSAL release \
  --authorization /private/isolated/activation-approval.json \
  --confirm-authorization-sha256 HUMAN_REVIEWED_FILE_SHA256 \
  --installation /private/isolated/installation.json --request-id UNIQUE_RELEASE_ID
python -m backend.services.investment_policy.publish_cli \
  --db /private/isolated/policy.sqlite --environment ISOLATED_REHEARSAL lifecycle --scope portfolio:7
```

回滚使用同一release命令，但授权operation必须为ROLLBACK，并绑定原ACTIVE与目标历史版本。CLI只返回收据摘要，不输出完整政策或文件路径；无自动审批/部署/研究/交易动作。

## 6. 明确未完成的生产集成问题

当前PolicyStore仍拒绝WEALTHPILOT_DB_PATH及E1库。隔离业务副本可安装policy表，但**不能据此宣称生产共库运行已就绪**。不通过布尔开关移除拒绝；P3-C前若采用共库，需另经审查的专用连接：固定业务库身份，只允许policy读/append、拒绝业务表及DDL、禁止trigger间接业务写，并完成回归。不能偷偷启用一个新的平行政策权威或擅改生产路径。此缺口及真实备份/Schema缺证均须保留为阻断，而非推定通过。

静态RuleEngine缓存及Chroma内存无法安全提取时维持UNKNOWN。P2在ACTIVE隔离旧数值和未版本化索引，不以缓存或Top-k片段替代PolicyService。没有完整可投资分母、FX或外部资产/穿透证明时继续CANNOT_QUANTIFY/UNKNOWN，HUMAN_REVIEW不变订单或硬持仓上限。

## 7. 验证与后续授权

新增[Publisher回归](../../tests/test_policy_publisher.py)、[迁移/Backup回归](../../tests/test_policy_schema_rehearsal.py)与[实际MCP协议](../../pocs/chatgpt_skills/test_policy_protocol.py)，并保持原全量、M5、前端及E1/交易权限Gate。公开测试只用合成源；真实批准源及冻结生产旧规则在私人脚本中交叉验证，不能代替本轮实时生产备份。

本轮只本地提交，不Push/更新远端PR/合并/部署/生产DDL或激活。正式P3-C须先消除存储/备份阻断、验证最终候选远端四CI、独立Review和同版本切换，再分别取得部署与实际政策激活授权。私人P3-C Runbook维护具体设备/路径及回滚收据，不在仓库暴露。
