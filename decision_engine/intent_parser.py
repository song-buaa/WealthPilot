"""
意图解析模块 (Intent Parser)

职责：将用户自然语言输入解析为结构化 JSON，供后续模块使用。
实现：调用 Claude LLM，输出固定格式 JSON。

输出结构：
    {
        "asset": "理想汽车",
        "action_type": "加仓判断",
        "time_horizon": "短期",
        "trigger": "发布会",
        "confidence_score": 0.85
    }

规则：
    - confidence_score < 0.6 → 不进入后续流程，返回澄清问题
    - 标的不识别 → asset = None，confidence_score 偏低
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    """意图解析结果"""
    asset: Optional[str]         # 标的名称，如 "理想汽车"，None 表示未识别
    action_type: str             # 加仓判断 / 减仓判断 / 持有评估 / 买入判断 / 卖出判断
    time_horizon: str            # 短期 / 中期 / 长期 / 未知
    trigger: Optional[str]       # 触发事件，如 "发布会"，可为 None
    confidence_score: float      # 0~1，解析置信度
    clarification: Optional[str] = None  # 仅 confidence < 0.6 时有值

    @property
    def needs_clarification(self) -> bool:
        return self.confidence_score < 0.6


# ── Claude 客户端（懒加载）────────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "未找到 ANTHROPIC_API_KEY 环境变量。\n"
                "请在终端执行：export ANTHROPIC_API_KEY='sk-ant-your-key'"
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一个投资决策助手，负责解析用户的投资意图。

请将用户输入解析为以下 JSON 格式，**只返回 JSON，不要有任何其他文字**：

{
  "asset": "标的名称（如理想汽车、腾讯、纳指ETF），无法识别则为 null",
  "action_type": "加仓判断 / 减仓判断 / 买入判断 / 卖出判断 / 持有评估",
  "time_horizon": "短期 / 中期 / 长期 / 未知",
  "trigger": "触发事件或原因（如发布会、财报、市场下跌），没有则为 null",
  "confidence_score": 0.0到1.0的小数，表示你对解析结果的置信度
}

confidence_score 评分规则：
- 标的清晰 + 意图明确 → 0.8~1.0
- 标的清晰但意图模糊 → 0.5~0.7
- 标的不明确 → 0.3~0.5
- 完全无法识别投资意图 → 0.0~0.3

action_type 必须从以下选项中选择：加仓判断 / 减仓判断 / 买入判断 / 卖出判断 / 持有评估
"""

_CLARIFICATION_PROMPT = """你是一个投资决策助手。

用户输入意图不明确，请生成一个简短的澄清问题（不超过30字），
帮助确认用户的真实意图。

例如："你是想了解理想汽车是否值得加仓，还是查看它的近期行情？"

只返回澄清问题本身，不要有其他内容。
"""


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def parse(user_input: str) -> IntentResult:
    """
    解析用户自然语言输入，返回结构化意图。

    Args:
        user_input: 用户输入字符串

    Returns:
        IntentResult，若 needs_clarification=True 则 clarification 字段有值
    """
    if not user_input or not user_input.strip():
        return IntentResult(
            asset=None,
            action_type="持有评估",
            time_horizon="未知",
            trigger=None,
            confidence_score=0.0,
            clarification="请输入您想做的投资决策，例如：'我想加仓理想汽车，发布会后怎么看？'"
        )

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_input}]
        )
        raw = response.content[0].text.strip()

        # 提取 JSON（兼容模型输出中夹带额外文字的情况）
        intent_data = _extract_json(raw)

        result = IntentResult(
            asset=intent_data.get("asset"),
            action_type=intent_data.get("action_type", "持有评估"),
            time_horizon=intent_data.get("time_horizon", "未知"),
            trigger=intent_data.get("trigger"),
            confidence_score=float(intent_data.get("confidence_score", 0.5)),
        )

        # 置信度不足 → 生成澄清问题
        if result.needs_clarification:
            result.clarification = _generate_clarification(client, user_input)

        return result

    except EnvironmentError:
        raise  # 没有 API Key，向上传递

    except Exception as e:
        # 解析失败：降级处理，返回低置信度结果
        return IntentResult(
            asset=None,
            action_type="持有评估",
            time_horizon="未知",
            trigger=None,
            confidence_score=0.2,
            clarification=f"意图解析遇到问题，请重新描述您的决策需求。（{type(e).__name__}）"
        )


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON，处理模型可能附加的说明文字。"""
    # 先尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 ```json ... ``` 代码块
    block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if block:
        return json.loads(block.group(1))

    # 提取第一个 { ... } 块
    brace = re.search(r'\{.*\}', text, re.DOTALL)
    if brace:
        return json.loads(brace.group())

    raise ValueError(f"无法从 LLM 输出中提取 JSON: {text[:200]}")


def _generate_clarification(client: anthropic.Anthropic, user_input: str) -> str:
    """生成澄清问题（置信度低时调用）。"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-20250514",  # 轻量模型即可
            max_tokens=128,
            system=_CLARIFICATION_PROMPT,
            messages=[{"role": "user", "content": user_input}]
        )
        return resp.content[0].text.strip()
    except Exception:
        return "您是想了解某个标的是否值得买入/加仓，还是有其他投资问题？"
