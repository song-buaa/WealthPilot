# 隔离开发与检查

本文件负责本地命令与测试隔离；规则及风险矩阵见[治理文档](PROJECT_GOVERNANCE.md#9-标准开发流程与风险分级)，架构见[当前架构](ARCHITECTURE_CURRENT.md)，复用入口见[能力索引](CAPABILITY_REGISTRY.md)。MacBook Dev / Mac Mini Prod 的发布步骤统一放在治理文档，不复制一套部署流程。

## 文档任务

仓库根执行，标准库 Python 即可，不导入业务、不启动服务：

```bash
python scripts/check_engineering_docs.py
git diff --check
```

随后按任务授权创建 PR，检查 exact HEAD 的全部四项 CI。仅文档任务不连接 Mini、券商或 Provider。

## 代码任务的离线基线

使用项目 Python 3.11 环境（本机已有环境可用 `/Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python`，其他设备自行创建，不依赖该绝对路径）。先安装 requirements；使用任务专用临时库，不复制用户真实 DB 或 .env。

```bash
export PYTHON_DOTENV_DISABLED=1
export WEALTHPILOT_DB_PATH="$(mktemp -d)/wealthpilot-test.db"
export PUBLIC_DEMO_MODE=true DEMO_ACCESS_PASSWORD=local-offline-only
export DEMO_ALLOW_MARKET_DATA=false BROKER_MODE=mock AV_DEV_MOCK=1
export IBKR_READ_ONLY_MODE=true ENABLE_IBKR_LIVE_TRADING=false
export ENABLE_TIGER_LIVE_TRADING=false FUTU_READ_ONLY_MODE=true
python -m pytest
python scripts/m5_e2e_18_cases.py --mode offline
cd frontend
npm ci
npm run lint
npm run build
```

在没有外部凭证的干净 shell 中运行；以上开关不证明所有新代码均隔离，测试作者仍须使用 mock/冻结 Provider。Offline M5 自带环境白名单、临时库/固定 Seed 和 socket guard，18/18 是代码 Gate；默认报告在系统临时目录。真实 integration marker / Live M5 不在默认测试授权中。专项 MCP、同步与部署 guard 的独立测试环境以[现有 CI](../.github/workflows/ci.yml)为准；`pytest.ini` 的 testpaths 不覆盖所有这些目录，不能用一次根 pytest 冒充全覆盖。

旧备忘曾要求 NO_PROXY / 等待在线后端；那属于历史在线 M5。默认 Offline M5 不依赖 localhost 服务、VPN 或真实 Provider。只在授权的本地 HTTP 调查中按需设置 `NO_PROXY=127.0.0.1,localhost`；不为离线测试修改用户全局代理配置。

## 启动本地 UI

先确认临时 DB、Demo/Mock、外部 Provider 关闭；Demo 需按原[Seed loader](../backend/services/demo_seed_loader.py)准备固定 Seed。启动命令见 [README](../README.md#3-启动开发应用)。非 Demo `backend.main` 会启动同步调度、OrderPoller、孤儿扫描和触发评估，不能用来做无副作用检查。不要复制这些 Dev 命令去替换 Mini 的 launchd 配置；生产安装/切换须单独授权。


## SEC 官方来源开发配置

MacBook和Mac Mini各自使用独立私有配置，不共享文件系统。生产Worker继续通过原 `load_analysis_environment()` 允许名单从其私有analysis-env加载；内容批准不代表Dev自动可访问Mini配置。本地直接CLI的标准文件是 `~/.config/wealthpilot/sec-identity.json`，目录0700、文件0600、归当前用户、拒绝符号链接，仅含两个JSON字符串字段 `WEALTHPILOT_SEC_ORGANIZATION` 与 `WEALTHPILOT_SEC_CONTACT_EMAIL`。不写示例身份，不提交真实值/私钥/Token/完整环境文件。

新电脑先执行（项目Python3.11）：

```sh
python scripts/sec_dev.py status
python scripts/sec_dev.py configure
python scripts/sec_dev.py run -- python your_authorized_sec_collection.py
```

`configure`在本机Terminal隐藏输入真实组织名和联系邮箱，独占创建文件，不覆盖既有配置。不得把真实信息粘贴聊天。`status`只显示PRESENT/MISSING/INVALID及路径；不是邮箱所有权证明。新Codex先检查此入口与状态，PRESENT就复用，MISSING/INVALID给本地初始化/修复方案，不重复询问同一字段。

标准库包装器只向这次明确选择的开发子进程注入两个字段，并禁dotenv；不改全局shell、launchd、Mini或其他配置。原 `request_identity()` 的格式/占位符拒绝、SEC白名单、4次/秒、超时与禁止重定向不变。原Worker可显式调用 `load_analysis_environment(path, sec_identity_only=True)`复用同一窄加载器；未指定此模式的生产加载语义不变。

若仅Mini存在有效身份，只能在获授权且已核实的个人连接中安全传递这两个字段，不复制完整runtime-env或使用公司SSH身份；个人连接不可用则保留本地Terminal初始化方案，不虚构身份。不为本任务调整生产服务。

诊断须区分：MISSING/INVALID或 `identity_unconfigured` 表示本地配置不满足、请求未发送；HTTP403表示服务器拒绝；proxy_connection_error/dns_error/tls_error/timeout为传输层；HTTP200后data_missing/解析缺字段才是具体来源资格缺口。HTTP200不等于财务事实合格，更不获得交易/生产权限。不要用网络失败泛称系统没有SEC能力。
