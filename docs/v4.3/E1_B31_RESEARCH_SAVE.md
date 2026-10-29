# WealthPilot 4.3｜E1-B3-1 显式研究保存桥接

2026-10-29历史实现说明。E1-I1后续已完成私人ChatGPT线上真人保存与重启读回，`CHATGPT_ONLINE_SAVE_AVAILABLE=true`；每条记录仍须展示预览并取得当次明确确认。本文件的本地实现和确认边界继续有效；B3-2调度状态另见 [B3-2](E1_B32_REVIEW_MONITORING.md)。

## 日常使用契约

用户只说“美团又跌了，我想补一点”。未来获授权连接的 ChatGPT 客户端应：

1. 用 `get_investment_thesis` / `get_investment_reviews` 查旧记录；需要新专业研究时复用原 start/get，同一 job 取回，不因保存失败重跑分析。
2. 组织 Thesis 更新或 Review，并调用 `preview_e1_record`。已有 schema 提供字段，revision、当前复核时间、request_id 可由服务端生成。既有 Thesis ID 从只读结果取得，新 Thesis 的内部 ID 由客户端组织，不要求用户指定。客户端保留返回的规范化 payload、ID、令牌及指纹，不让用户拼 JSON/SHA/命令。
3. 将 `summary`、`changes`、证据分类/known_at 资格、原分析 partial/limitations、价格/敞口缺口展示给用户。`saved=false`：此时只有有界内存草稿，没有数据库写入。
4. 等待本次新的人类明确指令“保存这次判断”，才用对应 token/fingerprint、`confirmed=true`、该确认原文调用 `commit_e1_record`。“研究完成”、工具返回或先前研究授权都不能代替确认。用户取消则不保存，可用 `confirmation="取消"` 丢弃令牌。
5. 收到 `SAVED_AND_READ_BACK` 后显示 thesis_id、revision/review_id、摘要及 provenance；随后可以用原只读工具查历史。未知/保存错误不报告成功、不自动重跑专业分析。

当前上述过程由**本地 MCP Client 和测试演员**实测，未连线上 ChatGPT。CLI 仍是兼容入口，但本地协议闭环已不需用户逐条 CLI 保存。生产配置/客户端发现、真实人类确认接线仍须4.3集成授权。布尔值和确认文本只是客户端对人类授权的声明，服务器不能从文本认证真实人类身份；令牌仅绑定预览内容，不能替代最终客户端的授权控制，更不是交易授权。

## 配置与实现

| E1 私人库配置 | `--e1-enable-writes` | 工具变化 |
|---|---|---|
| 无 | 默认关闭 | 原工具与行为不变 |
| 有 `--e1-db-path` / `WEALTHPILOT_E1_DB_PATH` | 默认关闭 | 仅原 E1 两项只读查询 |
| 有 | 显式开启 | 另增 `preview_e1_record`、`commit_e1_record` |
| 无 | 开启 | 拒绝启动配置，不隐式建库 |

本轮没有设置生产开关。服务端固定库和 portfolio scope，参数不能传任意路径/SQL/scope；原 real-read 与 mock-trade 边界不改。独立研究库必须已有显式 init、0700目录/0600文件和合法 schema，不迁移 B2 数据。配置片段仅供未来授权本地环境使用：`--e1-db-path <私人研究库> --e1-enable-writes`；原 server 的其余访问验证参数仍必须满足，不能以此启动完整生产 backend。

Owner 为 [ResearchBridge](../../backend/services/investment_thesis/bridge.py)，复用 [contracts](../../backend/services/investment_thesis/contracts.py)、[store](../../backend/services/investment_thesis/store.py) 和 [Review 降级投影](../../backend/services/investment_thesis/service.py)。MCP [适配器](../../pocs/chatgpt_skills/investment_thesis.py)只做参数/协议，默认注册见 [server](../../pocs/chatgpt_skills/server.py)。无新模型、业务库、账户或行情系统。

