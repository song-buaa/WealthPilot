"""
基金标的识别 + 盈米 MCP 数据注入（v2.6 M7 Step 2）

提供两个核心函数：
  is_likely_fund(asset_name, holdings, user_query) → bool
      启发式判别是否是基金标的
  fetch_fund_research_text(asset_name) → list[str]
      调用盈米 MCP 拿到基金研究文本，注入到 research 列表
"""
from __future__ import annotations
from typing import Optional


# 基金名称特征词（按出现频率排序，匹配任一即认为是基金）
FUND_NAME_KEYWORDS = [
    "ETF", "联接", "QDII", "混合", "纯债", "增强债", "可转债",
    "股票A", "股票C", "混合A", "混合C", "债券A", "债券C",
    "灵活配置", "中证", "沪深300", "中小盘", "科创", "创业",
    "纳指", "标普", "恒生", "黄金", "REITs",
    "前海开源", "易方达", "广发", "南方", "招商", "华夏",
    "景顺长城", "兴全", "中欧", "工银瑞信", "嘉实",
    # 兜底：直接含"基金"两字
    "基金",
]


def is_likely_fund(
    asset_name: str,
    holdings: list = None,
    user_query: str = "",
) -> tuple[bool, str]:
    """
    启发式判别 asset_name 是否是基金标的。

    Args:
        asset_name: 标的名称或代码（如 "华夏成长" / "000001"）
        holdings: 用户持仓列表（list[AggregatedPosition] 或 list[PositionInfo]）
        user_query: 用户原始问句

    Returns:
        (is_fund, reason) — reason 用于日志和调试
    """
    if not asset_name:
        return False, "asset_name 为空"

    asset_name_clean = asset_name.strip()

    # 规则 1：用户问句明确说"基金"
    if user_query and "基金" in user_query:
        return True, "用户问句含'基金'关键词"

    # 规则 2：从持仓里查找匹配
    if holdings:
        for h in holdings:
            h_name = getattr(h, "name", "") or ""
            h_ticker = getattr(h, "ticker", "") or ""
            if asset_name_clean in (h_name, h_ticker) or h_ticker == asset_name_clean:
                # 找到持仓，看 name 是否含基金关键词
                for kw in FUND_NAME_KEYWORDS:
                    if kw in h_name:
                        return True, f"持仓匹配且 name 含基金关键词 '{kw}'"
                # 持仓里有这个标的但 name 不含基金关键词，可能是股票
                return False, "持仓匹配但 name 不含基金关键词"

    # 规则 3：6 位纯数字 + 不在持仓里 + 用户问句含基金关键词 → 走 MCP
    # 如果用户没说"基金"，6 位数字可能是股票代码，不触发（歧义场景保持原有行为）
    if asset_name_clean.isdigit() and len(asset_name_clean) == 6:
        _FUND_INTENT_KW = ["基金", "ETF", "联接", "混合", "QDII", "FOF"]
        if user_query and any(kw in user_query for kw in _FUND_INTENT_KW):
            return True, "6 位纯数字 + 用户问句含基金关键词"
        # 歧义场景：不触发盈米 MCP
        return False, "6 位纯数字但用户未明确说基金（歧义场景）"

    return False, "未匹配任何基金特征"


# asset_name 里粘连的类型词后缀
ASSET_TYPE_SUFFIXES = (
    "这只基金", "这支基金", "这只股票", "这支股票",
    "基金", "股票", "ETF", "联接",
)


def normalize_asset_name(asset_name: str) -> str:
    """
    去掉 asset_name 末尾粘连的资产类型词。
    例：'000001这只基金' → '000001'
        '茅台股票' → '茅台'
    """
    if not asset_name:
        return asset_name
    cleaned = asset_name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in ASSET_TYPE_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
                cleaned = cleaned[:-len(suffix)].strip()
                changed = True
                break
    return cleaned or asset_name


def fetch_fund_research_text(asset_name: str, max_chars: int = 6000) -> list[str]:
    """
    调用盈米 MCP 拿基金研究文本，返回 list[str] 直接 append 到 LoadedData.research。

    流程：
      1. 调用 GetFundDiagnosis 拿全维度诊断报告（核心）
      2. 如果诊断成功，再补充 GetBatchFundPerformance 拿业绩数据
      3. 任一调用失败不影响主流程，返回 [] 让原有 research 兜底

    Returns:
        list[str]，每个元素是一段格式化文本，直接进 LLM prompt
    """
    results = []

    try:
        from backend.graph.tools import (
            execute_diagnose_fund,
            execute_fetch_fund_performance,
        )

        # 1. 全维度诊断（最有价值的单一数据源）
        diag = execute_diagnose_fund(fund_name_or_code=asset_name)
        if diag.success and diag.raw_text:
            results.append(
                f"[盈米基金诊断报告 - {asset_name}]\n{diag.raw_text[:max_chars]}"
            )

        # 2. 业绩数据（如果诊断成功且 asset_name 是 6 位数字）
        if diag.success and asset_name.isdigit() and len(asset_name) == 6:
            try:
                perf = execute_fetch_fund_performance(fund_codes=[asset_name])
                if perf.success and perf.raw_text:
                    results.append(
                        f"[盈米基金业绩指标 - {asset_name}]\n{perf.raw_text[:3000]}"
                    )
            except Exception:
                pass

    except Exception as e:
        print(f"[YingmiMCP] fetch_fund_research_text 异常: {e}", flush=True)

    return results
