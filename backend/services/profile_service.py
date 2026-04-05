"""
Profile Service — 用户画像与投资目标业务逻辑
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional

import openai

from app.models import UserProfile, get_session


# ── openai 懒加载（与 decision_engine/llm_engine.py 保持一致）────────────────

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("未找到 OPENAI_API_KEY 环境变量。")
        _client = openai.OpenAI(api_key=api_key)
    return _client


# ── 风险等级标准化 ────────────────────────────────────────────────────────────

def normalize_risk_level(source_type: str, original_level: str) -> int:
    """
    source_type: "bank" | "broker" | "custom"

    银行(A1-A5): A1→1, A2→2, A3→3, A4→4, A5→5
    券商(C1-C6): C1→1, C2→1, C3→2, C4→3, C5→4, C6→5
    自定义:      低→2, 中→3, 高→4

    返回 int 1-5
    """
    level = original_level.strip().upper()
    if source_type == "bank":
        mapping = {"A1": 1, "A2": 2, "A3": 3, "A4": 4, "A5": 5}
        return mapping.get(level, 3)
    elif source_type == "broker":
        mapping = {"C1": 1, "C2": 1, "C3": 2, "C4": 3, "C5": 4, "C6": 5}
        return mapping.get(level, 3)
    elif source_type == "custom":
        mapping = {"低": 2, "中": 3, "高": 4}
        return mapping.get(original_level.strip(), 3)
    return 3


_RISK_TYPE_MAP = {1: "保守型", 2: "稳健型", 3: "平衡型", 4: "成长型", 5: "进取型"}


def risk_level_to_type(level: int) -> str:
    return _RISK_TYPE_MAP.get(level, "平衡型")


# ── 冲突检测 ──────────────────────────────────────────────────────────────────

def check_conflicts(max_drawdown: str, target_return: str, fund_usage_timeline: str) -> list[dict]:
    """
    规则1: fund_usage_timeline == "1年内" AND max_drawdown in ["15-30%", ">30%"]
    规则2: max_drawdown == "<5%" AND target_return in ["10-20%", ">20%"]

    有冲突返回:
    [{"type": "conflict", "message": "...", "options": ["优先收益", "优先稳健"]}]

    无冲突返回空列表
    """
    conflicts = []
    if fund_usage_timeline == "1年内" and max_drawdown in ["15-30%", ">30%"]:
        conflicts.append({
            "type": "conflict",
            "message": "您的资金在1年内要用，但可接受的最大回撤较高（短期内可能面临较大亏损），两者存在冲突。",
            "options": ["优先收益", "优先稳健"],
        })
    if max_drawdown == "<5%" and target_return in ["10-20%", ">20%"]:
        conflicts.append({
            "type": "conflict",
            "message": "您期望的目标收益率较高，但最大回撤容忍度很低（<5%），高收益通常伴随高波动，两者存在冲突。",
            "options": ["优先收益", "优先稳健"],
        })
    return conflicts


# ── AI 槽位提取 ───────────────────────────────────────────────────────────────

_EXTRACT_SYSTEM = """你是 WealthPilot 的用户画像助手，从用户自然语言中提取画像字段。

严格规则：
1. 只提取能确定的字段，不确定的返回 null
2. 所有字段值必须在以下枚举值范围内，否则返回 null：
   - income_level: "<10万" | "10-30万" | "30-100万" | ">100万"
   - income_stability: "稳定" | "较稳定" | "波动"
   - total_assets: "<50万" | "50-200万" | "200-500万" | ">500万"
   - investable_ratio: "<20%" | "20-50%" | "50-80%" | ">80%"
   - liability_level: "无" | "低" | "中" | "高"
   - family_status: "单身" | "已婚无子" | "已婚有子" | "退休"
   - asset_structure: "现金为主" | "固收为主" | "股票基金为主" | "多元配置"
   - investment_motivation: "新增资金" | "调整配置" | "市场波动调整" | "长期规划"
   - fund_usage_timeline: "1年内" | "1-3年" | "3年以上" | "不确定"
   - goal_type: 数组，元素为 "资本增值" | "稳健增长" | "保值" | "现金流"
   - target_return: "<5%" | "5-10%" | "10-20%" | ">20%"
   - max_drawdown: "<5%" | "5-15%" | "15-30%" | ">30%"
   - investment_horizon: "<1年" | "1-3年" | "3-5年" | ">5年"
3. 返回严格 JSON，无 markdown 包裹
4. missing_fields 优先级：total_assets > goal_type > max_drawdown > investment_horizon

