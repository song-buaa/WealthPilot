"""
意图解析模块 (Intent Parser) — V3.1

职责：将用户自然语言输入解析为结构化 JSON，供后续模块使用。
实现：调用 Claude LLM，输出固定格式 JSON。

V3.1 新增：
    - intent_type 三路分类：investment_decision / general_chat / hypothetical
    - last_intent 上下文继承：追问时自动补全缺失字段
    - is_context_inherited 标记是否有字段来自继承

输出结构：
    {
        "asset": "理想汽车",
        "action_type": "加仓判断",
        "time_horizon": "短期",
        "trigger": "发布会",
        "confidence_score": 0.85,
        "intent_type": "investment_decision",
        "is_context_inherited": false
    }

规则：
    - confidence_score < 0.6 → 不进入后续流程，返回澄清问题
    - 标的不识别 → asset = None，confidence_score 偏低
    - intent_type = hypothetical → 拦截，不进入决策流程
    - intent_type = general_chat → 转普通对话，不进入决策流程
"""

import asyncio
import json
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Optional

import anthropic
import httpx


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class IntentResult:
    """意图解析结果 — V3.1"""
    asset: Optional[str]         # 标的名称，如 "理想汽车"，None 表示未识别
    action_type: str             # 加仓判断 / 减仓判断 / 持有评估 / 买入判断 / 卖出判断
    time_horizon: str            # 短期 / 中期 / 长期 / 未知
    trigger: Optional[str]       # 触发事件，如 "发布会"，可为 None
    confidence_score: float      # 0~1，解析置信度
    clarification: Optional[str] = None  # 仅 confidence < 0.6 时有值
    # V3.1 新增
    intent_type: str = "investment_decision"  # investment_decision / general_chat / hypothetical
    is_context_inherited: bool = False        # 是否有字段继承自上轮 last_intent

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

        # Fix: Streamlit's ScriptRunnerThread has no event loop.
        # httpx 0.28.x with SOCKS proxy may attempt asyncio internals → RuntimeError.
        # Ensure a fresh event loop exists in this thread before creating the client.
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        # Build an explicit synchronous httpx.Client.
        # Priority: HTTPS_PROXY (HTTP tunnel, no extra deps) > ALL_PROXY (SOCKS, needs httpcore[socks])
        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        all_proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")

        # Prefer HTTP/HTTPS proxy over SOCKS — avoids httpcore[socks] dependency
        proxy_url = None
        if https_proxy and not https_proxy.startswith("socks"):
            proxy_url = https_proxy
        elif all_proxy:
            proxy_url = all_proxy  # SOCKS fallback (needs httpcore[socks] installed)

        try:
            http_client = httpx.Client(proxy=proxy_url) if proxy_url else httpx.Client()
            _client = anthropic.Anthropic(api_key=api_key, http_client=http_client)
        except Exception:
            # Fallback: let Anthropic pick up proxies automatically
            _client = anthropic.Anthropic(api_key=api_key)

    return _client


# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一个投资决策助手，负责分类和解析用户的投资意图。

**第一步：识别 intent_type**

判断规则（按优先级）：
1. "hypothetical"：用户提出假设性问题，如"如果...会怎样""假如我...呢""要是...的话""如果减仓..."
2. "general_chat"：日常对话、问候、无关投资的问题，或无法解析为具体投资操作的输入
3. "investment_decision"：包含明确的投资标的 + 操作意图（买入/卖出/加仓/减仓/持有评估）

**第二步：返回 JSON**

对于所有类型，统一返回以下格式（只返回 JSON，不要任何其他文字）：

{
  "intent_type": "investment_decision 或 general_chat 或 hypothetical",
  "asset": "标的名称（如理想汽车、腾讯、纳指ETF），无法识别或不适用则为 null",
  "action_type": "加仓判断 / 减仓判断 / 买入判断 / 卖出判断 / 持有评估",
  "time_horizon": "短期 / 中期 / 长期 / 未知",
  "trigger": "触发事件或原因（如发布会、财报），没有则为 null",
  "confidence_score": 0.0到1.0的小数,
  "is_context_inherited": false
}

**上下文继承规则**（仅 investment_decision 生效）：
若输入包含 [历史上下文] 字段，且当前输入缺少 action_type 或 time_horizon：
- 缺少 action_type → 从 last_intent.action_type 继承，设 is_context_inherited = true
- 缺少 time_horizon → 从 last_intent.time_horizon 继承，设 is_context_inherited = true

**confidence_score 规则（investment_decision）**：
- 标的清晰 + 意图明确 → 0.8~1.0
- 标的清晰但意图模糊 → 0.5~0.7
- 标的不明确 → 0.3~0.5
- 完全无法识别投资意图 → 0.0~0.3

对于 general_chat / hypothetical，confidence_score 设为 0.9。
action_type 必须从以下选项中选择：加仓判断 / 减仓判断 / 买入判断 / 卖出判断 / 持有评估
"""

_CLARIFICATION_PROMPT = """你是一个投资决策助手。

用户输入意图不明确，请生成一个简短的澄清问题（不超过30字），
帮助确认用户的真实意图。

例如："你是想了解理想汽车是否值得加仓，还是查看它的近期行情？"

只返回澄清问题本身，不要有其他内容。
"""


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def parse(user_input: str, last_intent: Optional["IntentResult"] = None) -> "IntentResult":
    """
    解析用户自然语言输入，返回结构化意图。V3.1 新增 last_intent 上下文继承。

    Args:
        user_input:  用户输入字符串
        last_intent: 上轮解析结果（从 conversation_history 最近 user 消息的 intent 获取）。
                     仅用于意图字段继承，不改变是否执行完整决策流程的逻辑。

    Returns:
        IntentResult。intent_type 决定后续路由：
        - investment_decision → 进入完整决策流程
        - general_chat        → 走普通对话，不生成 decision_id
        - hypothetical        → 中断，提示不支持假设推演
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

    # 构建携带上下文的用户消息，供 LLM 进行字段继承
    if last_intent and last_intent.intent_type == "investment_decision":
        full_input = (
            f"[历史上下文]\n"
            f"上轮标的：{last_intent.asset or 'N/A'}，"
            f"上轮操作：{last_intent.action_type}，"
            f"上轮时间维度：{last_intent.time_horizon}\n\n"
            f"[当前输入]\n{user_input}"
        )
    else:
        full_input = user_input

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": full_input}]
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
            intent_type=intent_data.get("intent_type", "investment_decision"),
            is_context_inherited=bool(intent_data.get("is_context_inherited", False)),
        )

        # general_chat / hypothetical 不需要澄清，直接返回
        if result.intent_type in ("general_chat", "hypothetical"):
            return result

        # investment_decision：置信度不足 → 生成澄清问题
        if result.needs_clarification:
            result.clarification = _generate_clarification(client, user_input)

        return result

    except EnvironmentError:
        raise  # 没有 API Key，向上传递

    except Exception as e:
        # 解析失败：降级处理，返回低置信度结果
        # 将完整 traceback 打印到 stderr，便于开发调试
        tb = traceback.format_exc()
        print(f"[intent_parser] API 调用失败:\n{tb}", flush=True)

        # 重置缓存的 client，下次重试时重新创建
        global _client
        _client = None

        err_summary = str(e)[:120] if str(e) else type(e).__name__
        return IntentResult(
            asset=None,
            action_type="持有评估",
            time_horizon="未知",
            trigger=None,
            confidence_score=0.2,
            clarification=f"意图解析遇到问题，请重新描述您的决策需求。（{type(e).__name__}: {err_summary}）"
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
