"""
PUBLIC_DEMO_MODE 核心配置。

公开 demo 部署时的安全控制中心。所有外部连接、交易路径、凭证访问
的短路判断都通过此模块的常量进行。

设计原则：
- PUBLIC_DEMO_MODE 出生即开（默认 True），公开版永不改 False
- 显式设 PUBLIC_DEMO_MODE=false 才关闭（仅开发调试用）
- DEMO_ALLOW_MARKET_DATA 控制行情放行（AKShare/AV 免费源）
"""
import os

# ── 主开关 ──────────────────────────────────────────────────────
PUBLIC_DEMO_MODE: bool = (
    os.environ.get("PUBLIC_DEMO_MODE", "true").lower() != "false"
)

# ── 行情数据放行开关 ────────────────────────────────────────────
# PUBLIC_DEMO_MODE 下，仅当此开关为 True 时放行 AKShare/AV 行情报价+K线
# 为 False 时回到全静态种子数据
DEMO_ALLOW_MARKET_DATA: bool = (
    os.environ.get("DEMO_ALLOW_MARKET_DATA", "true").lower() != "false"
)

# ── 访问密码 ────────────────────────────────────────────────────
DEMO_ACCESS_PASSWORD: str = os.environ.get("DEMO_ACCESS_PASSWORD", "")

# ── 启动断言: demo 模式必须配密码，不允许裸奔 ────────────────────
if PUBLIC_DEMO_MODE and not DEMO_ACCESS_PASSWORD:
    raise RuntimeError(
        "PUBLIC_DEMO_MODE=true 但 DEMO_ACCESS_PASSWORD 未设置。"
        "公开 demo 必须配置访问密码，拒绝以无密码状态启动。"
        "请设置环境变量 DEMO_ACCESS_PASSWORD=<你的密码>"
    )


class DemoModeError(Exception):
    """PUBLIC_DEMO_MODE 下试图访问被禁止的路径时抛出。"""
    pass


def assert_not_demo(context: str = "") -> None:
    """断言当前不在 demo 模式。用于防御性保护。"""
    if PUBLIC_DEMO_MODE:
        raise DemoModeError(
            f"PUBLIC_DEMO_MODE 禁止此操作"
            f"{f': {context}' if context else ''}"
        )


def assert_no_credentials(context: str = "") -> None:
    """断言不应读取真实凭证。demo 模式下读凭证即 raise。"""
    if PUBLIC_DEMO_MODE:
        raise DemoModeError(
            f"PUBLIC_DEMO_MODE 禁止读取真实凭证"
            f"{f': {context}' if context else ''}"
        )
