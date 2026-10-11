# PoC 01：ChatGPT 私有 MCP 接入

独立 Python 服务器，仅提供 `get_synthetic_wealth_summary` 一个只读工具。
使用官方 Python MCP SDK 的 Streamable HTTP，地址为 `/mcp`，默认监听
`127.0.0.1:8787`。不导入 WealthPilot 业务模块，不读取配置、数据库或账户，
不调用 LLM、数据源或券商。结果来自固定合成 fixture。

## 独立运行

需要 Python 3.11+。以下命令从本目录执行；虚拟环境放在仓库外。
不使用项目主环境，不启动 `backend.main:app`。

```bash
python3.11 -m venv /tmp/wealthpilot-poc01-venv
/tmp/wealthpilot-poc01-venv/bin/python -m pip install --only-binary=:all: -r requirements-dev.txt
/tmp/wealthpilot-poc01-venv/bin/python -m pip check
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /tmp/wealthpilot-poc01-venv/bin/python server.py
```

另一个终端，在同一目录执行：

```bash
/tmp/wealthpilot-poc01-venv/bin/python probe.py
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /tmp/wealthpilot-poc01-venv/bin/python -m pytest -c pytest.ini tests --basetemp=/tmp/wealthpilot-poc01-evidence
```

`probe.py` 返回工具元数据、结构化结果和 `status: PASS`；非正常发现或调用退出非零。
测试自行启动/停止隔离服务器并选择空闲端口，不需要先运行服务器。
`--basetemp` 指定的目录会被 pytest 重建，只使用专门的临时目录。
完成后在手动运行服务器的终端按 Ctrl+C；不注册后台服务或开机启动。

运行依赖：`mcp==2.3.0`、`cryptography==46.0.3`。
后一版本用于本机 Intel Python 的二进制 wheel 兼容性；本轮安装没有编译原生扩展。
本机验证为 Python 3.11.13 / Darwin x86_64（ARM 主机上的 Intel Python），
不构成原生 macOS ARM 环境验证。实际解析依赖版本见 `evidence/dependency-versions.txt`。

## 数据契约

输入可省略，默认 `{"currency":"CNY"}`。只支持 CNY，不进行换汇。
发现结果包含 `inputSchema` / `outputSchema`，返回同时含 `structuredContent`
与等价 JSON 文本。所有金额为整数分：`minor_unit_scale=2`，100 分 = CNY 1 元。

| 字段 | 合成 fixture |
|---|---|
| schema_version | `poc01.wealth-summary.v1` |
| synthetic / source | `true` / `hardcoded_fixture` |
| as_of | `2026-01-01T00:00:00Z`（固定日期，不代表当前资产） |
| total_assets_minor | 100,000,000（100 万元） |
| total_liabilities_minor | 10,000,000（10 万元） |
| net_worth_minor | 90,000,000（90 万元） |
| assets | cash 20 万、fixed_income 40 万、equity 30 万、other 10 万 |

`notice`、工具描述和服务器 instructions 都标明合成数据。
`readOnlyHint=true` / `openWorldHint=false` 是客户端提示，不能代替权限控制。

错误路径由 SDK 提供：非法 currency / 类型、未知工具返回 `isError=true`，
不返回结构化财富结果；未知 JSON-RPC 方法返回 `-32601`。
畸形 JSON / 错误 Content-Type 返回 HTTP 400，错误 Accept 返回 406，
非白名单 Host 返回 421，非白名单 Origin 返回 403；错误后有效调用仍可成功。
POST 返回 JSON 是标准 Streamable HTTP 的一种模式。SDK 同时支持 `/mcp` GET SSE，
但没有旧式 `/sse` 路由；本轮 HTTPS 验证仅覆盖 JSON POST 路径。

## ChatGPT Web 接入与授权

