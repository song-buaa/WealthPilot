# PoC 02：私有财富总览 MCP

独立 MCP Server，仅有 `get_wealth_overview(currency="CNY")`。默认合成数据；真实模式必须先通过私有接入检查。没有交易工具，不启动完整业务后端，也不修改业务代码。

## 两个运行环境

MCP 使用独立 Python 3.11 环境，业务读取使用现有 WealthPilot Python 环境，通过一次性 worker 进程复用服务，避免在 MCP 进程中加载业务后端或混用 MCP SDK 版本。

```bash
python3.11 -m venv /tmp/wealthpilot-poc02-venv
/tmp/wealthpilot-poc02-venv/bin/python -m pip install -r pocs/poc02_wealth_mcp/requirements.txt
/tmp/wealthpilot-poc02-venv/bin/python pocs/poc02_wealth_mcp/server.py
```

只监听 `127.0.0.1:8788/mcp`，标准 Streamable HTTP，JSON 响应、stateless。合成模式不导入业务模块、不访问财富数据库。应用层 `noauth` 依赖 Secure MCP Tunnel 的身份与组织/工作区边界，**不能将真实模式接到 PoC 01 的匿名公网隧道**。本机受信任进程仍能访问 loopback；这是单用户本机 PoC 的信任边界。

## Secure MCP Tunnel

1. 在 Platform Tunnels 创建 `WealthPilot PoC 02`，仅关联自己的个人组织和个人 ChatGPT 工作区。核对组织成员与 Tunnels 权限。
2. 从官方 `openai/tunnel-client` release 下载客户端，核对 SHA256SUMS。此次验证使用 v0.0.16 darwin-arm64，临时安装到 `/tmp/wealthpilot-poc02-tunnel-client`。
3. 用户自行创建运行 API Key，授予 Tunnels Read + Use；不使用 Admin key，不把密钥发到聊天。运行 `prepare_tunnel.py` 在本地隐藏输入，将密钥存于 Git 外的 0600 文件。
4. `tunnel-client init --sample sample_mcp_remote_no_auth`，MCP URL 为 `http://127.0.0.1:8788/mcp`；API key reference 为 `file:/tmp/wealthpilot-poc02-private/runtime-key`。Tunnel ID 从页面保存到本地，不放入仓库。
5. 执行 `doctor`，以前台 `run` 启动。健康面板仅绑定 `127.0.0.1:8789`，不启用远程 UI、原始 HTTP 日志或 payload capture。`doctor` 的 configured PASS 本身不证明账号鉴权通过；还需控制面成功轮询和实际 ChatGPT 调用。
6. ChatGPT 添加自定义 MCP → Tunnel → 选择该 Tunnel。应用层选择“无需身份验证”，外层由 Tunnel 授权；只在合成模式先验证发现、调用和匿名/无效凭证拒绝。查看插件“已连接”不等于实际工具调用通过。

