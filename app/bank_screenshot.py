"""
银行APP截图解析模块，使用 OpenAI GPT-4o Vision 提取资产金额。
"""
import os
import json
import base64
from typing import Optional
from openai import OpenAI

# 每家银行的识别提示词
BANK_PROMPTS = {
    "招商银行": """从招商银行APP截图中提取3类资产的总金额（人民币元），返回纯JSON，不要markdown：
{"活钱管理": <数字>, "稳健投资": <数字>, "进取投资": <数字>}
如果某类不存在或看不清，填0。只返回JSON。""",

    "支付宝": """从支付宝APP截图中提取3类资产的总金额（人民币元），返回纯JSON，不要markdown：
{"活钱管理": <数字>, "稳健投资": <数字>, "进取投资": <数字>}
如果某类不存在或看不清，填0。只返回JSON。""",

    "建设银行": """从建设银行APP截图中提取4类资产的总金额（人民币元），返回纯JSON，不要markdown：
{"活钱": <数字>, "理财": <数字>, "债券": <数字>, "基金": <数字>}
如果某类不存在或看不清，填0。只返回JSON。""",
}

# 识别结果 → 数据库持仓名称映射
BANK_POSITION_MAP = {
    "招商银行": {
        "活钱管理": {"name": "朝朝宝",   "asset_class": "货币"},
        "稳健投资": {"name": "招行理财",  "asset_class": "固收"},
        "进取投资": {"name": "招行基金",  "asset_class": "权益"},
    },
    "支付宝": {
        "活钱管理": {"name": "余额宝",    "asset_class": "货币"},
        "稳健投资": {"name": "稳健理财",  "asset_class": "固收"},
        "进取投资": {"name": "进阶理财",  "asset_class": "权益"},
    },
    "建设银行": {
        "活钱": {"name": "建行活期",  "asset_class": "货币"},
        "理财": {"name": "建行理财",  "asset_class": "固收"},
        "债券": {"name": "建行债券",  "asset_class": "固收"},
        "基金": {"name": "建行基金",  "asset_class": "权益"},
    },
}


def parse_bank_screenshot(image_bytes: bytes, bank: str) -> tuple[dict, Optional[str]]:
    """
    调用 GPT-4o Vision 解析银行截图，返回 (result_dict, error_str)
    result_dict: {"活钱管理": 1234.56, ...}
    error_str: None 表示成功，否则为错误信息
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {}, "未配置 OPENAI_API_KEY"

    prompt = BANK_PROMPTS.get(bank)
    if not prompt:
        return {}, f"不支持的银行: {bank}"

    try:
        client = OpenAI(api_key=api_key)
        b64 = base64.b64encode(image_bytes).decode()
        # 判断图片格式
        mime = "image/png" if image_bytes[:4] == b'\x89PNG' else "image/jpeg"

        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": "high",
                    }},
                ],
            }],
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        # 去掉可能的 markdown 代码块
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        # 确保所有值都是 float
        result = {k: float(v) for k, v in result.items()}
        return result, None
    except json.JSONDecodeError as e:
        return {}, f"AI返回格式错误，无法解析：{e}"
    except Exception as e:
        return {}, str(e)


def bank_positions_to_db(result: dict, bank: str) -> list[dict]:
    """
    将识别结果转换为数据库更新格式
    返回 [{"name": ..., "market_value_cny": ..., "asset_class": ...}, ...]
    """
    mapping = BANK_POSITION_MAP.get(bank, {})
    updates = []
    for category, amount in result.items():
        pos_info = mapping.get(category)
        if pos_info and amount > 0:
            updates.append({
                "name": pos_info["name"],
                "asset_class": pos_info["asset_class"],
                "market_value_cny": round(amount, 2),
            })
    return updates