截至 2026-10-11 的官方文档使用 Plugins → 添加自定义 MCP → Server URL / Tunnel
→ 认证方式 → 创建并安装个人插件 → 新对话中选中插件的流程。
实际账号可能显示不同入口，应记录真实界面，不能仅凭文档或登录状态判为通过。
参考 [自定义 MCP 接入](https://developers.openai.com/api/docs/guides/custom-mcp-server)
和 [连接 ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)。

本 PoC 的 `No authentication` 只适合公开合成数据。个人安装、未上架并不意味着
HTTP 端点仅该账号可访问。本轮没有验证 OAuth、租户隔离或真实私有金融数据访问。

### 方式一：Secure MCP Tunnel（需账号权限，尚未实测）

官方支持本地 HTTP 服务经 Secure MCP Tunnel 接入；不需要本地入站公网端口。
用户需在 OpenAI Platform 侧拥有相应 Tunnel 权限、建立与 ChatGPT 工作区的关联，
并在自己的终端配置 tunnel-client 使用的凭证。HTTP origin 使用
`http://127.0.0.1:8787/mcp`。按当前官方客户端帮助及 quickstart 配置，不把
API Key、Token、Tunnel 凭证粘贴进聊天或报告。
见 [Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。

### 方式二：临时公网 HTTPS（仅合成 PoC）

本机已有 `cloudflared`；以下命令只转发 PoC 的端口，不转发 8000 或其它业务服务：

```bash
cloudflared --config /dev/null tunnel --no-autoupdate --origincert /tmp/wealthpilot-poc01-unused.pem --protocol http2 --url http://127.0.0.1:8787 --http-host-header localhost:8787
```

取命令输出的随机 HTTPS 地址，接入 URL 为 `https://<随机主机>/mcp`。
可在启动服务器时增加 `--allowed-host <随机主机>`（不带 scheme/path），
明确放行该 hostname/HTTPS Origin；不关闭 DNS rebinding 防护。

```bash
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /tmp/wealthpilot-poc01-venv/bin/python server.py --allowed-host <随机主机>
/tmp/wealthpilot-poc01-venv/bin/python probe.py --url https://<随机主机>/mcp
```

此通道是匿名公网测试入口，知道 URL 的人可访问合成摘要。停止 cloudflared 后失效。
Quick Tunnel 不保证可用性，官方声明不支持 SSE；成功的 JSON POST 探针不能证明
全部流式能力。见 [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。
不使用其交互式邮箱验证保护 MCP（机器客户端无法完成该流程）。

### 用户账号参与的步骤

1. 登录 ChatGPT 并确认账号/工作区有自定义 MCP 权限。需要开发者模式或管理员
   放行时，由用户授权相应安全设置；本轮没有擅自调整工作区策略。
2. 创建/安装名称为 `WealthPilot PoC 01 (synthetic only)` 的个人测试插件，
   Connection 选择可用通道，认证选择 No authentication。
3. 确认仅合成数据的非绑定风险提示；如出现服务条款、权限扩大或 OAuth 同意，
   需用户完成相应账号授权。当前工具没有 OAuth 流程。
4. 新建对话，选中此插件，输入：“调用 get_synthetic_wealth_summary，
   币种 CNY。明确说明为合成测试数据，列出总资产、负债、净资产及单位。”
5. 保存工具调用的实际结果证据；应为 100 万 / 10 万 / 90 万元，不能仅凭模型
   文字或插件创建成功判 PASS。再请求 USD 并记录错误处理。
6. 完成后移除测试插件/连接（按界面正常可恢复操作），停止服务器和转发进程。

## 手机 App 验收

官方说明账号可用的插件可在 Chat/Work 的 Web、桌面和手机使用；同时，通过
`mcp.json` / `.mcp.json` 导入并声明服务器的插件，即使 URL 是远程 HTTPS，也属于
Desktop only。这两种接入不能混为一谈。见
[Plugins](https://learn.chatgpt.com/docs/plugins) 和
[Plugin management](https://learn.chatgpt.com/docs/enterprise/plugin-management)。

用户已报告真实手机 App 能找到同一插件，与 PC Web 同步；发现入口有用户实测证据。
用户从 iOS ChatGPT App 1.2026.258 另起新对话发起 CNY 请求并确认返回工具结果；
顺序重测对应服务器第三条 fixture 返回，实际调用为 PASS。iOS 系统版本未提供，
手机渲染的 JSON 未单独捕获；Android、其他版本与账号环境未验证。
不能用桌面 App、Codex Remote、移动浏览器或模拟 viewport 替代验收。
证据见 evidence/chatgpt-phone-observation.json 与 chatgpt-phone-server-call.json。
用户实机操作和服务端时间窗记录共同构成本次证据；文档支持说明本身不计 PASS。

## 本轮结果与已留存证据

独立测试 11/11 PASS、HTTPS PASS、ChatGPT Web 与上述 iOS App 合成调用 PASS。
临时服务和 HTTPS 通道已停止，8787 已无监听；账号插件条目保留，自动断开未证实。
真实数据 OAuth、Secure Tunnel、原生 ARM 常驻与其它手机环境仍未验证。

`evidence/` 仅含合成结果和隔离测试证据；完整结论见
`../../docs/WealthPilot_4x_Phase1_PoC01_Private_MCP_Report.md`。
测试没有运行主项目评测脚本，因为本轮明确禁止启动完整业务后端。
不声明业务质量 Gate 或正式合并验收通过。
