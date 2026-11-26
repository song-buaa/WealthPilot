# 4.4 C 的 ETF 证据更新

ETF Data Owner 负责公开源原件、解析及证据签发。C 通过原 Allocation 读取已有 U1 和 ACTIVE Policy。Web、MCP 和 PEER 继续使用同一份 C 结果。该候选沿用原 `wealthpilot.etf-evidence.v1` 和同日收盘、有效窗、分母门。C 对同一配置一次只使用一种证据入口。

| 输入 | 处理 |
| --- | --- |
| SPY 日持仓、同日总净资产、ETF 与成分独立收盘 | 仅对已验 CUSIP 的 AAPL、GOOG、GOOGL、NVDA 尝试签发部分下限，未证明的尾部保留未知。 |
| QQQ、SPMO 官方完整日持仓 | 归档观察权重、来源时间、发行人和复杂头寸。当前日表缺期货账面冲抵、抵押和净敞口闭合证据，保持观察。 |
| 原固定 SHA 配置 | 继续校验 `WEALTHPILOT_ETF_EVIDENCE_FILE` 和 `WEALTHPILOT_ETF_EVIDENCE_SHA256`。 |
| 新签名发布配置 | 从受控目录读取签名回执和独立字节 SHA 对应的不可变 manifest。所用公钥来自消费者的可信配置。 |

程序记录实际落后交易日数。普通观察最多五个已完成交易日属于待审批的产品标准。当前观察供 Owner 检查，正式风控只使用合格记录。风险阈值由原 ACTIVE Policy 读取。

## 隔离执行

以下命令从项目 checkout 执行。Owner 目录使用非同步、本地目录。准备隔离测试 Ed25519 seed，其文本为 64 位 hex，文件权限为 0600。不在 Git 保存 seed。签名操作只有发布 Owner 运行。

```sh
python -m scripts.refresh_etf_evidence \
  --directory /absolute/owner-release-directory \
  --key-file /absolute/private/etf-owner.key \
  --portfolio-id 7
```

命令取得三个固定发行人来源。SPY 合格时另取固定 State Street NAV 及 Nasdaq 收盘。连接上限 20 秒，拒绝跨 URL 跳转。原件按 SHA 保存，解析结果按 parser 版本和来源 SHA 保存。单只来源不可用时继续其余来源。重复字节复用观察解析，保留最初观察日期和正式证据时钟。未获证的 SPY 若仍在合格日，可按需重试证明。收到更新后原子发布签名 `current.json`。回滚以新发布序列选取已验历史收据，原证据时钟保留，到期记录被略过。不要用系统命令覆盖 `current.json`。

按需使用相同命令。加 `--watch` 可在专属 Owner 进程复用项目 APScheduler。美东 06:00 仅在前一日为 XNYS 交易日时检查，包括星期六检查周五披露。周末和节假日通过交易日历处理。4.1 负责确认获授权的作业承载与日志保留。应用主进程不持有 ETF 签名 seed。可通过 `--listing-file` 和独立 `--listing-sha256` 消费 4.3 Owner 导出的上市证券记录。上市日期区间和 CUSIP 必须一致。原 C 仍使用当前 PolicySession 内的原上市映射。

`--rollback-receipt receipt-<sha256>.json` 使用本渠道签过的收据。投产前需完成两个真实运行身份的权限验收，并由 Product Owner 批准发布、安装及调度。

## 权限与估值

消费配置 `WEALTHPILOT_ETF_RELEASE_DIRECTORY` 为规范绝对路径。发布目录和父目录由发布 Owner 或系统管理，消费者无法替换。推荐目录 0750 和共享只读组，版本文件 0640。发布与消费 UID 必须不同，消费者通过独立配置 `WEALTHPILOT_ETF_RELEASE_OWNER_UID` 固定发布属主。消费者中的 `WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY` 固定为 Owner 公钥的 64 位 hex，由生产配置 Owner 安装。签名、组合 ID、收据时间、manifest SHA、版本和记录时钟在读侧验证。两个入口配置同时出现时读侧保持无资格。

历史 SPY 方法分母为已验基金净资产。自动候选沿用原数学资格，额外用较大的分母限缩下限。比较官方总净资产与 ETF 份额乘独立收盘上限，再用较大值。C 在同日最近收盘的原资格门之后，验证实际入账 ETF 金额不超过已持份额乘同一 FX 及签名的 USD 收盘上限。缺价格基础或冲突时，该 ETF 正式贡献降级。该收紧避免二级市场溢价放大按份额归属的 NAV 下限。上界和未认证成分继续未知。需要把 Invesco 观察权重升格、把五交易日滞后作为正式有效窗，或修改现有正式规则时，先取得 Product Owner 批准。

安装后由生产 Owner 验证签名和到期降级，再以真实同一 U1、收盘估值、可投资分母、ACTIVE PolicyReference 核对 C。真实分母未知时输出条件风险，正式比例及政策命中为空。ETF 缺证和到期保留有效的 B 配置。
