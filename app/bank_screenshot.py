"""
银行APP截图解析模块，使用 OpenAI GPT-4o Vision 提取资产金额。
"""
import os
import io
import json
import base64
from typing import Optional
import httpx
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

# 识别结果 → 数据库持仓名称映射（name 直接用银行 APP 的分类名）
BANK_POSITION_MAP = {
    "招商银行": {
        "活钱管理": {"name": "活钱管理", "asset_class": "货币"},
        "稳健投资": {"name": "稳健投资", "asset_class": "固收"},
        "进取投资": {"name": "进取投资", "asset_class": "权益"},
    },
    "支付宝": {
        "活期资产": {"name": "活钱管理", "asset_class": "货币"},
        "稳健理财": {"name": "稳健投资", "asset_class": "固收"},
        "进阶理财": {"name": "进取投资", "asset_class": "权益"},
    },
    "建设银行": {
        "活钱":     {"name": "活钱",     "asset_class": "货币"},
        "理财产品": {"name": "理财产品", "asset_class": "固收"},
        "债券":     {"name": "债券",     "asset_class": "固收"},
        "基金":     {"name": "基金",     "asset_class": "权益"},
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

    raw, error = _call_vision_api(image_bytes, prompt, max_tokens=300)
    if error:
        return {}, error
    try:
        result = json.loads(raw)
        result = {k: float(v) for k, v in result.items()}
        return result, None
    except json.JSONDecodeError as e:
        return {}, f"AI返回格式错误，无法解析：{e}"


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


# ── 券商持仓截图（雪盈/国金）─────────────────────────────────────────

BROKER_PROMPTS = {
    # 雪盈证券：港美账户，市值/盈亏均为美元
    "雪盈证券": """从雪盈证券APP持仓截图中，提取所有股票持仓数据。
返回纯JSON数组，不要markdown：
[{"name": <股票中文名>, "ticker": <英文代码如LI>, "quantity": <持仓数量整数>, "market_value_usd": <市值美元数字>, "pnl_usd": <浮动盈亏美元，负数带负号>, "pnl_pct": <盈亏百分比数字如-42.46>}]
字段说明：name=股票中文名，ticker=英文代码，quantity=持仓数量，market_value_usd=市值（美元），pnl_usd=浮动盈亏（美元，负数带负号），pnl_pct=盈亏百分比数字（不含%）。
如果某字段看不清填0，名称看不清填空字符串。只返回JSON数组。""",

    # 国金证券：普通交易-持仓页，市值和盈亏均已换算为人民币(CNY)，价格栏为HK$
    # 同一股票可能有多个批次（不同成本），分别列出
    "国金证券": """从国金证券APP"普通交易-持仓"截图中，提取所有股票持仓数据。
注意：截图顶部标注"人民币 CNY"，证券列的市值和持仓盈亏均为人民币（CNY），价格栏HK$可忽略。
同一股票若有多个持仓批次（成本不同），请分别列出（name后加序号区分，如"理想汽车-W_1"、"理想汽车-W_2"）。
返回纯JSON数组，不要markdown：
[{"name": <股票名称如理想汽车-W>, "ticker": "", "quantity": <持仓数量整数>, "market_value_cny": <市值人民币数字>, "pnl_cny": <持仓盈亏人民币，负数带负号>, "pnl_pct": <盈亏百分比数字如-51.87>}]
只返回JSON数组。""",
}


def _call_vision_api(image_bytes: bytes, prompt: str, max_tokens: int = 1000) -> tuple[str, Optional[str]]:
    """调用 GPT-4o Vision，返回 (raw_text, error_str)"""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "", "未配置 OPENAI_API_KEY"

    try:
        http_client = httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        client = OpenAI(api_key=api_key, http_client=http_client)

        # 压缩图片
        try:
            from PIL import Image as PILImage
            img = PILImage.open(io.BytesIO(image_bytes))
            max_side = 1600
            if max(img.width, img.height) > max_side:
                img.thumbnail((max_side, max_side), PILImage.LANCZOS)
            buf = io.BytesIO()
            fmt = "PNG" if image_bytes[:4] == b'\x89PNG' else "JPEG"
            img.save(buf, format=fmt, quality=85)
            image_bytes = buf.getvalue()
        except Exception:
            pass

        b64 = base64.b64encode(image_bytes).decode()
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
            max_tokens=max_tokens,
        )
        raw = resp.choices[0].message.content.strip()
        # 去掉可能的 markdown 代码块
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return raw, None
    except Exception as e:
        return "", str(e)


def parse_broker_screenshot(image_bytes: bytes, broker: str) -> tuple[list, Optional[str]]:
    """
    解析券商APP持仓截图（雪盈/国金），返回 (positions_list, error_str)
    positions_list: [{"name": ..., "ticker": ..., "quantity": ..., ...}, ...]
    """
    prompt = BROKER_PROMPTS.get(broker)
    if not prompt:
        return [], f"不支持的券商: {broker}"

    raw, error = _call_vision_api(image_bytes, prompt, max_tokens=1500)
    if error:
        return [], error

    try:
        result = json.loads(raw)
        if not isinstance(result, list):
            return [], "AI返回格式错误：期望数组"
        # 数值字段统一转 float
        num_fields = ["quantity", "market_value_usd", "market_value_hkd", "market_value_cny",
                      "pnl_usd", "pnl_cny", "pnl_pct"]
        for item in result:
            for key in num_fields:
                if key in item:
                    try:
                        item[key] = float(item[key])
                    except (ValueError, TypeError):
                        item[key] = 0.0
        # 过滤掉名称为空或市值为0的行
        result = [p for p in result if p.get("name", "").strip()
                  and (p.get("market_value_usd", 0) > 0
                       or p.get("market_value_hkd", 0) > 0
                       or p.get("market_value_cny", 0) > 0)]
        return result, None
    except json.JSONDecodeError as e:
        return [], f"AI返回格式错误，无法解析：{e}"


def broker_positions_to_db(positions: list, broker: str) -> list[dict]:
    """
    将券商截图识别结果转换为 Position 数据库格式。
    返回可直接传入 _import_positions_by_platform 的 list[dict]。
    """
    from app.fx_service import fx_service
    usd_to_cny, _ = fx_service._get_rate_with_date("USD", "CNY", "latest")
    hkd_to_cny, _ = fx_service._get_rate_with_date("HKD", "CNY", "latest")

    db_rows = []
    for pos in positions:
        name = pos.get("name", "").strip()
        if not name:
            continue
        ticker = str(pos.get("ticker", "")).strip()
        quantity = float(pos.get("quantity", 0))
        pnl_pct = float(pos.get("pnl_pct", 0))

        if broker == "雪盈证券":
            mv_usd = float(pos.get("market_value_usd", 0))
            pnl_usd = float(pos.get("pnl_usd", 0))
            db_rows.append({
                "name": name,
                "ticker": ticker,
                "platform": broker,
                "asset_class": "权益",
                "segment": "投资",
                "currency": "USD",
                "original_currency": "USD",
                "original_value": round(mv_usd, 2),
                "fx_rate_to_cny": round(usd_to_cny, 4),
                "fx_rate_date": "latest",
                "market_value_cny": round(mv_usd * usd_to_cny, 2),
                "quantity": quantity,
                "cost_price": 0.0,
                "current_price": 0.0,
                "profit_loss_original_value": round(pnl_usd, 2),
                "profit_loss_value": round(pnl_usd * usd_to_cny, 2),
                "profit_loss_rate": pnl_pct,
            })

        elif broker == "国金证券":
            # 截图中市值已是人民币，反推 HKD 原始金额供港币列显示
            mv_cny = float(pos.get("market_value_cny", 0))
            pnl_cny = float(pos.get("pnl_cny", 0))
            mv_hkd = round(mv_cny / hkd_to_cny, 2) if hkd_to_cny > 0 else 0.0
            db_rows.append({
                "name": name,
                "ticker": ticker,
                "platform": broker,
                "asset_class": "权益",
                "segment": "投资",
                "currency": "HKD",
                "original_currency": "HKD",
                "original_value": mv_hkd,
                "fx_rate_to_cny": round(hkd_to_cny, 4),
                "fx_rate_date": "latest",
                "market_value_cny": round(mv_cny, 2),
                "quantity": quantity,
                "cost_price": 0.0,
                "current_price": 0.0,
                "profit_loss_original_value": 0.0,
                "profit_loss_value": round(pnl_cny, 2),
                "profit_loss_rate": pnl_pct,
            })

    return db_rows