预览接受 THESIS/REVIEW，经原契约验证；原 AnalysisResult 副本不改写，来源只允许 `USER_SUPPLIED_ANALYSIS_COPY` 或 `NOT_RUN`，不能凭参数宣称原 Worker 认证。无分析须给 not_run_reason。预览纯投影不读取个人上限配置；未传入获授权完整资产视图时保持 UNKNOWN，不擅自查账户或设默认百分比。

## 一致性与错误

预览最多32份、每份输入上限2MiB、15分钟有效；boot内不落盘，过期/重启拒绝旧令牌。确认保存不接受新正文，绑定 scope/规范化正文/expected revision/指纹；新 Review 还绑定预览时的前一 Review。内容变化、Thesis推进或新 Review使上下文过期，均需重新预览和新确认。并非所有历史源都具备严格PIT资格，不能靠保存来升级。

request_id在原两张版本/Review表全scope幂等，同ID不同内容拒绝；自动ID由规范化内容确定。客户端重试必须保留首次返回的规范化记录（含复核时间）、expected revision及request_id，不重新生成时间或新ID。重启后需重新预览并确认，同ID同正文可读回已有结果；不能拿旧令牌重放。

`BEGIN IMMEDIATE` 内检查CAS、幂等并追加事件；Review占位及完成事件在同一事务，故障一起回滚，不新增存储/schema。COMMIT后用只读连接回查才返回成功。若已提交但读回失败，返回错误而非成功；用同ID再确认可返回既有结果，不产生重复。不要把这类未知状态当作保存未发生而换ID再写。

回执示意（合成协议用例节选）：`saved=true`、`state=SAVED_AND_READ_BACK`、`revision=2`、`review_id=<实际生成ID>`、`analysis_provenance=USER_SUPPLIED_ANALYSIS_COPY`、`execution_authorized=false`。预览中请求SUPPORT但原报告partial/缺仓位时有效判断为UNKNOWN；回执不恢复买入许可。完整实际预览/回执由测试输出到显式私有 artifact 目录。

## 可复制验收

沿 [隔离开发](../dev_setup.md) 使用禁dotenv、临时业务DB、Mock/冻结Provider的干净环境；不要借用用户私有库或完整backend。测试用例自动准备明确合成输入，用户不需组装JSON：

```bash
python -m pytest tests/test_e1_save_bridge.py tests/test_investment_thesis.py tests/test_thesis_exposure.py
WP_BUSINESS_PYTHON=<business-python> E1_ACCEPTANCE_OUTPUT_DIR=<已有0700证据目录> <独立mcp-python> -m pytest pocs/chatgpt_skills/test_e1_save_protocol.py pocs/chatgpt_skills/test_e1_protocol.py
```

[存储/确认测试](../../tests/test_e1_save_bridge.py)覆盖无确认、取消、过期、篡改、同ID冲突、并发CAS、前次Review变化、故障回滚/读回失败、跨scope及unknown降级。[MCP实际协议](../../pocs/chatgpt_skills/test_e1_save_protocol.py)覆盖读旧记录→预览→取消/无确认拒绝→模拟人类确认→重复确认→重启读回/同ID重新预览，并比较关闭开关时原工具schema/注解/描述（仅规范化原有boot scope随机值）。业务Worker与socket哨兵零调用。新协议用例纳入原 MCP CI suite，不新增Gate。

B2真实冻结副本另在仓外私人4.3证据目录回放三例，保留原库哈希；副本新增3份Review及3份v3，原6版本/5Review不动。所有确认均是测试演员模拟，没有新PEER/Provider、真实持仓读取或投资决策。数据原件不进Git。实际命令、结果和限制见本轮私有报告/RESULT。

B3-2仅建议沿现有Review时间/事件做受控复核、去重和通知，另行授权；本轮无调度、通知、订单、同步、生产写接线或部署。4.2稳定后再从已集成main对齐注册和文档、跑集成回归，不取未合并旁支。
