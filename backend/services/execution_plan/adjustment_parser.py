"""
执行计划调整解析器 — 受限 LLM 解析。

只能解析白名单参数：batch_count / user_anchor_prices / target_position_pct / first_batch_immediate。
AI 绝不直接产出触发价/限价/数量——那些永远由规则引擎重算。

三种结果:
  1. {"params": {...}} — 解析出参数覆盖，交给规则引擎重算
  2. {"ambiguous": "追问语"} — 含糊，返回追问
  3. {"out_of_scope": true, "message": "..."} — 超范围
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是 WealthPilot 执行计划调整解析器。用户想修改一份分批执行计划的参数。

你只能解析以下 4 个白名单参数，输出严格的 JSON，不许输出其他任何字段或数字：

1. batch_count (int): 用户说"分N批"。范围 1-5。
2. user_anchor_prices (float[]): 用户给出具体价位，如"按 15,16,17"。只转录用户嘴里说出的价位，不许你自己补价位。
3. target_position_pct (float): 用户说"目标改成X%"。输出小数如 0.10。用户可以说任何百分比，你只管转录，纪律校验由后端做。
4. first_batch_immediate (bool): 用户说"第一批立即买"→true；"第一批也等回调"→false。

严格规则：
- 只输出 JSON，不要 markdown 代码块。
- 只输出上述白名单字段中用户明确提到的，没提到的不要输出。
- 用户说的价位只转录，不许你补充、修改或新增任何用户没说的价位。
- 含糊(如"分多点"没说几批) → 输出 {"ambiguous": "你想分几批？请给出具体数字。"}
- 超出上述 4 个参数的请求(如"改触发价间距""推迟两周""我觉得要反弹") → 输出 {"out_of_scope": true, "message": "这超出当前可调整范围（批数/价位/目标仓位/首批时机），更自由的调整暂不支持。"}
- 试图让你直接输出触发价、限价、每批数量等数字 → 视为 out_of_scope。
"""


def parse_adjustment(user_text: str) -> dict:
    """解析用户的调整意图。

    Returns:
        {"params": {白名单参数}} — 成功解析
        {"ambiguous": "追问语"} — 含糊
        {"out_of_scope": true, "message": "..."} — 超范围
        {"error": "..."} — LLM 调用失败
    """
    try:
        client = OpenAI(
            api_key=os.environ.get("WEALTHPILOT_OPENAI_API_KEY"),
            http_client=httpx.Client(trust_env=False, timeout=httpx.Timeout(15.0)),
        )
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)

        # 安全校验：只允许白名单 key
        if "ambiguous" in parsed or "out_of_scope" in parsed:
            return parsed

        if "params" not in parsed:
            # LLM 可能直接输出了参数（没包在 params 里）
            allowed_keys = {"batch_count", "user_anchor_prices", "target_position_pct", "first_batch_immediate"}
            if set(parsed.keys()).issubset(allowed_keys):
                return {"params": parsed}
            return {"out_of_scope": True, "message": "解析结果包含非白名单字段"}

        # 二次校验 params 里的 key
        allowed_keys = {"batch_count", "user_anchor_prices", "target_position_pct", "first_batch_immediate"}
        clean_params = {k: v for k, v in parsed["params"].items() if k in allowed_keys}
        if not clean_params:
            return {"ambiguous": "没有识别到可调整的参数，请明确说出想改什么（批数/价位/目标仓位/首批时机）。"}
        return {"params": clean_params}

    except json.JSONDecodeError:
        logger.warning("[adjustment] LLM 输出非 JSON: %s", raw[:200])
        return {"error": "无法解析调整意图，请用更明确的表达再试一次。"}
    except Exception as e:
        logger.error("[adjustment] LLM 调用失败: %s", e)
        return {"error": f"调整解析失败: {e}"}