官方资料：[Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)、[Custom MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server)、[应用认证限制](https://developers.openai.com/plugins/build/auth)。

已创建本地 `poc02` profile 后，以前台进程运行（先启动 MCP）：

```bash
/tmp/wealthpilot-poc02-tunnel-client/tunnel-client run \
  --profile poc02 --profile-dir /tmp/wealthpilot-poc02-private/profiles \
  --log.file /tmp/wealthpilot-poc02-private/tunnel-runtime.log \
  --log.level warn --admin-ui.log-buffer-events 1
```

`http://127.0.0.1:8789/readyz` 返回 200 后再发起 ChatGPT 验收。运行密钥由用户保存、组织/工作区范围由用户确认；浏览器不要读取或输出尚未关闭的密钥弹窗。本次实际权限/凭证事故详见验证报告，不能将这里的最小权限建议误当作已完成验证。

## 真实模式（访问保护通过后）

本地私有 JSON 配置位于 Git 外，权限 0600：`database_path` 为既有 SQLite 的绝对路径，`portfolio_id` 为当前 Web 使用的组合。没有自动选择或创建数据库。可选 `tiger_cash` 只包含已有现金读取所需的三个配置字段；未提供时明确返回 `not_configured`，不会偷偷读取 `.env`。

另一个 0600 activation evidence JSON 包含经过人工审阅的 `access_scope_confirmed`、`synthetic_web_pass`、`anonymous_denied`、`no_public_listener` 四项 true。此文件仅防止操作误开启，**不是认证机制**；实际边界由 Tunnel 实施。没有通过检查时不创建此凭据、不开放真实数据。

```bash
/tmp/wealthpilot-poc02-venv/bin/python pocs/poc02_wealth_mcp/server.py \
  --real-config /absolute/private/location/wealth-config.json \
  --access-evidence /absolute/private/location/access-evidence.json \
  --business-python /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python
```

`reader.py` 在单一 SQLite read transaction 内绑定已有服务的 session，调用 `wealth_service._current_totals`；以 `mode=ro`、`query_only`、SQLite authorizer 三层限制写入。不会调用有快照写入副作用的 `get_summary` / `init_db`。SQLite WAL 锁协调可能涉及 `-shm` 文件，不能将“无业务写入”描述为“OS 完全不访问侧文件”。复用私有函数是短期 PoC 耦合，服务签名变更需重新验证。

金额统计完全沿用财富总览核心净资产口径：养老金展示项和投资关联项由既有规则处理；不会重新加总 Portfolio 的杠杆负债。金额仅进行与 Web 一致的两位小数格式化，然后编码为整数分。`currency=CNY`、`amount_unit=minor`、`minor_unit_scale=2`。

`queried_at` 为请求时间，`data_updated_date` 为全部纳入组件日期已知时的最新估值日期，`oldest_component_date` 显示最旧已知日期。日期未知返回 null；Position.created_at 和财富快照记录时间不冒充行情更新时间。手工估值超 30 天、同步估值超 1 天为 PoC 新鲜度提示阈值，不代表投资业务规则。最近同步失败、过期、估值日期未知及 Tiger 现金/汇率降级明确列入 `issues`；现金来源失败时金额返回 null，避免静默零值被误认为完整财富。零现金与空来源在旧接口中无法区分时也保留提示。

不返回持仓、账户标识、资产名称、备注、凭证或源错误详情。金额与 Web 的真实比对结果仅记录相等/不等，不将真实金额写入仓库。可用 `--events` 将工具完成元数据记录到 Git 外，不记录财务 payload。

## 验证与停止

```bash
/tmp/wealthpilot-poc02-venv/bin/python -m pytest pocs/poc02_wealth_mcp/test_http.py -q
PYTHON_DOTENV_DISABLED=1 /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python -m pytest pocs/poc02_wealth_mcp/test_reader.py -q
```

所有自动化数据库都是临时合成库。真实验收分别由 Web 和真实 iOS 新请求发起，与服务端返回记录核对，比较同一组合、相同数据状态下现有 WealthPilot Web 三个 KPI；仅同步看到旧聊天不算 iOS 调用通过。

现有 Web 未启动时，可以使用验证专用的 `web_compare.py`：从真实库的只读连接备份到仓库外的 0700 临时目录，使用原有 `wealth_service.get_summary` 和 Tiger 现金读取计算一次参考值，并服务未修改的 `frontend/dist`。原接口的快照写入仅发生在临时副本。该程序只有 GET 路由，其他方法返回 405；不加载 `backend.main`、定时任务、同步或交易路由。它用于页面验收，不是正式 Web 后端，趋势范围固定为参考查询的 365 天。

```bash
# 在已有 frontend 依赖环境中先 npm run build
mkdir -m 700 /absolute/private/location/web-check
/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python pocs/poc02_wealth_mcp/web_compare.py \
  --config /absolute/private/location/wealth-config.json \
  --private-dir /absolute/private/location/web-check
```

打开 `http://127.0.0.1:8790/#/wealth`。仅核对财富总览，避免使用页面上的编辑/同步入口（验证服务不提供这些接口）。页面 KPI 按既有 `fmtCny` 四舍五入到元；与 ChatGPT 的精确两位小数金额按同一格式比较，底层 API 则逐分比较。临时库和 `web-summary.json` 含真实数据，只保存在该私有目录，不纳入 Git。

测试结束终止 MCP 和 Tunnel 前台进程；不安装 launchd，不部署生产。用户可在 Platform 撤销 PoC 专用运行 Key 和 Tunnel；不自动删除其他凭证、插件或配置。
