# ChatGPT Skills · 迭代 1

独立、显式启动的 Streamable HTTP 入口；不替换现役 PoC 02 runner、launchd、Tunnel 或 Web。业务进程继续使用现有 Python 业务环境，MCP SDK 使用独立环境，不合并两套依赖。

## 本地 Mock

先安装仓库 `requirements.txt` 到业务环境，`pocs/poc01_private_mcp/requirements-dev.txt` 到独立 MCP 环境。示例中的变量必须替换为实际绝对路径；fixture 必须是不存在的新路径，不能指向个人/生产数据库。

```sh
BUSINESS_PYTHON=/absolute/path/to/business/python
MCP_PYTHON=/absolute/path/to/mcp/python
FIXTURE_DIR=$(mktemp -d)
PYTHON_DOTENV_DISABLED=1 "$BUSINESS_PYTHON" pocs/chatgpt_skills/worker.py \
  --init-mock "$FIXTURE_DIR/synthetic.db" --portfolio-id 7
"$MCP_PYTHON" pocs/chatgpt_skills/server.py --port 8810 \
  --business-python "$BUSINESS_PYTHON" \
  --mock-database "$FIXTURE_DIR/synthetic.db" --portfolio-id 7
```

端点为 `http://127.0.0.1:8810/mcp`。fixture 初始无持仓/手工项目，查询会如实显示空数据；自动测试另行写入合成样本。订单完全使用原 MockBrokerAdapter；应用标记阻止误用未经显式创建的数据库。进程重启会丢失 Mock 券商内存状态，查询返回明确不可用，不能自动重下单。该标记是本地防误操作检查，不是身份认证。

```sh
PYTHON_DOTENV_DISABLED=1 "$BUSINESS_PYTHON" -m pytest -q tests/test_chatgpt_skills.py
WP_BUSINESS_PYTHON="$BUSINESS_PYTHON" "$MCP_PYTHON" -m pytest -q pocs/chatgpt_skills/test_protocol.py
```

## 工具与金额

| 工具 | 行为 |
|---|---|
| `get_wealth_overview` | 原 `poc02.wealth-overview.v1`，CNY 整数分，保留质量状态 |
| `get_manual_wealth_items` | 按 kind / item_id 读取原手工资产、负债；原 current_value 为人民币元，original_value 使用 currency |
| `get_investment_details` | 原账户归属分组和持仓；按 opaque account_ref / symbol 过滤；CNY 市值、原币价格、数量、盈亏、原跨账户标的权重及来源日期 |
| `create_trade_draft` | ChatGPT 提取明确 BUY/SELL 参数后持久化原 ActionDraft；无委托 |
| `get_trade_preview` | 只读完整预览、现有风险提示与 preview_hash |
| `confirm_trade` | 新的用户明确确认 + 当前 hash + 原风险确认文字；原 confirm_draft / OrderManager.place_order，当前入口只有 Mock |
| `get_trade_order_status` | 原 sync_order_status 写回状态/成交/审计，因此不是 readOnly 工具 |

账户是已有持仓中的归属分组，不宣称可查询未同步账户或实时可用资金。持仓 `profit_loss_rate` 原样沿用 PortfolioService 的百分数；`symbol_weight_of_investments` 为 0..1，分母为全部投资类持仓，不含手工资产，也不是单账户仓位。无来源日期不使用查询时间替代。

重试草稿使用相同 request_id（最长36字符）；修改指令使用新 ID。显示预览后才可传 `confirmed=true`，风险提示要求时必须保留 `我已知晓风险并坚持下单`。该布尔参数不能证明用户真实点击，ChatGPT 端的写工具确认仍须账号实测。用户未确认、hash 变化、草稿被 Web 改动不提交；重复确认只返回既有尝试。拒单/未知/确认后崩溃均不自动重试。

## 真实读取与部署边界

P1 可复用原 PoC 02 `--real-config /absolute/private/config.json --access-evidence /absolute/private/evidence.json`，不能同时传 `--mock-database`。依旧核验原私人访问证据与仓库外私有配置权限；没有新的认证系统。真实读取模式中的交易调用明确返回 `MOCK_TRADE_FIXTURE_NOT_ENABLED`，不会加载实盘适配器。

本轮没有修改 Mini 安装器或生产入口。先审查额外账户/明细数据的私人范围，再在设备阶段安装 CI 通过的完整 SHA，以独立候选端点验证 P1；不要直接覆盖现役配置。Business 写工具先连接独立 Mock 端点，Web/iOS 分别验收后再决定真实券商的后续授权。本轮不启用实盘。

测试结果、平台待验项和 Web 备用路径见 [迭代验收说明](../../docs/WealthPilot_4x_ChatGPT_Skills_Iteration1.md)。
