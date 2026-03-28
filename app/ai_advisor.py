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
    AI_RESEARCH_MODEL, AI_RESEARCH_MAX_TOKENS,
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


def generate_research_card(
    raw_content: str,
    title: str,
    object_name: str = "",
    market_name: str = "",
) -> dict:
    """
    将研究资料原文提炼为结构化候选观点卡。

    返回 dict，字段与 ResearchCard 一一对应。
    若 LLM 调用失败，返回 {"error": "<msg>"}。

    Prompt 设计原则：
    - 要求输出纯 JSON，不加任何 Markdown 包裹
    - 要求偏"研究判断"而非泛泛摘要
    - horizon / stance 枚举值固定，便于后续筛选
    """
    system_prompt = """你是一名专业的卖方研究分析师助理，专门负责把研究资料提炼成结构化的投研观点卡。

你的任务：
1. 仔细阅读用户提供的研究资料（可能是研报摘要、新闻分析、博主观点、会议纪要等）
2. 提炼出核心投研判断，而不是泛泛摘要
3. 尽量区分"看多逻辑"和"看空逻辑/风险"，即使原文只有一个立场
4. 对缺失信息，填 null，不要编造

输出要求：
- 输出纯 JSON 对象，不加 ```json 包裹，不加注释
- 所有字段必须存在，可以为 null
- horizon 只能是 "short" / "medium" / "long" / null
- stance 只能是 "bullish" / "bearish" / "neutral" / "watch" / null
- key_drivers、risks、key_metrics、suggested_tags 输出为 JSON 数组（字符串列表）
- bull_case、bear_case、thesis 用中文，简明扼要，避免废话"""

    context = ""
    if object_name:
        context += f"标的：{object_name}\n"
    if market_name:
        context += f"市场：{market_name}\n"

    user_prompt = f"""请提炼以下研究资料：

标题：{title}
{context}
===资料正文===
{raw_content[:4000]}
===END===

请输出如下 JSON 结构：
{{
  "summary": "这份资料主要讲什么（1-2句）",
  "thesis": "核心投研结论（1-3句，直接说判断，不要模糊）",
  "bull_case": "看多逻辑（如果资料无看多逻辑则为 null）",
  "bear_case": "看空/风险逻辑（如果资料无看空逻辑则为 null）",
  "key_drivers": ["驱动因素1", "驱动因素2"],
  "risks": ["风险1", "风险2"],
  "key_metrics": ["后续观察指标1", "指标2"],
  "horizon": "short|medium|long|null",
  "stance": "bullish|bearish|neutral|watch|null",
  "action_suggestion": "基于此资料，对应操作建议：加仓 / 减仓 / 持有观察 / 避开等（可为 null）",
  "invalidation_conditions": "什么情况下该观点失效（可为 null）",
  "suggested_tags": ["标签1", "标签2"]
}}

注意：suggested_tags 最多输出 5 个最核心的标签，不要超过 5 个。"""

    try:
        response = _get_client().chat.completions.create(
            model=AI_RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=AI_TEMPERATURE,
            max_tokens=AI_RESEARCH_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        raw_json = response.choices[0].message.content
        return json.loads(raw_json)
    except EnvironmentError as e:
        return {"error": f"配置错误：{str(e)}"}
    except json.JSONDecodeError as e:
        return {"error": f"AI 返回结果无法解析为 JSON：{str(e)}"}
    except Exception as e:
        return {"error": f"AI 解析失败：{str(e)}"}


def generate_research_card_full(raw_content: str) -> dict:
    """
    从原始内容中一次性提取元数据（标题、标的、市场、作者、发布时间）
    和结构化投研观点卡字段。

    返回 dict（字段名与 generate_research_card 一致，额外包含
    title / object_name / market_name / author / publish_time）。
    失败返回 {"error": "<msg>"}。
    """
    system_prompt = """你是一名专业的卖方研究分析师助理，负责从研究资料中提取结构化信息。

任务：
1. 识别文档元数据（标题、研究标的、市场、作者、发布时间）
2. 提炼核心投研判断，区分看多/看空逻辑
3. 缺失信息填 null，不要编造

输出要求：
- 输出纯 JSON 对象，不加 ```json 包裹，不加注释
- 所有字段必须存在，可以为 null
- market_name 只能是 "港股" / "美股" / "A股" / "宏观" / "行业" / "其他" / null
- horizon 只能是 "short" / "medium" / "long" / null
- stance 只能是 "bullish" / "bearish" / "neutral" / "watch" / null
- key_drivers / risks / key_metrics / suggested_tags 输出为 JSON 数组
- suggested_tags 最多 5 个"""

    user_prompt = f"""请从以下研究资料中提取所有结构化信息：

===资料正文===
{raw_content[:5000]}
===END===

请输出以下 JSON 结构：
{{
  "title": "资料标题（从内容推断，若明显可见则直接提取）",
  "object_name": "研究标的名称（如：美团、纳斯达克100 等，无则 null）",
  "market_name": "港股|美股|A股|宏观|行业|其他|null",
  "author": "作者或来源机构（无则 null）",
  "publish_time": "发布时间（如：2025-03、2025年Q1，无则 null）",
  "summary": "资料主要内容（1-2句）",
  "thesis": "核心投研结论（1-3句，直接说判断，不要模糊）",
  "bull_case": "看多逻辑（无则 null）",
  "bear_case": "看空/风险逻辑（无则 null）",
  "key_drivers": ["驱动因素1", "驱动因素2"],
  "risks": ["风险1", "风险2"],
  "key_metrics": ["后续观察指标1", "指标2"],
  "horizon": "short|medium|long|null",
  "stance": "bullish|bearish|neutral|watch|null",
  "action_suggestion": "操作建议（如：加仓 / 减仓 / 持有观察，可为 null）",
  "invalidation_conditions": "观点失效条件（可为 null）",
  "suggested_tags": ["标签1", "标签2"]
}}"""

    try:
        response = _get_client().chat.completions.create(
            model=AI_RESEARCH_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=AI_TEMPERATURE,
            max_tokens=AI_RESEARCH_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
        raw_json = response.choices[0].message.content
        return json.loads(raw_json)
    except EnvironmentError as e:
        return {"error": f"配置错误：{str(e)}"}
    except json.JSONDecodeError as e:
        return {"error": f"AI 返回结果无法解析为 JSON：{str(e)}"}
    except Exception as e:
        return {"error": f"AI 解析失败：{str(e)}"}


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