返回格式：
{
  "extracted": {<字段名>: <值> | null},
  "missing_fields": ["字段名列表"],
  "next_question": "下一个追问的自然语言问题（如果 missing_fields 不为空，否则 null）"
}"""


def extract_profile_from_text(user_input: str, existing_fields: dict) -> dict:
    """从自然语言提取画像字段"""
    try:
        client = _get_client()
        user_msg = (
            f"用户输入：{user_input}\n\n"
            f"已有字段（不要重复提取）：{json.dumps(existing_fields, ensure_ascii=False)}"
        )
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=512,
            timeout=20,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()
        # 去掉可能的 markdown 包裹
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        return {
            "extracted": {},
            "missing_fields": [],
            "next_question": None,
            "error": str(e),
        }


_EXTRACT_IMAGES_SYSTEM = """你是 WealthPilot 的用户画像助手，从用户上传的风险评估报告截图中提取画像字段。

严格规则：
1. 只提取图片中能明确看到的字段，不确定的返回 null
2. 所有字段值必须在以下枚举值范围内，否则返回 null：
   - risk_source: "bank" | "broker" | "custom"
   - risk_original_level:
     若 risk_source=="bank": "A1"|"A2"|"A3"|"A4"|"A5"
     若 risk_source=="broker": "C1"|"C2"|"C3"|"C4"|"C5"
     若 risk_source=="custom": "低"|"中"|"高"
   - income_level: "<10万" | "10-30万" | "30-100万" | ">100万"
   - income_stability: "稳定" | "较稳定" | "波动"
   - total_assets: "<50万" | "50-200万" | "200-500万" | ">500万"
   - investable_ratio: "<20%" | "20-50%" | "50-80%" | ">80%"
   - liability_level: "无" | "低" | "中" | "高"
   - family_status: "单身" | "已婚无子" | "已婚有子" | "退休"
   - asset_structure: "现金为主" | "固收为主" | "股票基金为主" | "多元配置"
   - investment_motivation: "新增资金" | "调整配置" | "市场波动调整" | "长期规划"
   - fund_usage_timeline: "1年内" | "1-3年" | "3年以上" | "不确定"
3. 多张图片合并提取，字段有冲突时取更可信的值
4. 返回严格 JSON，无 markdown 包裹

返回格式：
{
  "extracted": {<字段名>: <值> | null},
  "missing_fields": ["字段名列表"],
  "next_question": null
}"""


def extract_profile_from_images(images: list[str], existing_fields: dict) -> dict:
    """从图片（base64）提取画像字段，支持多张图片同时解析"""
    try:
        client = _get_client()
        content = []
        content.append({"type": "text", "text": f"请从以下图片中提取用户画像字段。已有字段（不要重复提取）：{json.dumps(existing_fields, ensure_ascii=False)}"})
        for img_b64 in images:
            # 判断是否已带 data URI 前缀
            if img_b64.startswith("data:"):
                url = img_b64
            else:
                url = f"data:image/jpeg;base64,{img_b64}"
            content.append({
                "type": "image_url",
                "image_url": {"url": url, "detail": "high"},
            })
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=512,
            timeout=30,
            messages=[
                {"role": "system", "content": _EXTRACT_IMAGES_SYSTEM},
                {"role": "user",   "content": content},
            ],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        return {
            "extracted": {},
            "missing_fields": [],
            "next_question": None,
            "error": str(e),
        }


# ── AI 画像生成 ───────────────────────────────────────────────────────────────

_GENERATE_SYSTEM = """你是 WealthPilot 的画像总结助手。根据用户画像数据，生成一段简洁的自然语言总结（2-3句话），以及一个风格标签。

返回严格 JSON（无 markdown 包裹）：
{"summary": "...", "style": "稳健|平衡|进取"}

