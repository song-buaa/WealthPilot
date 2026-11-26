# Policy Configuration & Fast Release P1

[#127](https://github.com/song-buaa/WealthPilot/issues/127) 开发候选。适用于现有支持类别、现有 ETF 和合格美国上市普通股战略目标。原三张不可变表、原 scope、原 SQLite 写事务及原读取引用保留。版本 `wp.policy.config.v1` 与 Bundle 序号分别管理。

## 配置与来源

`status --config-out <私有目标文件>` 从当前 ACTIVE 导出可编辑目标。目标使用十进制字符串，例如 `"0.04"`。类别和 ETF 现有集合必须完整，直接股按稳定发行人 ID 输入一次。未指定额度与实际未用额度分别处理。一级节点从已支持类别计算。利益相关风险阈值、原策略分母、临时现金上限和美国核心权益底线均从批准前序继承。BTC 目标和中性现金因原条款另有批准资格，留在强化审批路径。若新权重无法维持原弹性上限、联合约束或核心权益底线，本期拒绝发布。

编译产物冻结完整配置、原始来源 SHA、继承约束、八条风险规则、身份记录及目标到证券的绑定。既有受控证券映射与 Owner 核准的补充上市身份数据共同使用。补充数据经 Owner 查验证券类别、官方来源、有效期及证据 SHA 后放入受保护安装清单。目标文件不能提交自己的身份数据。身份冲突和过期在 preview 与发布前检查。已冻结快照回放使用当时绑定。

配置生成新的可读 Markdown 和结构化快照。原理念保留原字节，混合配置章节重算目标数字，原则与风险条款沿原批准模板。发行人身份无法确认时拒绝发布该目标。政策读回不提升实际持仓分母资格。

## 命令

```sh
python -m backend.services.investment_policy.config_cli \
  --db "$WP_POLICY_DB" --shared-contract "$WP_POLICY_SHARED_CONTRACT" \
  --environment PRODUCTION --scope portfolio:1 \
  status --config-out "$WP_PRIVATE_DIR/targets.json"
```

后续命令使用相同前缀。

1. `preview --config targets.json --out candidate.json`。在 0700 私人目录内生成 0600 候选、派生 Markdown 和两份来源 SHA。读取人核对完整 Diff、候选 SHA、预期 Bundle 和前序。preview 只读政策库。
2. 人工签名工具生成独立批准 JSON，其中 contract 为 `wp.policy.fast-release.v1`，包含 `operation=PUBLISH_L1`、`candidate_sha256`、`environment`、`scope`、`store_identity`、`approved_by`、实际带时区的 `approved_at` 和独立 `approval_id`。批准人使用独立保管的 Ed25519 私钥对该 JSON 的规范化 UTF-8 字节（递归排序键、紧凑分隔、保留 Unicode、无终止换行）生成原始 64 字节签名，例如 OpenSSL 3 的 `pkeyutl -sign -rawin`。批准 JSON 和签名沿原操作员安全通道交付。
3. `publish --candidate candidate.json --approval approval.json --signature approval.sig`。校验签名、当前安装、候选和前序，执行六路候选兼容确认及原 Backup API 保护。一个事务追加 DRAFT、VALIDATED、APPROVED 和 RELEASE。保留 stdout JSON 作为发布回执。
4. 激活后六路只读执行实际读回。若发生 `ACTIVATED_READBACK_PENDING`，用同一候选、同一批准和签名重试，或用 `status` 重新核验。旧前序已被其他发布取代时显示历史已记录及当前已改变。事务中断保留旧 ACTIVE。正常路径无需重启。
5. 回滚用 `preview --rollback-to <已正式发布Bundle ID> --out rollback.json`，独立签署 `operation=ROLLBACK`，再用 `rollback` 执行。回退到只识旧格式的代码前，先在兼容版本追加经批准的历史 Markdown Bundle 回滚事件并验证六路读回，继续保留配置化发布历史。

## 首次安装

沿原 Mini 发布机制安装获批的 main SHA。现有三表及历史可由新代码读取，无政策 DDL。生产固定读取 `/private/etc/wealthpilot/policy-fast-release.json` 的 root 所有且不可被操作用户改写配置。根清单预置实际批准人、公钥、代码和 store 身份、scope、独立备份目录、受控身份数据及六个候选/ACTIVE 探针命令。签名私钥不进入此清单、代码或部署主机。首次上线需验证该清单和探针后再开放运行。

探针通过 stdin 收到 phase 及 scope。CANDIDATE 另含冻结 Bundle，探针绑定已安装代码并检查对应消费者兼容。ACTIVE 通过各自的实际只读入口取回当前引用，返回 consumer、phase、code_sha、runtime_id 和 policy_reference。ACTIVE 探针不接收目标 Bundle。可用本仓 `python -m backend.services.investment_policy.config_probe --consumer <名称>` 绑定六个原入口，读取现有 Mini `installed.json`。Web 和 MCP 的 ACTIVE 探针需由安装者指定现役 127.0.0.1 端点，Web 如需鉴权可指定 0600 headers 文件。内嵌候选探针不启用完整后端、不启动持续服务。生产安装清单和运行实例的实际接线属于首次安装验收。激活前后收据各自检查，任一路径失败不产生最终 PASS。

隔离运行使用 `--environment ISOLATED_REHEARSAL --rehearsal-trust <私有清单路径>`。不使用生产密钥、进程或政策库。现有 MCP 仍仅提供政策读取。本期没有远程政策写入入口。
