# WealthPilot 4.3｜E1-I1 集成准备与单次发布授权点

2026-10-29。当前为 `LOCAL_PASS / ONLINE_BLOCKED_AWAITING_RELEASE_AUTHORIZATION`，`CHATGPT_ONLINE_SAVE_AVAILABLE=false`，B3-2 `NOT_STARTED`。尚未push/merge/deploy、迁移真实资料或打开生产研究写开关。

## 最小差异与证据

实际 fetch：`origin/main=b386bab10a72ee09253bfa5bf746cb89d130354b`；B3-1代码候选为 `21f6ece0f560eb23b826f7fe689ef3fc4657ce3a`，main是其祖先，无新增集成冲突。E1独立分支只增加研究域、MCP注册、示例/测试/文档及原CI命令；不改原PEER、worker、证券交易或账户模块。定点观察的 `codex/v42-sec-quarter-collection` 工作树clean，HEAD等于main；不据此宣布4.2全部结项，不取其未合并旁支。

本次当前Codex连接的 WealthPilot-Prod 目录为13项原工具，没有E1四工具。这是客户端提供的工具目录证据，不是服务器tools/list或ChatGPT Web界面证据；不能区分未部署、未开配置和缓存未刷新。真实生产SHA/启动配置本轮未获授权读取，保持UNKNOWN。

现有 `pocs/chatgpt_skills/server.py` 已有私人库、写开关和scope参数，不需要新增启动包装或服务。旧 `pocs/poc03_mac_mini/runner.py` 仍是PoC02单工具入口，不能把给旧runner加flag当作现役13工具服务接线；发布沿既有Skills进程/原Tunnel，不新建服务、不切回旧入口。

本轮额外实际启动4次独立loopback Streamable HTTP进程，验证关闭/只读/启写工具发现、无确认/取消不写、模拟确认保存、重复幂等、终止进程后重启读回及旧token拒绝。显式合成库scope43；业务库仅不可读哨兵，哈希不变；禁止Worker派生和外部连接。它是 `LOCAL_PASS`，不能标为STAGING或线上真人通过。完整原始结果与命令在仓外4.3私人E1-I1交付目录。

## 一份可执行发布计划（尚未获授权）

目标：现有Mac Mini个人WealthPilot运行用户下，复用已连接ChatGPT的Skills MCP进程与Secure MCP Tunnel。待发布完整SHA以本轮RESULT的 `release_candidate_sha` 为准（仅I1文档在已验收代码候选之上）。PM一次授权下列完整顺序，Ops不再逐字段申请；真实研究保存仍须在展示预览后另收用户当次确认。

1. **集成**：push独立候选、创建/附加PR，运行候选exact HEAD原四项CI，人工审查。main改变则仅对齐已集成main、处理实际冲突并回归，不取4.2活跃代码；新候选SHA如改变须在发布收据明确记录。CI失败不合并。通过后按原linear-history流程合并；不直接推覆盖main、不绕保护。
2. **设备预检及安装**：获同批授权后，Ops读取现役Skills进程的入口/SHA、原解释器、launchd/启动文件、端口、Tunnel指向、原portfolio-id和依赖；不输出Key/账户内容。使用原install-github机制安装已合入且四项CI通过的不可变SHA，保留现役原PEER分析参数/业务解释器/配置，不启动完整backend，不安装新服务。若现役SHA含候选没有的4.2代码，停止切换，禁止回退4.2；仅处理对应集成差异后交PM，不盲目覆盖。
3. **独立持久化**：在设备端核实的既有私人运行根目录下新建 `data/e1/research.db`（文档参考根为 `~/Library/Application Support/WealthPilotProd`，设备实际根由原配置核对，不能猜测）。目录0700、文件0600，使用现役MCP原portfolio-id初始化新E1库；原portfolio-id不改。库在release目录之外，升级/回滚不删除。若路径已存在，验证后复用，不重新init。**不迁B2、账户快照、Key或真实研究原件。**
4. **仅切换现役MCP**：备份原启动文件与参数；在原Skills server命令上追加 `--e1-db-path <上述绝对路径> --e1-enable-writes`。其余scope/real-config/access-evidence/analysis参数、Tunnel归属和端口不变；不改prepare/交易权限。只重启该MCP父进程，原Web/券商/调度不动。新版本读取旧进程内job/token不可恢复，不自动重跑分析；切换前说明会中断未完成任务。
5. **客户端接线**：先设备端确认原工具加E1四项，原工具输入schema/权限不变；再在同一私人ChatGPT连接Refresh，核实四工具出现，开新对话完成真实用户交互测试。官方流程见 [Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata)。账户/工作区实际可用权限仍以客户端为准，不凭文档或工具数宣称真人通过。
6. **真人验收**：使用全新 `E1-I1 TEST ONLY` 合成Thesis，非买入建议、无新PEER/行情/真实持仓调用。展示初始预览，先取消并只读确认无记录；新预览后用户真实说“保存这次判断”，返回 `SAVED_AND_READ_BACK`、真实ID及只读回查。随后在该测试Thesis上预览合成Review，再按真实用户明确确认保存；重复同次确认必须同ID幂等。MCP受控重启后，从同一线上连接读回版本/Review；旧token拒绝。每次研究写入都需用户当次确认，发布授权不能替代保存授权。保留脱敏用户确认、回执及重启读回证据，全部通过才置ONLINE_PASS/true。
7. **回滚**：工具发现、原读接口、保存或权限异常时，先移除E1写flag并恢复备份的原MCP启动参数/现役代码指向，只重启同一进程并Refresh客户端。保留新E1库与备份，禁止删除已确认记录或回滚原业务数据库；不重放job或不确定保存。原13项/当前实际原工具集恢复后停止。若仅客户端问题，保持ONLINE_BLOCKED，不制造模拟确认。

唯一待授权项是上述**完整集成/发布/客户端刷新批次**，不是继续做审计。实际部署前的设备参数核实包含在同批授权，不向PM拆成多次字段审批；当前没有权限跨越此点。B2迁移、采购、订单、prepare/WhatIf、券商同步及B3-2自动化始终不在该批次内。

## 本轮验证口径

本轮未改业务源码；新鲜E1定向及原MCP/新工具回归、HTTP启动测试见私有RESULT。B3-1完整2371/7skip、M5 18/18及lint/build仅作为同业务代码的既有验收，不能写成I1重新执行。I1文档检查单独执行；远端四项CI在授权push后对最终HEAD运行。没有已配置并获授权的独立staging证据，因此STAGING=BLOCKED/NOT_RUN，不以本地HTTP替代。当前停止于发布授权点。
