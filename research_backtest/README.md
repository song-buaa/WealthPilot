# 离线 AV × LEAN 研究模块

只研究；完整能力、来源与限制见[正式合同](../docs/v4.3/AV_LEAN_RESEARCH_INFRA.md)。这是已验收个人实现的最小迁入，不导入原Redstone，不提供HTTP取数/Key加载、生产服务、账户或交易入口。

`SOURCE`为个人受控的已合格AV冻结包（原B3导入格式：HASH_MANIFEST、raw/normalized/events/calendar/coverage及原生日历元数据）；`FROZEN`、两个`RUN`与`REPORT`必须是不同的、仓外个人非同步输出路径。不得把Key、原始行情或大体积工件放到Git仓库。缺原始事件/日历/hash时拒绝，不拿当前S3 DAILY/SPLITS子集冒充完整公司行动包。

```sh
python -m research_backtest.prepare "$SOURCE" "$FROZEN"
python -m research_backtest.verify "$FROZEN" /absolute/new-independent-output
python -m research_backtest.run "$FROZEN" "$RUN_A" --name buy_hold
python -m research_backtest.run "$FROZEN" "$RUN_A" --name annual_small_rebalance
python -m research_backtest.run "$FROZEN" "$RUN_B" --name buy_hold
python -m research_backtest.run "$FROZEN" "$RUN_B" --name annual_small_rebalance
python -m research_backtest.report "$FROZEN" "$RUN_A" "$REPORT" --repeat-runs "$RUN_B"
```

Docker运行只使用原锁定ARM64镜像和本机Unix socket、禁网、只读输入、专用输出，不自动pull/升级、不接受自定义配置模板。原生算法仅在容器内加载QuantConnect包，宿主纯模块不依赖该包或生产backend。

`--strict-settlement`必须在Docker前拒绝；不得catch拒绝后自动改默认模式重试。默认模式仍有原生分红舍入/除息日提前入现，不是严格现金可用模型。report须有锁定引擎完成记录、输入/观察hash和两个独立run_id才能给研究设施PASS；严格结算始终PARTIAL。复制一次运行或指向同目录不能算重跑。

数据采集Owner继续归WP原AV服务；本模块通过不可变文件交接，未来采集扩展沿原Owner，不另建客户端。既有个人冻结数据可直接导入并离线复现，代码不依赖公司GitLab在线或任何公司运行产物。
