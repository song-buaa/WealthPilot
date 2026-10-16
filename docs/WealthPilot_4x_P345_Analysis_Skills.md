# P3 / P4 / P5：原专业分析能力的 MCP 适配

同一 WealthPilot-Prod 增加 `analyze_position`、`review_portfolio`、`analyze_performance`，保留 P1 三读取和 P2 纯 URL 工具。三个工具接受 `request.question`、可选 `symbol`、相关 `history[{role,content}]`。只分析，不创建草稿或订单；用户后续决定交易时使用既有 P2 链接。

最短调用：MCP → 独立业务 worker → `run_chat_stream_v3` → 原 Planning / Executing / Expressing / Reviewing。通过可选进程内 result callback 取得原 LoadedData、规则、信号、行情、研究和结构化表达；汇总原 SSE 最终文本。没有本机 HTTP 请求，没有复制决策计算，没有再次生成结论。普通 Web 沿用原默认入口和行为。

分析 worker 复用原完整 Web Python 环境和运行配置中白名单分析 Provider/代理项；不继承交易权限。`--analysis-business-python`、`--analysis-env-file`、`--analysis-working-directory` 均为操作者配置，模型不能指定路径/Portfolio ID。所有原服务数据库连接使用 SQLite `mode=ro`、query_only 和 authorizer；同一 worker 到退出始终只读，包括超时后仍在结束的可选 Provider 线程。显式进程环境 `WEALTHPILOT_EPHEMERAL_ANALYSIS=true` 让原图不落 checkpoints、原会话不持久化；只作用于该独立进程。ChatGPT 传入相关历史，不写 Web 会话。原交易意图解析在该分析调用中跳过，避免无关重复模型调用。相对知识/纪律文件复用原完整 Web 工作目录；不存在既有知识索引时按原 optional 行为降级，不创建另一份 Chroma 数据库或集合。

结果 `wealthpilot.analysis.v1` 包含 conclusion、原 professional_result.analysis_result、结构化 evidence、scope、data_quality、issues 和 queried_at。查不到组合、模型/路径/执行失败显式返回错误或 unavailable；澄清为 partial，不把缺数据变成零。来源日期使用既有同步成功时间，查询时间不是估值日期；保留同步失败、过期、日期未知、模型降级和审查警告。新建仓直接保留原 Expressing 结构，不使用旧 SSE 兼容摘要中的默认 HOLD 冒充新建仓结论。

- P3 复用 position_single 和原新建仓逻辑；行情字段、规则与信号有数据才返回，不补造实时价格、研究来源或技术形态。
- P4 复用 PortfolioReview/AssetAllocation；返回原 AllocationService 快照、固定默认目标与原偏离计算。原目标不是已验证的个人自定义目标。单标仓位分母为聚合投资持仓；AllocationService 比例分母为正值且可分类的投资持仓，未分类金额单列并标 partial；均不等于完整个人财富，不包含额外注入的实时券商现金。无行业字段则不推断行业集中度。
- P5 复用原 `_build_performance_data(LoadedData)`：当前持仓市值减聚合成本，原盈利/亏损 Top3 与整元展示。不是历史期间收益率、资金流调整收益率、已实现交易盈亏或历史决策因果归因。已有默认用户画像、无日期研究 fallback 仍明确标出限制，不包装为已确认个人偏好或实时研究。

自动化仅临时库/冻结模型或 MockBrokerAdapter；生产验收独立记录，不上传真实金额、账户、凭证或原始金融响应。PoC 02 凭证事故保持历史 FAIL。

## 增量发布与验收

一个 PR、四项 CI 与必要回归后发布 main 完整 SHA。Mini 沿用 install-github 安装，不变更 current、真实数据库、完整 Web 或 Tunnel；仅将现有 MCP 启动包装器固定到该 SHA，并指向已有完整 Web Python/运行配置，保留原 guards、私人 URL 和仅工具名/状态的观察记录。先独立验证七只读工具与原 P1/P2，再备份并重启原 MCP label；失败恢复启动包装器，不恢复/覆盖业务数据库。

在既有 ChatGPT 连接刷新工具（不新建应用/Tunnel）。Web/iOS 分别测试一个现有标的、组合评估、当前收益；核实实际工具调用、原业务事实/口径一致、partial 和限制正确，原 P1/P2/Web 仍可用。iOS 必须独立发起，不能以同步聊天记录代替。

参考：[OpenAI 工具声明](https://developers.openai.com/plugins/reference)、[连接刷新](https://developers.openai.com/plugins/deploy/connect-chatgpt)。
