"""
银行APP截图解析模块，使用 OpenAI GPT-4o Vision 提取资产金额。
"""
import os
import json
import base64
from typing import Optional
from openai import OpenAI

# 每家银行的识别提示词（关键词严格对应 APP 界面实际文字）
BANK_PROMPTS = {
    # 招行 TREE配置页：显示"活钱管理"、"稳健投资"、"进取投资"
    "招商银行": """从招商银行APP截图中，找到"活钱管理"、"稳健投资"、"进取投资"对应的金额（人民币元）。
返回纯JSON，不要markdown：
{"活钱管理": <数字>, "稳健投资": <数字>, "进取投资": <数字>}
如果某类不存在或看不清，填0。只返回JSON。""",

    # 支付宝三笔钱页：显示"灵活取用/活期资产"、"稳健理财"（稳健投资）、"进阶理财"（进取投资）
    # 注意："养老金"属于未来保障，不要提取
    "支付宝": """从支付宝APP截图的"三笔钱分布"中，提取以下3类金额（人民币元）：
- 活期资产：即"灵活取用"下的"活期资产"金额
- 稳健理财：即"投资增值"下的"稳健理财"金额
- 进阶理财：即"投资增值"下的"进阶理财"金额
注意：不要提取"养老金"或"人身保障"。
返回纯JSON，不要markdown：
{"活期资产": <数字>, "稳健理财": <数字>, "进阶理财": <数字>}
如果某类不存在或看不清，填0。只返回JSON。""",

    # 建行财富全景页：显示"活钱"、"投资"（含理财产品/基金/债券）
    # 注意：不要提取"个人养老金"
    "建设银行": """从建设银行APP截图中，提取以下4类资产的金额（人民币元）：
- 活钱：页面顶部"活钱"后面的总金额（直接可用金额）
- 理财产品：投资区域中"理财产品"的金额
- 基金：投资区域中"基金"的金额
- 债券：投资区域中"债券"的金额
注意：不要提取"个人养老金"。
返回纯JSON，不要markdown：
{"活钱": <数字>, "理财产品": <数字>, "基金": <数字>, "债券": <数字>}
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
        "活期资产": {"name": "余额宝",    "asset_class": "货币"},
        "稳健理财": {"name": "稳健理财",  "asset_class": "固收"},
        "进阶理财": {"name": "进阶理财",  "asset_class": "权益"},
    },
    "建设银行": {
        "活钱":     {"name": "建行活期",  "asset_class": "货币"},
        "理财产品": {"name": "建行理财",  "asset_class": "固收"},
        "债券":     {"name": "建行债券",  "asset_class": "固收"},
        "基金":     {"name": "建行基金",  "asset_class": "权益"},
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
