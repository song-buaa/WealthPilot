# 投资政策 P3-B：正式发布者与隔离迁移预演

状态：**LOCAL_CANDIDATE / NOT_DEPLOYED / PRODUCTION_NOT_ACTIVE**。P3-B/R1/R2为P3-A的后继；P3-C1现将其与最新已集成main合入同一Draft PR #65，等待独立合并授权。生产Schema/一致性备份/真实副本预演的实际状态单独记录在私人报告，不能由合成测试或本页推定通过。部署、内容批准、结构化审批、正式激活和具体交易授权相互独立。

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

## 6. R1共库受限连接与真实备份边界

R1沿现有[共享库契约](../../app/shared_sqlite.py)增加[CoLocatedPolicyStore](../../backend/services/investment_policy/colocated.py)，继承原PolicyStore的stage/事件/Publisher协议，原Store拒绝业务/E1库的保护不变。显式配置`WEALTHPILOT_POLICY_SHARED_CONTRACT`或CLI的`--shared-contract`选择的是完整受控身份契约，不是布尔绕过；固定path、device/inode、owner/权限、scope和完整Schema指纹，每次连接及SQL前重新核验。

日常连接专用sqlite3且无连接池，不借用ORM/原业务writer连接。预检只读Schema元数据、不读Portfolio/账户行；运行期只有固定scope及精确17条政策SQL模板。SQLite authorizer仅允许政策表读取/追加和九项已核验触发器的规定读取，拒绝业务/E1数据、UPDATE/DELETE、DDL/PRAGMA/ATTACH/视图和非政策trigger间接访问。只提供execute及已取回的政策行，不提供原connection/cursor/authorizer操作。任何权限/身份复检异常污染事务，即使内部捕获并恢复配置仍整体回滚。连接退出后不可重用。

本边界限制SQL能力，不声称隔离任意同用户恶意Python进程。批准的共享契约是原数据库Owner派生运行收据，不是第三份人工政策。分析Worker的WEALTHPILOT_DB_PATH可能指向U1冻结视图，政策仍绑定原共享契约，不能跟随临时分析库重绑定；缺失/错配配置fail closed。

Schema安装仍与日常连接分离：隔离备份校验在DDL前检查源外键；DDL阶段独立authorizer只允许确切政策对象，不能调用运行连接迁移或访问订单。原业务/E1事务与运行配置不变。以后实际生产Schema改变须另行授权，并按原Owner流程更新共享契约指纹；本轮不安装、不重启或激活。

R1真实备份与实际迁移结果以私人收据为准。备份一致性/恢复成功不等于所有外键有效；发现既有外键问题时禁止忽略、删除历史或放宽迁移门禁。静态缓存和Chroma内存不能安全读取则保留UNKNOWN；ACTIVE仍隔离旧数值及未版本化索引，资产资格不足仍UNKNOWN。

## 7. 验证与后续授权

R1新增[共库权限回归](../../tests/test_policy_colocated.py)。新增[Publisher回归](../../tests/test_policy_publisher.py)、[迁移/Backup回归](../../tests/test_policy_schema_rehearsal.py)与[实际MCP协议](../../pocs/chatgpt_skills/test_policy_protocol.py)，并保持原全量、M5、前端及E1/交易权限Gate。公开测试只用合成源；真实批准源及冻结生产旧规则在私人脚本中交叉验证，不能代替本轮实时生产备份。

P3-B/R1/R2阶段只本地提交；P3-C1只授权Push/更新Draft PR及远端CI，不合并、部署、生产DDL或激活。后续须核对最终候选远端四CI、独立Review和同版本切换，再分别取得部署与实际政策激活授权。私人P3-C Runbook维护具体设备/路径及回滚收据，不在仓库暴露。

## 8. R2真实备份副本验收边界（2026-10-31）

R2在用户针对唯一历史孤儿及其V2迁移标记例外分别确认后，完成一行受控生产研究数据清理。删除前后均使用原SQLite Backup API及源写禁止保护取得私人一致性备份；事务要求目标完整行Hash、唯一外键异常、备份与事务起点摘要一致、影响恰好一行，删除后核对全部非目标表、Schema及E1。V2卡片和历史文字迁移标记保留；该标记不再指向现役V1行，原行保留在私人备份及恢复证据中。一次性操作脚本与原记录不进入仓库，不新增常驻修复服务。

清理后真实两库备份的完整性、外键及隔离恢复通过。在真实业务备份的隔离副本完成政策三表/九触发器安装、重复迁移、故障回滚、共库受限连接、正式Publisher的模拟审批/发布/回滚及消费者PolicyReference读回。原43张业务表和独立E1五张表、原Schema对象均按行多集合Hash核验。MCP使用实际SDK只读查询隔离副本，禁止调用业务Worker、PEER和券商。私人收据记录具体数据库身份、Hash及逐表对账，不以本页替代生产运行证据。

本轮没有业务源码修复；R1安全边界和权限测试保持不变。生产仍无政策三表/正式PolicyRelease，不迁移或激活政策、不部署/重启、不修改E1、不执行订单。后续P3-C须重新对齐已集成main、复测并取得生产迁移/安装/激活的明确授权，不能将真实副本验收当作生产政策迁移成功。原孤儿的完整备份已在隔离目录恢复验证；不得自动把带缺失父引用的原行插回生产、关闭外键或用旧库覆盖新合法历史。

R2本轮政策定向152、E1/交易安全136、共享库/部署只读保护146、独立MCP协议59、Offline M5 18/18及21份文档检查均通过；最终独立Review Critical 0 / Important 0。`P3B_READY_FOR_P3C_REVIEW=YES`仅表示可交P3-C审议，不是生产迁移或激活许可。本轮未改业务源码，未重跑完整pytest和前端lint/build，不复用R1全量数量冒充本轮结果。


