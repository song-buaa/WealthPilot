# 开发环境配置备忘

## M5 脚本运行环境要求

由于 Clash Verge 等代理工具默认会代理所有 HTTP/HTTPS 请求（包括 localhost），
运行 M5 脚本时必须显式设置 NO_PROXY 绕过本地后端：

```bash
NO_PROXY=127.0.0.1,localhost AV_DEV_MOCK=1 python scripts/m5_e2e_18_cases.py
```

或者在 `~/.zshrc` 中永久设置：

```bash
export NO_PROXY=127.0.0.1,localhost
```

另：脚本 timeout=180 秒（v3.6.5 调整，原 60 秒在 VPN + OpenAI 链路下不够）。
