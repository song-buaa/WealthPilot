"""
WealthPilot - AI 投顾解读模块
调用LLM对分析结果进行自然语言解读
"""

import json
import os
from typing import List, Optional
from openai import OpenAI
from app.analyzer import BalanceSheet, DeviationAlert
from app.config import (
    AI_REPORT_MODEL, AI_ALERT_MODEL,
    AI_REPORT_MAX_TOKENS, AI_ALERT_MAX_TOKENS,
    AI_TEMPERATURE,
)

# 懒加载：避免应用启动时就因为没有 API Key 而失败，
# 也方便未来支持用户在 UI 里动态传入 key。
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "未找到 OPENAI_API_KEY 环境变量。\n"
                "请执行：export OPENAI_API_KEY='sk-your-key'"
            )
        _client = OpenAI(api_key=api_key)
    return _client


def generate_portfolio_analysis(
    balance_sheet: BalanceSheet,
    alerts: List[DeviationAlert],
    target_allocation: dict,
) -> str:
    """
    基于资产负债表和告警信息，生成自然语言的投资分析报告
    """

    # 构建结构化的分析数据
    analysis_data = {
        "资产负债概览": {
            "总资产": f"{balance_sheet.total_assets:,.0f} 元",
            "总负债": f"{balance_sheet.total_liabilities:,.0f} 元",
            "净资产": f"{balance_sheet.net_worth:,.0f} 元",
            "杠杆率": f"{balance_sheet.leverage_ratio}%",
        },
        "当前资产配置": {
            "权益": f"{balance_sheet.equity_pct}%",
            "固收": f"{balance_sheet.fixed_income_pct}%",
            "现金": f"{balance_sheet.cash_pct}%",
            "另类": f"{balance_sheet.alternative_pct}%",
        },
        "目标资产配置": target_allocation,
        "持仓集中度_TOP5": {
            k.split(":", 1)[1]: v
            for k, v in sorted(balance_sheet.concentration.items(), key=lambda x: x[1], reverse=True)[:5]
        },
        "平台分布": {k: f"{v:,.0f} 元" for k, v in balance_sheet.platform_distribution.items()},
        "风险告警": [
            {
                "类型": a.alert_type,
                "严重程度": a.severity,
                "标题": a.title,
                "描述": a.description,
            }
            for a in alerts
        ],
    }

    system_prompt = """你是 WealthPilot 的 AI 投资顾问。你的角色是：
1. 基于用户的资产负债数据和分析结果，给出清晰、专业、可操作的解读
2. 你不做投资决策，你辅助用户理解自己的资产状况
3. 语言风格：专业但不晦涩，像一个经验丰富的私人银行顾问在跟客户沟通
4. 结构要求：先给总体评价（一句话），再分点展开（资产配置、风险提示、建议）
5. 如果有告警，必须明确指出并给出具体的调整方向
6. 所有建议必须基于数据，不要编造数据"""

    user_prompt = f"""请基于以下数据，对我的个人资产配置进行全面分析和解读：

{json.dumps(analysis_data, ensure_ascii=False, indent=2)}

请按以下结构输出：
1. **总体评价**（一句话概括当前资产状况的健康度）
2. **资产配置分析**（当前配置与目标的对比，哪些偏离了，偏离多少）
3. **风险提示**（如果有告警，逐条解读；如果没有，说明当前风险可控）
4. **调整建议**（具体的、可操作的建议，比如"建议将权益仓位从70%降至60%，可考虑减持XXX"）"""

    try:
        response = _get_client().chat.completions.create(
            model=AI_REPORT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=AI_TEMPERATURE,
            max_tokens=AI_REPORT_MAX_TOKENS,
        )
        return response.choices[0].message.content
    except EnvironmentError as e:
        return f"⚠️ 配置错误：{str(e)}"
    except Exception as e:
        return f"AI 分析生成失败: {str(e)}\n\n请检查网络连接和 API 配置。"


def generate_alert_explanation(alert: DeviationAlert) -> str:
    """针对单条告警生成详细解释"""

    system_prompt = """你是 WealthPilot 的 AI 投资顾问。请对以下投资风险告警进行简洁的解释和建议。
要求：2-3句话，说清楚问题是什么、为什么重要、建议怎么做。"""

    user_prompt = f"""告警类型: {alert.alert_type}
严重程度: {alert.severity}
标题: {alert.title}
描述: {alert.description}
当前值: {alert.current_value}%
目标值: {alert.target_value}%
偏离: {alert.deviation:+.1f} 个百分点"""

    try:
        response = _get_client().chat.completions.create(
            model=AI_ALERT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=AI_TEMPERATURE,
            max_tokens=AI_ALERT_MAX_TOKENS,
        )
        return response.choices[0].message.content
    except EnvironmentError as e:
        return f"⚠️ 配置错误：{str(e)}"
    except Exception as e:
        return f"解释生成失败: {str(e)}"
