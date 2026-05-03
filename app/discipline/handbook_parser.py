"""
WealthPilot — 投资纪律手册解析器

功能：
1. 从手册的人类可读 Markdown 文字中提取规则参数（用户只改文字即可）
2. 校验参数合法性（类型 + 合理范围）
3. 与当前配置对比，生成人类可读的 diff
4. [内部] 仍支持从 <!-- RULES_CONFIG ... --> 块提取（机器写入用）

设计原则：
- 用户只需编辑手册中的表格数字/百分比，系统自动检测并同步到规则引擎
- 用户无需知道参数的 JSON 字段名
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

# ── 被追踪的字段（影响仪表盘逻辑），附带人类可读标签和合理范围 ──────────────
_TRACKED_FIELDS: list[dict] = [
    {
        "path": ["leverage_limits", "level_0_forbidden"],
        "label": "Level0 禁止工具列表（规则1）",
        "type": list,
    },
    {
        "path": ["leverage_limits", "level_1_max_pct"],
        "label": "杠杆ETF仓位上限（规则1）",
        "type": float, "min": 0.01, "max": 0.20,
        "fmt": lambda v: f"{v*100:.0f}%",
    },
    {
        "path": ["leverage_limits", "leverage_ratio_normal_max"],
        "label": "总杠杆率正常上限（规则1）",
        "type": float, "min": 1.0, "max": 2.0,
        "fmt": lambda v: f"{v:.2f}x",
    },
    {
        "path": ["leverage_limits", "leverage_ratio_acceptable_max"],
        "label": "总杠杆率可接受上限（规则1）",
        "type": float, "min": 1.0, "max": 2.0,
        "fmt": lambda v: f"{v:.2f}x",
    },
    {
        "path": ["leverage_limits", "leverage_ratio_warning_max"],
        "label": "总杠杆率警戒上限（规则1）",
        "type": float, "min": 1.0, "max": 3.0,
        "fmt": lambda v: f"{v:.2f}x",
    },
    {
        "path": ["single_asset_limits", "max_position_pct"],
        "label": "单一标的仓位硬性上限（规则3）",
        "type": float, "min": 0.10, "max": 1.0,
        "fmt": lambda v: f"{v*100:.0f}%",
    },
    {
        "path": ["single_asset_limits", "warning_position_pct"],
        "label": "单一标的警戒区起点（规则3）",
        "type": float, "min": 0.10, "max": 1.0,
        "fmt": lambda v: f"{v*100:.0f}%",
    },
    {
        "path": ["liquidity_limits", "min_cash_pct"],
        "label": "最低流动性要求（规则4）",
        "type": float, "min": 0.05, "max": 0.50,
        "fmt": lambda v: f"{v*100:.0f}%",
    },
    {
        "path": ["position_sizing", "max_single_add_pct"],
        "label": "单次加仓上限（规则6）",
        "type": float, "min": 0.01, "max": 0.30,
        "fmt": lambda v: f"{v*100:.0f}%",
    },
]


def _get_nested(d: dict, path: list[str]) -> Any:
    """取嵌套字段值，不存在返回 None。"""
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return None
        d = d[key]
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 核心：从人类可读 Markdown 文字解析规则参数
# ─────────────────────────────────────────────────────────────────────────────

def _get_rule_section(md: str, rule_num: int) -> str:
    """
    提取手册中指定规则编号的章节文字（从该规则标题到下一个规则标题之前）。
    找不到则返回空字符串。
    """
    pattern = rf"###\s*规则{rule_num}[^\n]*\n([\s\S]*?)(?=###\s*规则\d+|$)"
    m = re.search(pattern, md)
    return m.group(1) if m else ""


def parse_rules_from_markdown_text(md: str) -> dict:
    """
    从手册的人类可读 Markdown 文字中提取规则参数。

    用户只需修改手册里的表格数字/百分比，无需知道 JSON 字段名。
    系统通过正则匹配特征文字（"正常""可接受""警戒""安全区""超限" 等）
    定位对应的数值，自动识别用户的改动。

    各规则在自己的章节内单独解析，避免不同规则间的数字相互干扰。

    返回：部分规则字典（只含被识别到的字段），结构与 get_rules() 一致。
          识别不到的字段不包含在返回值中（调用方应保留现有值）。
    """
    # 先去掉 RULES_CONFIG JSON 块，只分析人类可读文字
    clean = re.sub(r"<!--\s*RULES_CONFIG[\s\S]*?-->", "", md)

    lev: dict = {}   # leverage_limits
    pos: dict = {}   # single_asset_limits
    liq: dict = {}   # liquidity_limits
    siz: dict = {}   # position_sizing

    # ── 规则1：总杠杆率四档阈值 ───────────────────────────────────────────
    #
    # 手册表格样式（用户修改 <X> / <Y> 处的数字）：
    #   | **≤ <X>**      | 🟢 正常  | ...  → leverage_ratio_normal_max     = X
    #   | **<X> ~ <Y>**  | 🟡 可接受 | ...  → leverage_ratio_acceptable_max = Y
    #   | **<X> ~ <Y>**  | ⚠️ 警戒  | ...  → leverage_ratio_warning_max   = Y
    #
    # 所有匹配限定在规则1的章节内，避免误命中其他章节。

    sec1 = _get_rule_section(clean, 1)

    m = re.search(r"\*\*≤\s*([\d.]+)\*\*[^\n]*正常", sec1)
    if m:
        lev["leverage_ratio_normal_max"] = float(m.group(1))

    m = re.search(r"\*\*([\d.]+)\s*[~～]\s*([\d.]+)\*\*[^\n]*可接受", sec1)
    if m:
        lev["leverage_ratio_acceptable_max"] = float(m.group(2))

    m = re.search(r"\*\*([\d.]+)\s*[~～]\s*([\d.]+)\*\*[^\n]*警戒", sec1)
    if m:
        lev["leverage_ratio_warning_max"] = float(m.group(2))

    # 杠杆ETF总持仓上限：总持仓上限 ≤ X%（也在规则1）
    m = re.search(r"总持仓上限[^\n%]*?≤\s*([\d.]+)%", sec1)
    if m:
        lev["level_1_max_pct"] = float(m.group(1)) / 100

    # ── 规则3：单一标的仓位限制 ───────────────────────────────────────────
    #
    # 手册表格样式：
    #   | ≤ <X>%  | 🟢 安全区 | ...  → warning_position_pct = X/100
    #   | > <Y>%  | 🚨 超限   | ...  → max_position_pct     = Y/100

    sec3 = _get_rule_section(clean, 3)

    m = re.search(r"≤\s*([\d.]+)%[^\n]*安全区", sec3)
    if m:
        pos["warning_position_pct"] = float(m.group(1)) / 100

    m = re.search(r">\s*([\d.]+)%[^\n]*超限", sec3)
    if m:
        pos["max_position_pct"] = float(m.group(1)) / 100

    # ── 规则4：流动性最低要求 ─────────────────────────────────────────────
    # 手册表格：| 正常市场 | ≥ X% |

    sec4 = _get_rule_section(clean, 4)
    m = re.search(r"正常市场[^\n]*≥\s*([\d.]+)%", sec4)
    if m:
        liq["min_cash_pct"] = float(m.group(1)) / 100

    # ── 规则6：单次加仓上限 ───────────────────────────────────────────────
    # 手册文字：单次加仓 ≤ 总投资性资产的 **X%**

    sec6 = _get_rule_section(clean, 6)
    m = re.search(r"单次加仓[^\n]*?\*\*([\d.]+)%\*\*", sec6)
    if m:
        siz["max_single_add_pct"] = float(m.group(1)) / 100

    result: dict = {}
    if lev:
        result["leverage_limits"] = lev
    if pos:
        result["single_asset_limits"] = pos
    if liq:
        result["liquidity_limits"] = liq
    if siz:
        result["position_sizing"] = siz
    return result


def apply_text_rules(base: dict, text_rules: dict) -> dict:
    """
    将从文字解析出的规则叠加到基础配置上，返回完整配置。
    text_rules 中不存在的字段保留 base 的值。
    """
    merged = copy.deepcopy(base)
    for section, values in text_rules.items():
        merged.setdefault(section, {}).update(values)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 校验 & Diff（供上传确认流程使用）
# ─────────────────────────────────────────────────────────────────────────────

def validate_rules_config(config: dict) -> list[str]:
    """
    校验参数合法性，返回错误信息列表（空列表 = 通过）。
    只校验 _TRACKED_FIELDS 中定义的字段，缺失字段不报错。
    """
    errors: list[str] = []
    for field in _TRACKED_FIELDS:
        val = _get_nested(config, field["path"])
        if val is None:
            continue
        label = field["label"]
        expected_type = field["type"]

        if expected_type is float and not isinstance(val, (int, float)):
            errors.append(f"「{label}」类型错误：应为数值，实际为 {type(val).__name__}")
            continue
        if expected_type is list and not isinstance(val, list):
            errors.append(f"「{label}」类型错误：应为列表")
            continue

        if expected_type is float:
            val = float(val)
            if "min" in field and val < field["min"]:
                errors.append(f"「{label}」值 {val} 低于允许下限 {field['min']}")
            if "max" in field and val > field["max"]:
                errors.append(f"「{label}」值 {val} 超过允许上限 {field['max']}")

    return errors


def diff_rules_config(current: dict, new: dict) -> list[dict]:
    """
    对比两个规则配置，返回有变化的字段列表。
    每项：{"label": str, "old": str, "new": str, "note": str}
    """
    changes: list[dict] = []
    for field in _TRACKED_FIELDS:
        old_val = _get_nested(current, field["path"])
        new_val = _get_nested(new, field["path"])
        if new_val is None or old_val == new_val:
            continue
        fmt = field.get("fmt", str)
        if field["type"] is list:
            old_str = "、".join(old_val) if old_val else "（无）"
            new_str = "、".join(new_val) if new_val else "（无）"
        else:
            old_str = fmt(float(old_val)) if old_val is not None else "（未设置）"
            new_str = fmt(float(new_val))

        note = ""
        path_key = ".".join(field["path"])
        if "level_0_forbidden" in path_key:
            note = "⚠️ 影响 Level0 违规检测"
        elif "leverage_ratio" in path_key:
            note = "⚠️ 影响杠杆率分级显示"
        elif "max_position_pct" in path_key or "warning_position_pct" in path_key:
            note = "⚠️ 影响单仓集中度告警"
        elif "min_cash_pct" in path_key:
            note = "⚠️ 影响流动性告警"

        changes.append({
            "label": field["label"],
            "old": old_str,
            "new": new_str,
            "note": note,
        })
    return changes


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具（仅供机器写入，用户不可见）
# ─────────────────────────────────────────────────────────────────────────────

def parse_rules_config(md: str) -> dict | None:
    """
    [内部] 从 <!-- RULES_CONFIG ... --> 块提取 JSON 配置。
    主要用于读取系统自动写入的机器元数据，用户不需要手动维护此块。
    """
    pattern = r"<!--\s*RULES_CONFIG\s*([\s\S]*?)-->"
    match = re.search(pattern, md)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None
