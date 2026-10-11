# WealthPilot 4.0 · Phase 1 / PoC 01：ChatGPT 私有 MCP 接入验证

审计与实验日期：2026-10-11。范围：一个独立 Python MCP Server、一个合成财富摘要只读工具。

## 1. 结论及决策边界

**PoC 01 最小合成查询接入 PASS：独立协议测试 11/11、HTTPS、ChatGPT Web 与 iOS App 实际调用通过。真实私有数据认证、Secure Tunnel 等扩展验证仍 BLOCKED。**

已证实：无需启动现有业务后端，即可使用官方 Python SDK 提供 Streamable HTTP，
完成握手、工具发现、结构化结果、金额契约、错误处理和恢复；临时 HTTPS 通道上
工具发现与调用成功。隔离实验没有观察到业务模块加载、数据库/凭证文件访问或服务器外连。

已证实：本用户账号有自定义 MCP 创建入口，个人合成测试插件创建后由用户完成连接，
插件详情显示“已连接”，新聊天输入框中出现该插件。
已证实：Chrome 两轮回复包含与 fixture 完全一致的 JSON、正确金额换算和合成数据声明；
第二轮页面新请求与隔离服务器函数返回记录对应，Web 合成查询通过。用户确认手机 App
能找到同一插件，并另起新对话发起请求返回工具结果；顺序重测时服务器新增函数
完成记录。手机为 iOS、ChatGPT App 1.2026.258，未提供 iOS 系统版本。
Secure MCP Tunnel 账号权限与工作区关联、OAuth/身份隔离仍未验证。
用户回复“已登录”，并明确是在 Codex 内置浏览器中的 ChatGPT 页面。
内置浏览器页面控制超时；随后用户提供可见首页截图，并授权换 Chrome 继续。
Chrome 已观察到登录后页面、自定义 MCP 创建表单、“已连接”详情页、选中插件的新聊天
和完整合成回复，服务端补证已通过。
此前 Chrome 网络错误是另一次环境尝试的结果，不能用于判断已登录的内置浏览器是否联网。

**本轮的“个人私有安装”与“网络及数据访问私有”需区分。** 匿名公网合成数据的成功
不证明实际财富数据可以安全迁移。该服务器可继续用作接入实验 fixture；当前结果
不足以决定 4.0 的生产访问方案，也不足以开放真实数据库或任何交易能力。

## 2. 基线、改动及隔离

| 项目 | 已证实事实 |
|---|---|
| 开发基线 | local `main`、`origin/main` 与 `git ls-remote origin refs/heads/main` 同为 `4da23b1868f3e6dae85a15383405aad3b15da5e3` |
| 短期分支 | 从该 main 创建 `codex/phase1-poc01-private-mcp`；没有迁移正式分支 |
| 原有工作 | 两份 Phase 0 报告为进入任务前的未跟踪文件；保留原内容 |
| 本轮文件 | `pocs/poc01_private_mcp/` 及本报告；没有修改已有跟踪业务代码 |
| Git 操作 | 没有提交、推送、合并、创建/移动 Tag |
| 外部状态 | 没有访问真实财富数据库、读取业务 .env、触碰券商账户或交易状态；没有生产部署 |