## 9. P3-C1最终集成与五个生产关口（准备，不执行）

P3-C1保留P1→P2→P3-A→P3-B→R1→R2全部祖先，与实际fetch的main合并，不移植未合并旁支。原18项MCP工具的条件注册保持不变；显式配置政策库时另外注册只读`get_investment_policy_context`，不会提供政策写工具。私人原文、配置、备份及R2一次性删除脚本不进入Git，也不属于部署初始化任务。能力Owner/接口无新增；复用原PolicyService、PEER、Web、MCP和共享SQLite。

[部署边界回归](../../tests/test_policy_installation_boundary.py)验证：未配置政策路径的六消费者只读查询为NOT_ACTIVE/LEGACY，不打开政策DB、不执行DDL/Publisher；提前指向没有政策Schema的共享库为UNAVAILABLE且不创建对象；缺失政策文件不自动创建。原[Publisher测试](../../tests/test_policy_publisher.py)验证APPROVED但无有效发布仍NOT_ACTIVE，真实源不可用合成入口绕过批准。损坏/错scope/旧引用均拒绝降为旧规则后继续确定性建议。

```mermaid
flowchart LR
  Main[最新已集成main + P1至R2] --> Draft[单个Draft PR / exact HEAD四CI]
  Draft --> Merge[关口1：人工授权Merge]
  Merge --> Code[关口2：原install-github安装与受控切换 / LEGACY]
  Code --> Schema[关口3：单独授权政策DDL与Owner契约升级]
  Schema --> Bundle[关口4：原文Hash + stage/validate + 独立结构化审批]
  Bundle --> Active[关口5：确切Bundle/安装收据的ACTIVATE授权]
  Active --> Consumers[Web / Backend / PEER / Allocation / discipline / MCP同PolicyReference]
```

| 关口 | 必须证据和Owner | 成功后仍禁止自动进行 |
|---|---|---|
| 1 Merge | exact HEAD四CI、独立Review、最新main祖先、人工明确Merge授权；原受保护流程 | 不自动部署/生产读写 |
| 2 LEGACY兼容部署 | 另获部署授权；原Mini操作员核对每组件现役SHA/启动参数/私人Skills包装器、Tunnel、解释器、scope；完整main SHA必须经install-github核验同SHA四CI；新的一致性备份与入口/配置回退收据 | 不填政策环境变量，不运行policy CLI、DDL、Publisher或R2清理；政策仍NOT_ACTIVE/LEGACY |
| 3 Schema | 另获迁移授权；新FK0/integrity备份/恢复；原共享Owner固定file identity/scope，审查完整三表九trigger DDL、单事务authorizer和故障回滚；对账原业务/E1行与Schema | 不激活，不把schema_rehearsal的隔离路径校验当生产迁移入口；不关闭外键 |
| 4 Bundle | 私人两份批准原字节Hash；policy CLI在固定共库契约stage/validate；独立APPROVE_STRUCTURED授权，精确Bundle/structured SHA、scope及DB身份 | 无ACTIVATE收据仍NOT_ACTIVE；不将原文批准或模拟审批当正式生效 |
| 5 ACTIVE | 另获确切ACTIVATE授权；安装收据完整code SHA/时间及六消费者Bundle Hash；CAS当前sequence/ACTIVE Hash；所有消费者准备切换同一引用 | 不扩大资产/券商/交易权限，不自动保存E1或执行PEER/订单 |

Schema改变与共享契约指纹更新是关口3的同一次受控切换：先保存旧契约与新DDL计划，协调所有现役共享库读写进程，防止旧进程在新Schema下继续操作；按原Owner验证新完整Schema后以受控原子替换升级私人契约并重新校验相关组件。当前仓库提供隔离迁移预演而非可直接对生产执行的通用迁移CLI；关口3必须明确实际操作脚本、源写授权及契约切换/故障恢复，不能使用日常PolicyStore连接执行DDL。缺少这些前置时保持LEGACY，不影响关口2兼容安装准备。

关口2运行收据逐组件核对，不能只看GitHub main或一项健康：install-github的github-install/manifest、组件当前启动路径及进程cwd/命令、依赖解释器、Web dist构建来源/Hash（完整Web可独立安装）、Backend源码SHA、MCP现役包装器导入路径均须绑定最终SHA。现有受限Web状态可报告revision，但不能把该测试入口部署成现役Full Web，或把MCP固定协议版本当应用SHA。P3-C1不访问生产，因此最新现役各组件SHA仍待获部署授权后的定点预检。

LEGACY安装时保持现有政策env未配置；若误配一个尚无policy表的共享库，代码将UNAVAILABLE，必须恢复已批准配置并验收，而不是自动安装Schema或混用旧值。正式ACTIVE以后禁止通过unset路径回旧纪律；异常先停止精确建议/写入入口，在独立ROLLBACK授权与兼容安装收据下追加历史。Publisher批准文件只是可信操作员保存的授权证明，不提供人类认证；须保留真实用户原始授权引用。

代码回退只恢复已核验兼容的原组件/配置/入口，不替换业务或E1数据库。政策回滚使用`publish_cli release`且operation=ROLLBACK、目标须曾正式发布，绑定当前sequence/ACTIVE SHA、目标Bundle和新安装收据；不得重放旧request_id换内容或用旧备份抹除后续合法历史。代码发布、DDL、结构化批准与ACTIVATE没有一键自动链。

P3-C1最终状态以本轮私人报告、最终PR HEAD及GitHub四CI收据为准；本页说明准备边界，不宣称已安装、激活或真实ChatGPT业务验收通过。P3-C2/C3、真实Provider调用/成本和部署分别等待明确授权。
