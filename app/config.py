"""
WealthPilot - 全局配置常量
所有硬编码的业务参数、阈值、模型名称统一在这里管理。
修改配置只需改此文件，不需要翻业务代码。
"""

# ── 分析引擎 ─────────────────────────────────
# 策略偏离告警阈值（百分点），超过此值才产生告警
DEVIATION_THRESHOLD: float = 5.0

# 高严重度的偏离阈值（百分点）
HIGH_SEVERITY_THRESHOLD: float = 15.0

# ── AI 模型 ──────────────────────────────────
# 完整报告使用能力更强的模型
AI_REPORT_MODEL: str = "gpt-4.1-mini"

# 单条告警解读使用轻量模型，节省 token
AI_ALERT_MODEL: str = "gpt-4.1-nano"

# AI 输出的最大 token 数
AI_REPORT_MAX_TOKENS: int = 2000
AI_ALERT_MAX_TOKENS: int = 500

# AI 温度（越低越确定性，金融分析建议保持低温度）
AI_TEMPERATURE: float = 0.3

# 投研观点模块：AI 解析研报用较强模型，保证结构化质量
AI_RESEARCH_MODEL: str = "gpt-4.1-mini"
AI_RESEARCH_MAX_TOKENS: int = 2000

# ── UI 展示 ──────────────────────────────────
# 告警严重程度对应的图标
SEVERITY_ICONS: dict = {
    "高": "🔴",
    "中": "🟡",
    "低": "🟢",
}

# 各大类资产的说明示例
ASSET_CLASS_EXAMPLES: dict = {
    "货币": "余额宝、货币基金、活期存款、现金",
    "固收": "债券基金、银行理财、国债、信托",
    "权益": "股票、股票型基金、指数ETF",
    "另类": "黄金、REITs、大宗商品、私募股权",
    "衍生": "期权、期货、可转债",
}