参考 [现状审计](WealthPilot_3x_Current_State_Audit.md) 和
[迁移影响初评](WealthPilot_4x_Migration_Impact_Assessment.md#6-关键技术-poc-与决策-gate)。
本轮只验证初评 P0-1 的合成查询入口与平台接入前提，没有实现 OAuth 或扩大到其他 PoC。

核心实现证据：

- [server.py:13](../pocs/poc01_private_mcp/server.py#L13)：Pydantic 结果契约，schema 版本、合成标记、来源、固定时间及整数分。
- [server.py:44](../pocs/poc01_private_mcp/server.py#L44)：唯一工具注册、只读/封闭数据提示、noauth 元数据。
- [server.py:57](../pocs/poc01_private_mcp/server.py#L57)：固定 fixture，无数据库、行情、LLM 或券商调用。
- [server.py:78](../pocs/poc01_private_mcp/server.py#L78)：独立 CLI、loopback 监听、Streamable HTTP、无状态、JSON POST、Host/Origin 白名单。
- [probe.py:12](../pocs/poc01_private_mcp/probe.py#L12)：官方 SDK Client 的 initialize 路径发现与调用，不依赖 WealthPilot 客户端。
- [test_http.py:28](../pocs/poc01_private_mcp/tests/test_http.py#L28)：实际 HTTP 子进程、临时工作目录、环境白名单、业务 import/文件访问/外连 fail-closed 守卫。

运行说明见 [PoC README](../pocs/poc01_private_mcp/README.md)。

## 3. 环境及本地实验

独立 venv：`/tmp/wealthpilot-poc01-venv`，不包含主项目 site-packages。
Python 3.11.13 / Darwin x86_64，官方 `mcp==2.3.0`，pytest 8.4.2，jsonschema 4.26.0。
虽然主机是 ARM，本实验运行的是 Intel Python；原生 macOS ARM 支持仍未验证。
既有业务环境的 MCP 版本与依赖没有更新。完整实验依赖列表见
[dependency-versions.txt](../pocs/poc01_private_mcp/evidence/dependency-versions.txt)。

首次安装解析到 cryptography 50.0.2，原生构建因 Intel/ARM 工具链架构冲突失败。
PoC 单独固定 cryptography 46.0.3，并使用 `--only-binary=:all:` 后安装成功；
`pip check` 输出 `No broken requirements found`。这是已解决的实验环境失败，
没有把它归因为业务回归或原生 ARM 验证通过。

实际测试命令（仓库根目录执行）：

```bash
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /tmp/wealthpilot-poc01-venv/bin/python -m pytest -c pocs/poc01_private_mcp/pytest.ini pocs/poc01_private_mcp/tests --basetemp=/tmp/wealthpilot-poc01-evidence --junitxml=/tmp/wealthpilot-poc01-junit.xml
```

结果：**11 passed in 1.62s，退出码 0**。证据：
[local-test-results.txt](../pocs/poc01_private_mcp/evidence/local-test-results.txt)。
测试不是只调用 Python 函数，而是在空闲 loopback 端口启动独立进程，经真实 HTTP 验证。

| 编号 | 状态 | 已执行实验与证据 |
|---|---|---|
| L01 | PASS | tools/list 恰好一个工具；输入 CNY、输出 JSON Schema、只读提示正确。`test_discovery_and_output_contract`；[contract.json](../pocs/poc01_private_mcp/evidence/contract.json) |
| L02 | PASS | synthetic/source/金额单位、各分类合计、资产减负债等于净资产、JSON 文本与 structuredContent 一致；默认参数重复调用稳定。同上测试与契约文件 |
| L03 | PASS | USD、整数 currency、未知 `place_order`（3 个用例）均 `isError=true` 且无结构化财富结果；不暴露 Traceback，有效调用随后恢复。`test_tool_errors_and_recovery` |
| L04 | PASS | 手写 JSON-RPC initialize 协商 `2025-06-18`，HTTP 200 JSON；initialized 通知 202；发现/调用成功；未知方法返回 `-32601`。`test_legacy_initialize_and_json_rpc`；[wire.json](../pocs/poc01_private_mcp/evidence/wire.json) |
| L05 | PASS | 畸形 JSON 400、错误 Accept 406、错误 Content-Type 400、非法 Host 421、非法 Origin 403（5 个用例）；每次错误后有效发现恢复。`test_http_errors` |
| L06 | PASS | `/api/health`、`/api/portfolio/summary`、旧 `/sse` 均 404；SDK 的 `/mcp` GET SSE 为 200。`test_no_business_routes_or_legacy_sse` |
| L07 | PASS | 测试服务器尝试业务 import、受保护文件访问或出站 connect 会中止；本次记录全空。fixture teardown 断言；[isolation.json](../pocs/poc01_private_mcp/evidence/isolation.json) |
| L08 | PASS | 独立 `probe.py` 子进程经 HTTP 完成发现/调用，退出 0、status PASS，同样运行隔离守卫；[local-probe.json](../pocs/poc01_private_mcp/evidence/local-probe.json) |
| H01 | PASS | SDK 客户端通过真实临时 HTTPS → Cloudflare → loopback PoC 发现并调用；[https-probe.json](../pocs/poc01_private_mcp/evidence/https-probe.json)。客户端仍在本机，未验证另一个网络环境 |
| H02 | PASS | HTTPS 上 USD 请求返回 `isError=true`、无 structuredContent，随后有效调用恢复；[https-error-recovery.json](../pocs/poc01_private_mcp/evidence/https-error-recovery.json) |

开发中曾修正测试对 SDK Client mode、Content-Type 状态码及开放 GET SSE 的错误预期，
最终以上表格以通过的测试结果为准；没有为满足错误预期修改业务架构。

隔离结论的范围：守卫保护的是实验服务器进程，客户端和 tunnel 需要联网。
结果说明这个合成服务没有越界，不构成整个旧系统的安全审计。

未运行 `m5_e2e_18_cases.py`。项目约定要求业务代码改动跑 M5，但本轮用户明确要求
不启动完整 FastAPI 业务后端，且没有修改既有业务代码。遵循本轮约束，只运行独立 PoC
测试；不能把 Phase 0 的 M5 结果当作本轮重新通过，也不能宣称满足主项目合并 Gate。

## 4. HTTPS 通道及其限制

使用本机已安装的 cloudflared 2026.6.1，仅转发 `http://127.0.0.1:8787`。
配置为 `/dev/null`、独立不存在的 origin cert 路径、HTTP/2、不自动更新；没有使用
生产域名、账户 Tunnel、业务端口或 API 凭证。服务器仍仅监听 127.0.0.1，
放行该次随机 hostname，不关闭 DNS rebinding 防护。

这是无认证、短期公网合成 fixture。知道 URL 的人可访问，不等价于真正私网。
官方指出 Quick Tunnel 用于测试、不保证可用性且不支持 SSE；本轮 H01 仅证明
Streamable HTTP 的 JSON POST 发现/调用路径。服务器的可选 GET SSE 本地通过，
不能由此宣称 Quick Tunnel 流式路径通过。
[Cloudflare 官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

Secure MCP Tunnel 是另一候选：官方支持把本地 HTTP MCP 通过出站连接提供给
ChatGPT；需要 Platform Tunnel 角色、运行凭证和对应 ChatGPT 工作区关联。
本轮没有该账号权限/关联的实测证据，没有创建平台凭证或把密钥写入仓库。
[OpenAI Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

两轮临时 cloudflared 与 PoC 服务器均已停止；第二轮账号验收后向准确识别的测试
进程发送 SIGTERM，cloudflared 会话退出 0、服务器会话退出 143。8787 连接检查
确认无监听，最终隔离记录全空；[清理证据](../pocs/poc01_private_mcp/evidence/cleanup.json)。
没有注册常驻或开机服务。随机地址已失效，不作为可持续使用入口。
账号测试插件未删除；尝试进入其管理页断开，但界面停在“正在加载设置”，未证实
账号连接已断开。保留安装条目不会使已停止的端点继续服务，后续可由正常界面断开。

## 5. ChatGPT Web 实验、授权与阻塞

【官方文档事实】当前流程为 ChatGPT Plugins 添加自定义 MCP，Connection 选 Server URL
或 Tunnel，配置认证，创建并安装个人/工作区插件，再在对话中选中。
支持 streaming HTTP；合成公开只读信息可采用 No authentication。
[自定义 MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server)、
[连接 ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)

【已证实】本个人账号出现上述自定义接入界面，并已连接合成测试插件。
【待验证】具体套餐、工作区管理策略及 Secure Tunnel 权限；没有为确定套餐额外读取账号敏感信息。
不能用入口可用推断所有认证/管理能力均可用，也不能根据旧文档断言必须用某个历史入口。

| 编号 | 状态 | 本轮观察及证据 |
|---|---|---|
| W00 | FAIL（历史环境尝试，已恢复） | 初次 Chrome 访问出现 `ERR_TIMED_OUT`；后续用户配合后已能读取登录后页面。此前失败不再作为当前 Web 网络阻塞 |
| W01 | BLOCKED | 用户明确已登录 Codex 内置浏览器；标签元数据显示 `ChatGPT / https://chatgpt.com/`。页面绑定、DOM 内容及截图操作均超时，未获取自定义 MCP 入口与套餐/工作区证据。[补验记录](../pocs/poc01_private_mcp/evidence/web-browser-check.json) |
| W02 | PASS | Chrome 中可见创建入口及 URL / 隧道、三种认证选项；选择 No authentication 创建个人合成测试插件，用户完成连接，详情显示“已连接”；“在聊天中试用”后新聊天显示该插件。[Web 观察](../pocs/poc01_private_mcp/evidence/chatgpt-web-observation.json) |
| W03 | PASS | Chrome CNY 请求及重测均显示正确 JSON、100 万 / 10 万 / 90 万元及分→元解释。重测期间服务器直接记录函数完成和固定结果；[服务端调用](../pocs/poc01_private_mcp/evidence/chatgpt-web-server-call.json)、[页面摘录](../pocs/poc01_private_mcp/evidence/chatgpt-web-response-ax.txt)、[回复契约](../pocs/poc01_private_mcp/evidence/chatgpt-web-response.json) |
| W03b | BLOCKED（未单独执行） | ChatGPT 对话中的非法 USD 提示及恢复未测；服务器错误路径已由 L03/H02 通过。不能用服务器测试宣称模型/平台错误展示通过 |
| W04 | BLOCKED | Secure Tunnel 权限、工作区关联、运行凭证尚无账号授权与实验结果；未以匿名 HTTPS 结果代替 |
| M01a | PASS（用户实测报告） | 用户说明手机 App 能找到该插件、与 PC Web 同步；尚无 App/OS 版本，范围仅限插件可见 |
| M01b | PASS（用户操作 + 服务端观察） | iOS ChatGPT App 1.2026.258；用户明确另起新对话发起并返回工具结果，随后手机重测对应第三条函数完成记录。未自动控制手机或独立截取手机 JSON；[手机观察](../pocs/poc01_private_mcp/evidence/chatgpt-phone-observation.json)、[服务器返回](../pocs/poc01_private_mcp/evidence/chatgpt-phone-server-call.json) |
| M02 | BLOCKED（未覆盖环境） | iOS 系统版本未提供；Android、其他 App 版本、账号套餐和工作区策略没有实测。M01 不推广到这些环境 |

W00 的截图在本机 `/tmp/wealthpilot-poc01-chatgpt-web-blocked.png`，已在当前会话展示；
截图可能包含浏览器周边信息，未放入仓库。Chrome 没有改代理、证书或网络配置。
没有把自动化超时解释为账号被拒绝，也没有改变安全设置绕过阻塞。

2026-10-11 补验：在用户说明登录浏览器后，重新检查同一内置浏览器。
`tabs.list` 成功获取 ChatGPT 标签标题和首页 URL；页面绑定、DOM snapshot 和
截图分别在 15 秒内未返回。按浏览器排障说明尝试不同已文档化读取方式后，
仍没有可操作的界面证据。这一轮没有重新要求登录或创建账号连接；
随后按用户授权转到 Chrome 完成后续账号实验。

后续 Chrome 实验：用户提供的内置浏览器截图显示 ChatGPT 首页和账号相关内容。
经用户授权换 Chrome 后，已读取登录后首页与 Plugins → 添加 → 添加自定义 MCP 服务器。
表单支持 URL / Tunnel，以及 OAuth / No authentication / mixed 三种选择。
以第二条临时 HTTPS 通道创建合成测试插件（探针先通过），平台进入“连接”确认页，
插件出现在已安装侧边栏。点击/键盘操作未使确认页变化，坐标操作返回窗口不可用；
因此请求用户完成可见“连接”按钮。用户回复“已连接”，随后读取的详情页与用户截图
均有“已连接”标识；“在聊天中试用”进入新聊天，输入框包含该插件。
自动粘贴/赋值未写入测试文本，用户随后发送明确合成 CNY 请求，并贴出完整回复。
Chrome AX 读到了同一回复；提取 JSON 后与本地 fixture 逐字段比较相等。
服务器新增一条 HTTP 200 POST，但普通访问日志没有 JSON-RPC method；该日志只能
说明请求到达，不能独立证明 tools/call。平台工具轨迹未导出，模型自述不单独计作证据。
因此在仓库外使用临时 sitecustomize/profile 观察器，继承原隔离守卫，只记录该 fixture
函数完成时的 CNY 参数及合成结果，不修改 server.py 或业务模块。控制探针先产生一条
记录；随后停止探针，在独立时间窗请用户 Chrome 重测，新增第二条函数完成记录
与 AX 的新用户请求及结果对应，结果逐字段一致。随后明确请用户手机重测，用户
回复“手机已发送”，新增第三条函数完成记录；期间没有独立客户端探针请求。
三条记录的时间分别为 04:59:25Z（控制）、04:59:58Z（Web）、05:01:18Z（手机）。
当前通道匿名，时间窗对应不是
认证身份或请求 ID 追踪；也不是完整的平台 trace 导出。
[观察器方法](../pocs/poc01_private_mcp/evidence/runtime-observer-sitecustomize.py)、
[控制实验](../pocs/poc01_private_mcp/evidence/runtime-observer-control.json)、
[对应记录](../pocs/poc01_private_mcp/evidence/chatgpt-runtime-observation.json)。
截图保存在本机临时路径并已在会话展示，未写入仓库。

账号动作及授权范围：

| 动作 | 谁需要参与 | 本轮情况 |
|---|---|---|
| ChatGPT 登录 | 用户完成已有账号登录 | 用户表示已登录；未读取/处理密码或 Token |
| 查看自定义接入入口与资格 | 可读界面验证；套餐/工作区缺证时由用户说明 | 入口 PASS；具体套餐与管理员策略未查，不影响当前合成插件创建事实 |
| 开启开发者/自定义 MCP 权限、管理员策略 | 如账号要求，需用户/管理员授权；不能默认扩大访问 | 未执行 |
| 创建并安装个人合成测试插件 | 用户账号的测试连接，记录准确授权/提示 | 已创建；用户完成“连接”确认，UI 已证实，未使用 OAuth 或凭证 |
| Secure Tunnel 创建、关联、运行凭证 | Platform/ChatGPT 工作区对应权限及用户本地凭证配置 | 未执行；不要经聊天传递凭证 |
| OAuth 授权 | 后续真实私有数据场景需用户授权且服务器验证身份/权限 | 本轮 noauth，不实现、不宣称通过 |

【合理推断】HTTP 服务与现有业务分离在工程上可行；本账号个人插件安装和合成
调用已实测。真实数据的身份链仍是迁移 Gate。SDK 注解与平台提示不承担后端访问控制。
真实私有用户数据需另行设计并验收 OAuth/令牌校验，不能沿用本 PoC 的 noauth。
[OpenAI 认证文档](https://developers.openai.com/plugins/build/auth)

## 6. 手机支持限制

【官方文档事实】账号可用的插件可在 Chat/Work 的 Web、桌面和手机使用；
标为 Desktop only 的插件不在手机可用范围。
[Plugins 文档](https://learn.chatgpt.com/docs/plugins)

【官方文档事实】通过 `mcp.json` / `.mcp.json` 导入并声明 MCP 服务器的插件会被标为
Desktop only，即使服务器是远程 HTTPS。引用已登记应用的 `.app.json` 是另一种机制，
且不能自行授予用户权限。
[Plugin management](https://learn.chatgpt.com/docs/enterprise/plugin-management)

【已证实（用户实测报告）】本 PoC 采用账号内自定义 MCP 注册后可在用户手机找到，
用户称与 PC Web 同步。用户提供 iOS / ChatGPT App 1.2026.258；iOS 系统版本未提供。
【已证实（用户实测报告）】用户明确从手机 App 另起新对话发起，能返回工具结果。
【已证实】观察器下的手机重测新增 fixture 函数返回，结果逐字段符合契约；由用户操作
实机、报告成功，与服务器顺序时间窗结合验收。没有独立捕获手机渲染的 JSON，
没有认证的客户端身份或请求 ID；此限制保留在证据文件中。
本轮没有创建导入型 plugin manifest，没有由自动化控制手机端或使用模拟手机测试。
因此既不宣称手机全部支持，也不宣称手机全部不支持。Codex Remote/连接主机、
桌面原生客户端或移动网页均不计作 ChatGPT 手机 App 实证。

本次通过只适用于本账号的远程 HTTP 自定义 MCP 注册和上述 iOS App。
官方的 Desktop only 导入型 MCP 插件限制仍适用；本轮未测试该路径、Android、
其他套餐/工作区、手机端创建插件、OAuth 或后台持续调用。Codex Remote 不计作手机实证。

## 7. 迁移决策与后续待验证项

本轮最小目标已完成：保留独立专业服务、通过标准 MCP 暴露结构化只读工具，
由 ChatGPT Web 与真实手机 App 发起请求，在工程与本账号接入上均可行。
结果支持进入下一项有边界的身份认证/访问控制 PoC；没有授权接入真实资产数据。

| 后续问题 | 当前证据及决策影响 |
|---|---|
| 真实私有身份及数据权限 | BLOCKED：noauth 无身份校验。需用户 OAuth 授权、服务器令牌/作用域验证及拒绝访问实验；个人安装不能代替访问控制 |
| Secure MCP Tunnel | BLOCKED：没有 Platform Tunnel 权限、工作区关联或运行凭证实验。匿名 Cloudflare 结果不能替代 |
| 常驻 macOS ARM 与稳定 HTTPS | BLOCKED：当前 Intel Python 与短期 Quick Tunnel。没有原生 ARM、开机启动、7×24、恢复或稳定域名验证 |
| ChatGPT 错误展示及恢复 | BLOCKED：本地/HTTPS 服务错误路径 PASS，但没有单独的 ChatGPT USD 对话验收；模型可能在 schema 层拒绝而不调用服务器 |
| 手机其他环境 | BLOCKED：Android、其他 App/OS/账号与导入型 Desktop only 插件未测；不能泛化 M01 |
| 流式响应 | 本地 SDK GET SSE PASS；Quick Tunnel 不支持 SSE。本轮只证明标准 Streamable HTTP 的 JSON POST 工具调用 |

代码、运行说明、11 项测试输出、契约、Web 页面摘录和 Web/手机服务端返回均在
独立 PoC 目录。测试进程已关闭；账号插件安装条目仍保留，自动断开未证实。
本轮没有扩展到真实财富服务、PEER 替换、SkillsLoader 改造、投资建议或交易工具，
没有提交、推送、main 合并或生产部署。