规则：
- summary 必须非空，基于数据描述用户的投资风格和目标
- style 只能是 "稳健"、"平衡"、"进取" 三者之一
- 输出语言为中文"""


def generate_ai_profile(profile: UserProfile) -> dict:
    """
    调用 gpt-4.1 生成 summary 和 style。
    confidence 本地计算（不调 LLM）：
      - risk_source == "external" → "high"
      - 所有核心字段有值（risk_normalized_level, goal_type, max_drawdown, investment_horizon）→ "medium"
      - 否则 → "low"
    """
    # 本地计算 confidence
    if profile.risk_source in ("bank", "broker", "custom", "external"):
        confidence = "high"
    elif all([
        profile.risk_normalized_level,
        profile.goal_type,
        profile.max_drawdown,
        profile.investment_horizon,
    ]):
        confidence = "medium"
    else:
        confidence = "low"

    profile_dict = {
        "risk_type":             profile.risk_type,
        "risk_normalized_level": profile.risk_normalized_level,
        "income_level":          profile.income_level,
        "income_stability":      profile.income_stability,
        "total_assets":          profile.total_assets,
        "family_status":         profile.family_status,
        "goal_type":             profile.goal_type,
        "target_return":         profile.target_return,
        "max_drawdown":          profile.max_drawdown,
        "investment_horizon":    profile.investment_horizon,
        "fund_usage_timeline":   profile.fund_usage_timeline,
    }

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=256,
            timeout=20,
            messages=[
                {"role": "system", "content": _GENERATE_SYSTEM},
                {"role": "user",   "content": json.dumps(profile_dict, ensure_ascii=False, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        return {
            "summary":    result.get("summary", ""),
            "style":      result.get("style", "平衡"),
            "confidence": confidence,
        }
    except Exception as e:
        return {
            "summary":    f"根据您的画像数据，系统暂时无法生成完整总结（{e}）。",
            "style":      "平衡",
            "confidence": confidence,
        }


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_profile() -> Optional[dict]:
    """获取当前画像（不存在返回 None）"""
    session = get_session()
    try:
        profile = session.query(UserProfile).first()
        if profile is None:
            return None
        return _profile_to_dict(profile)
    finally:
        session.close()


def upsert_profile(data: dict) -> dict:
    """保存/更新画像（始终只有一条记录，upsert 语义）"""
    session = get_session()
    try:
        profile = session.query(UserProfile).first()
        is_new = profile is None
        if is_new:
            profile = UserProfile()
            session.add(profile)

        for key, value in data.items():
            if key in ("id", "created_at"):
                continue
            if not hasattr(profile, key):
                continue
            # goal_type 前端传 list，存为 JSON 字符串
            if key == "goal_type" and isinstance(value, list):
                setattr(profile, key, json.dumps(value, ensure_ascii=False))
            # risk_assessed_at 前端传 ISO 字符串，存为 datetime
            elif key == "risk_assessed_at" and isinstance(value, str):
                try:
                    setattr(profile, key, datetime.fromisoformat(value))
                except ValueError:
                    pass
            else:
                setattr(profile, key, value)

        profile.updated_at = datetime.now()
        profile.version = 1 if is_new else ((profile.version or 1) + 1)
        session.commit()
        session.refresh(profile)
        return _profile_to_dict(profile)
    finally:
        session.close()


def is_risk_expired() -> bool:
    """检查风险评估是否过期（超过12个月）"""
    session = get_session()
    try:
        profile = session.query(UserProfile).first()
        if profile is None or profile.risk_assessed_at is None:
            return False
        return (datetime.now() - profile.risk_assessed_at) > timedelta(days=365)
    finally:
        session.close()


# ── 内部：序列化 ──────────────────────────────────────────────────────────────

def _profile_to_dict(profile: UserProfile) -> dict:
    return {
        "id":                    profile.id,
        "version":               profile.version,
        "created_at":            profile.created_at.isoformat() if profile.created_at else None,
        "updated_at":            profile.updated_at.isoformat() if profile.updated_at else None,
        "risk_source":           profile.risk_source,
        "risk_provider":         profile.risk_provider,
        "risk_original_level":   profile.risk_original_level,
        "risk_normalized_level": profile.risk_normalized_level,
        "risk_type":             profile.risk_type,
        "risk_assessed_at":      profile.risk_assessed_at.isoformat() if profile.risk_assessed_at else None,
        "income_level":          profile.income_level,
        "income_stability":      profile.income_stability,
        "total_assets":          profile.total_assets,
        "investable_ratio":      profile.investable_ratio,
        "liability_level":       profile.liability_level,
        "family_status":         profile.family_status,
        "asset_structure":       profile.asset_structure,
        "investment_motivation": profile.investment_motivation,
        "fund_usage_timeline":   profile.fund_usage_timeline,
        "goal_type":             json.loads(profile.goal_type) if profile.goal_type else None,
        "target_return":         profile.target_return,
        "max_drawdown":          profile.max_drawdown,
        "investment_horizon":    profile.investment_horizon,
        "ai_summary":            profile.ai_summary,
        "ai_style":              profile.ai_style,
        "ai_confidence":         profile.ai_confidence,
    }
