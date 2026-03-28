"""
数据加载模块 (Data Loader)

职责：加载决策引擎所需的全部数据，统一封装为 LoadedData。

数据来源（MVP）：
    - 用户画像：mock JSON（Portfolio 模型暂不含风险偏好，待后续扩展）
    - 持仓数据：通过公共聚合模块 app.utils.position_aggregator
              （与「投资纪律」页面使用完全相同的多平台融合逻辑和口径）
    - 投资纪律：Portfolio 模型 + discipline/config.py
    - 投研观点：ResearchViewpoint 模型（无数据时 fallback 到 mock）

口径说明：
    当前仓位 (weight) = 该标的聚合市值 / 所有投资类持仓总市值
    与「投资纪律 - 持仓集中度」完全一致，全系统唯一口径。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from app.database import get_session
from app.discipline.config import get_rules as _get_discipline_rules
from app.models import Portfolio, ResearchViewpoint, ResearchCard, ResearchDocument
from app.state import portfolio_id as default_portfolio_id
from app.utils.position_aggregator import (
    AggregatedPosition,
    aggregate_investment_positions,
    find_target,
)


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """用户投资画像（MVP 阶段 mock）"""
    risk_level: str = "中高"    # 低 / 中 / 中高 / 高
    goal: str = "长期增值"
    investment_years: int = 5   # 预计投资年限


@dataclass
class PositionInfo:
    """
    单一标的持仓信息（聚合后，每个标的唯一一条）。

    weight 始终等于 market_value_cny / total_assets，
    与「投资纪律 - 持仓集中度」口径完全一致。
    """
    name: str
    ticker: str
    asset_class: str
    weight: float              # 占投资组合总市值比例（0~1）
    market_value_cny: float    # 聚合市值（所有平台之和）
    cost_price: float          # 聚合成本（cost_value，非单价）
    current_price: float       # 当前价格（聚合持仓中首条，参考用）
    profit_loss_rate: float    # 加权盈亏率（小数）
    platforms: list[str] = field(default_factory=list)  # 持仓平台列表

    @classmethod
    def from_aggregated(cls, agg: AggregatedPosition) -> "PositionInfo":
        """从 AggregatedPosition 转换，保持字段语义一致。"""
        return cls(
            name=agg.name,
            ticker=agg.ticker,
            asset_class=agg.asset_class,
            weight=agg.weight,
            market_value_cny=agg.market_value_cny,
            cost_price=agg.cost_value,      # 聚合成本总额
            current_price=0.0,              # 聚合后无单价概念，置 0
            profit_loss_rate=agg.profit_loss_rate,   # 已是小数
            platforms=list(agg.platforms),
        )


@dataclass
class InvestmentRules:
    """投资纪律约束"""
    max_single_position: float  # 单一持仓上限（0~1）
    max_equity_pct: float       # 权益上限
    min_cash_pct: float         # 最低流动性
    max_leverage_ratio: float   # 最大杠杆率


@dataclass
class DataWarning:
    """数据质量告警，供 decision_flow 决定是否降级结论。"""
    level: str      # "error" | "warning"
    message: str


@dataclass
class LoadedData:
    """决策引擎所需全部数据，由 load() 返回"""
    profile: UserProfile
    positions: list[PositionInfo]           # 所有持仓（聚合后，每标的唯一一条）
    target_position: Optional[PositionInfo] # 被决策标的（聚合后）
    rules: InvestmentRules
    research: list[str]                     # 投研观点文本列表
    total_assets: float                     # 总投资性资产（人民币，与 discipline 同口径）

    # 原始数据（供 UI 展示用）
    raw_portfolio: Optional[object] = None

    # 歧义匹配：聚合后仍有多个候选时非空（此时 target_position 为 None）
    ambiguous_matches: list[PositionInfo] = field(default_factory=list)

    # 数据质量告警
    data_warnings: list[DataWarning] = field(default_factory=list)

    @property
    def has_required_data(self) -> bool:
        """前置校验用：三要素是否齐全"""
        return (
            self.profile is not None
            and len(self.positions) > 0
            and self.rules is not None
        )

    @property
    def has_data_errors(self) -> bool:
        """是否存在 error 级别的数据质量问题（应中断最终结论）"""
        return any(w.level == "error" for w in self.data_warnings)


# ── Mock 数据（用于 demo / 数据缺失时的 fallback）────────────────────────────

_MOCK_RESEARCH = {
    "理想汽车": [
        "看好 2025 年新车型产品周期，L9 / MEGA 销量稳定",
        "短期销量承压，市场竞争加剧，需关注月度交付数据",
        "公司现金流健康，自研芯片进展超预期",
    ],
    "腾讯": [
        "游戏业务回暖，海外收入增长明显",
        "监管环境趋于稳定，港股估值具备吸引力",
    ],
    "英伟达": [
        "AI 算力需求持续爆发，数据中心业务增长强劲",
        "估值偏高，短期存在波动风险",
    ],
}

_DEFAULT_MOCK_RESEARCH = [
    "暂无该标的的投研观点，建议自行研究或参考市场报告。"
]


# ── 投研缓存（联网搜索 + 卡片提炼）──────────────────────────────────────────

# 格式：{asset_name: (timestamp, list[str])}
_RESEARCH_CACHE: dict[str, tuple[float, list[str]]] = {}       # 联网搜索缓存，4h TTL
_CARD_DISTILL_CACHE: dict[str, tuple[float, list[str]]] = {}   # 卡片提炼缓存，24h TTL
_CACHE_TTL_SECONDS = 4 * 3600        # 联网搜索：4小时
_CARD_DISTILL_TTL = 24 * 3600        # 卡片提炼：24小时（用户资料不常变）


def _get_cached_research(asset_name: str) -> Optional[list[str]]:
    """返回缓存的联网搜索结果，未命中或已过期返回 None。"""
    entry = _RESEARCH_CACHE.get(asset_name)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _RESEARCH_CACHE[asset_name]
        return None
    return data


def _distill_research_cards(session, asset_name: str) -> list[str]:
    """
    从 ResearchCard 中读取 AI 解析的结构化内容，再调用 LLM 按重要性提炼 3-5 条投研要点。

    这解决了直接读 ResearchViewpoint 字段时内容残缺的根本问题：
    - ResearchCard 已有 thesis/bull_case/bear_case/key_drivers/risks/key_metrics 等全量字段
    - LLM 理解全部内容后按重要性归纳，输出完整结论句
    - 结果缓存 24h（用户资料不常变，避免重复调用）
    """
    # 命中缓存直接返回
    cached = _CARD_DISTILL_CACHE.get(asset_name)
    if cached is not None:
        ts, data = cached
        if time.time() - ts <= _CARD_DISTILL_TTL:
            return data

    try:
        # 查询该标的下所有已解析的 ResearchCard
        cards = (
            session.query(ResearchCard)
            .join(ResearchDocument, ResearchCard.document_id == ResearchDocument.id)
            .filter(ResearchDocument.object_name.ilike(f"%{asset_name}%"))
            .filter(ResearchDocument.parse_status.in_(["parsed", "saved_only"]))
            .order_by(ResearchDocument.uploaded_at.desc())
            .limit(5)
            .all()
        )

        if not cards:
            return []

        # 汇总所有卡片的结构化字段
        sections = []
        for card in cards:
            card_parts = []
            if card.thesis:
                card_parts.append(f"核心论点：{card.thesis}")
            if card.bull_case:
                card_parts.append(f"看多逻辑：{card.bull_case}")
            if card.bear_case:
                card_parts.append(f"看空风险：{card.bear_case}")
            if card.key_drivers:
                try:
                    drivers = json.loads(card.key_drivers)
                    if isinstance(drivers, list) and drivers:
                        card_parts.append("关键驱动：" + "；".join(str(d) for d in drivers[:4]))
                except Exception:
                    pass
            if card.risks:
                try:
                    risks = json.loads(card.risks)
                    if isinstance(risks, list) and risks:
                        card_parts.append("主要风险：" + "；".join(str(r) for r in risks[:3]))
                except Exception:
                    pass
            if card.action_suggestion:
                card_parts.append(f"操作建议：{card.action_suggestion}")
            if card_parts:
                sections.append("\n".join(card_parts))

        if not sections:
            return []

        combined = f"\n\n---\n".join(sections)

        # 调用 LLM 提炼
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            return []

        import openai as _openai
        client = _openai.OpenAI(api_key=openai_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=400,
            timeout=15,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是投研助手，擅长从结构化投研资料中提炼关键投资观点。"
                        "输出语言为中文，简洁专业。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"以下是用户上传的关于「{asset_name}」的投研资料解析内容：\n\n"
                        f"{combined}\n\n"
                        f"请从中提炼出最重要的3-5个投资观点，按重要性从高到低排序。\n"
                        f"要求：\n"
                        f"- 每条必须是完整的结论性句子，不少于15字，不超过60字\n"
                        f"- 禁止输出标题、前言、分节符\n"
                        f"- 每条以「- 」开头\n"
                        f"- 如果多份资料有矛盾，保留最重要的正反两面各一条"
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content.strip()

        # 解析条目
        lines = []
        for line in raw.split("\n"):
            cleaned = line.strip().lstrip("-•·*1234567890. \t").strip()
            if len(cleaned) >= 15:
                lines.append(cleaned)

        result = [f"[用户资料] {l}" for l in lines[:5] if l]

        if result:
            _CARD_DISTILL_CACHE[asset_name] = (time.time(), result)

        return result

    except Exception as e:
        print(f"[data_loader] 卡片提炼失败 ({asset_name}): {e}", flush=True)
        return []


def _search_research_online(asset_name: str) -> list[str]:
    """
    调用 Perplexity sonar-pro 联网搜索指定标的的最新投研观点。

    优先使用 PERPLEXITY_API_KEY；未配置时降级到 OPENAI_API_KEY + gpt-4o-search-preview。
    返回带 [联网参考] 前缀的字符串列表，供 LLM 与用户录入内容区分优先级。
    任何异常均静默处理，返回空列表，不影响主流程。
    """
    # 优先走缓存
    cached = _get_cached_research(asset_name)
    if cached is not None:
        return cached

    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if not perplexity_key and not openai_key:
        return []

    try:
        import openai as _openai

        if perplexity_key:
            # Perplexity API（格式与 OpenAI 兼容，sonar-pro 专为搜索优化）
            client = _openai.OpenAI(
                api_key=perplexity_key,
                base_url="https://api.perplexity.ai",
            )
            model = "sonar-pro"
        else:
            # 降级：OpenAI gpt-4o-search-preview
            client = _openai.OpenAI(api_key=openai_key)
            model = "gpt-4o-search-preview"

        response = client.chat.completions.create(
            model=model,
            max_tokens=600,
            timeout=20,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个专业的投资研究助手，擅长从市场最新信息中提炼简洁的投研观点。",
                },
                {
                    "role": "user",
                    "content": (
                        f"请搜索「{asset_name}」的最新投研观点，以中文返回3-5条简洁摘要。"
                        f"每条不超过60字，聚焦：近期基本面变化（业绩/产品/市场份额）、"
                        f"机构评级或目标价变动、主要风险点。"
                        f"格式要求：每条直接写结论性内容，以「- 」开头，"
                        f"禁止输出分节标题（如「**机构评级**:」），禁止输出来源链接和前言介绍。"
                    ),
                },
            ],
        )
        raw = response.choices[0].message.content.strip()

        # 解析返回列表（兼容"- ""• ""1. "等格式）
        lines = []
        for line in raw.split("\n"):
            stripped = line.strip()
            if not stripped or len(stripped) <= 8:
                continue
            # 去掉列表标记和行首 markdown 标记
            cleaned = stripped.lstrip("-•·*1234567890. \t").strip()
            # 去掉残余的 markdown 粗体标记（** 包裹）
            cleaned = cleaned.replace("**", "")
            # 跳过节标题行：内容以冒号结尾且不含实质信息
            if cleaned.endswith(":") or cleaned.endswith("："):
                continue
            if len(cleaned) > 8:
                lines.append(cleaned)
        result = [f"[联网参考] {l}" for l in lines[:5] if l]

        if result:
            _RESEARCH_CACHE[asset_name] = (time.time(), result)

        return result

    except Exception as e:
        print(f"[data_loader] 联网投研搜索失败 ({asset_name}): {e}", flush=True)
        return []


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def load(asset_name: Optional[str], pid: int = default_portfolio_id) -> LoadedData:
    """
    加载决策所需的全部数据。

    持仓数据通过公共聚合模块 app.utils.position_aggregator 加载，
    与「投资纪律 - 持仓集中度」使用完全相同的多平台融合逻辑。

    Args:
        asset_name: 被决策标的名称（来自意图解析），可为 None
        pid: portfolio_id

    Returns:
        LoadedData 实例
    """
    warnings: list[DataWarning] = []
    session = get_session()
    try:
        # ── 1. 投资纪律配置（Portfolio 表，由「投资纪律」模块统一管理）─────────
        # 注：删除「策略设定」Tab 后，Portfolio 表的唯一写入来源是「投资纪律」UI。
        # _MockPortfolio 仅在数据库无记录时作兜底，其默认值与 discipline/config.py 对齐。
        portfolio = session.query(Portfolio).filter_by(id=pid).first()
        if portfolio is None:
            portfolio = _mock_portfolio()
            warnings.append(DataWarning(
                level="warning",
                message="未找到投资纪律配置，使用纪律手册默认值（单标上限 40%，权益上限 80%）。"
            ))

        # ── 2. 持仓数据（通过公共聚合模块，口径与投资纪律完全一致）───────────
        agg_positions, total_assets = aggregate_investment_positions(pid)

        if not agg_positions:
            warnings.append(DataWarning(
                level="error",
                message="投资账户中暂无持仓数据，无法进行决策分析。"
            ))

        # ── 3. total_assets 异常检查 ─────────────────────────────────────────
        if total_assets <= 0:
            warnings.append(DataWarning(
                level="error",
                message=f"总资产异常（{total_assets:.2f}），数据可能存在问题，建议核实后重试。"
            ))

        # ── 4. 转换为 PositionInfo（保持对下游的兼容性）─────────────────────
        positions = [PositionInfo.from_aggregated(p) for p in agg_positions]

        # ── 5. 查找目标持仓（聚合后精确匹配）────────────────────────────────
        target_position: Optional[PositionInfo] = None
        ambiguous_matches: list[PositionInfo] = []

        if asset_name:
            agg_target, agg_ambiguous = find_target(agg_positions, asset_name)
            if agg_target:
                target_position = PositionInfo.from_aggregated(agg_target)
            elif agg_ambiguous:
                ambiguous_matches = [PositionInfo.from_aggregated(p) for p in agg_ambiguous]
            else:
                # 第三步：LLM 语义解析（精确/模糊匹配失败时）
                # 把用户描述 + 持仓列表交给 gpt-4.1-mini，利用模型泛化理解能力匹配
                inferred = _resolve_asset_by_llm(agg_positions, asset_name)
                if inferred:
                    target_position = PositionInfo.from_aggregated(inferred)

        # ── 6. 投资纪律规则 ──────────────────────────────────────────────────
        # 全部来自 discipline/config.py 的 get_rules()，与「投资纪律」页面同源，确保口径一致：
        #   max_single_position  ← single_asset_limits.max_position_pct       = 0.40
        #   max_equity_pct       ← asset_allocation_ranges.equity_max         = 0.80
        #   min_cash_pct         ← liquidity_limits.min_cash_pct              = 0.20
        #   max_leverage_ratio   ← leverage_limits.leverage_ratio_warning_max = 1.35（v1.4硬限）
        _dr = _get_discipline_rules()
        rules = InvestmentRules(
            max_single_position=_dr["single_asset_limits"]["max_position_pct"],
            max_equity_pct=_dr["asset_allocation_ranges"]["equity_max"],
            min_cash_pct=_dr["liquidity_limits"]["min_cash_pct"],
            max_leverage_ratio=_dr["leverage_limits"]["leverage_ratio_warning_max"],
        )

        # ── 7. 投研观点 ──────────────────────────────────────────────────────
        research = _load_research(session, pid, asset_name)

        # ── 8. 用户画像（MVP mock）──────────────────────────────────────────
        profile = UserProfile()

        return LoadedData(
            profile=profile,
            positions=positions,
            target_position=target_position,
            rules=rules,
            research=research,
            total_assets=total_assets,
            raw_portfolio=portfolio,
            ambiguous_matches=ambiguous_matches,
            data_warnings=warnings,
        )

    finally:
        session.close()


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _resolve_asset_by_llm(positions: list, asset_name: str) -> Optional[object]:
    """
    用 LLM 语义理解做资产名称解析，替代关键词规则匹配。

    当 find_target 精确/模糊匹配失败时调用。
    把用户描述的资产名 + 实际持仓列表送给 gpt-4.1-mini，让模型判断匹配哪个。

    优点：
    - 天然理解别名（招行=招商银行）、品类语义（稳健理财≈固收类）
    - 不需要维护规则表，模型自带泛化能力
    - Prompt 极短，延迟低（~300ms），成本约 $0.001/次

    结果按 asset_name 缓存（session 内，避免同一标的重复调用）。
    """
    # 命中缓存直接返回（key = asset_name，值 = 持仓名称字符串或 "NONE"）
    cache_key = f"_llm_resolve:{asset_name}"
    cached_name = _RESOLVE_CACHE.get(cache_key)
    if cached_name is not None:
        if cached_name == "NONE":
            return None
        for p in positions:
            if p.name == cached_name:
                return p
        return None

    try:
        import openai as _openai
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        # 构建持仓列表描述
        position_lines = "\n".join(
            f"- {p.name}（平台：{'、'.join(p.platforms) if p.platforms else '未知'}，类别：{p.asset_class or '未知'}）"
            for p in positions
        )

        client = _openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=50,
            timeout=8,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是资产名称匹配助手。用户用自然语言描述了一个持仓标的，"
                        "请从给定的持仓列表中找出最匹配的那一个。\n"
                        "规则：\n"
                        "1. 只输出持仓名称原文，不加任何解释\n"
                        "2. 如果无法确定或有歧义，输出 NONE\n"
                        "3. 理解平台简称（招行=招商银行，建行=建设银行等）\n"
                        "4. 理解品类描述（稳健/低风险≈固收类，进取/成长≈权益类）"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户描述：{asset_name}\n\n"
                        f"持仓列表：\n{position_lines}\n\n"
                        f"最匹配的持仓名称："
                    ),
                },
            ],
        )

        matched_name = response.choices[0].message.content.strip().strip("「」【】""''")

        # 写入缓存
        _RESOLVE_CACHE[cache_key] = matched_name

        if matched_name == "NONE":
            return None

        # 在 positions 里找到对应的对象
        for p in positions:
            if p.name == matched_name:
                return p

        return None

    except Exception as e:
        print(f"[data_loader] LLM 资产解析失败 ({asset_name}): {e}", flush=True)
        return None


# LLM 资产解析结果缓存（session 级，进程重启清除）
_RESOLVE_CACHE: dict[str, str] = {}


def _load_research(session, pid: int, asset_name: Optional[str]) -> list[str]:
    """
    加载投研观点，三层融合策略：
    1. 优先：ResearchCard 全量结构化字段 → LLM 提炼 3-5 条 [用户资料]（主来源）
    2. 补充：ResearchViewpoint 的 action_suggestion / invalidation_conditions 等字段
       （用户手动修订过的高置信度内容，最多2条）
    3. 兜底：联网搜索 [联网参考]（无用户资料时全量，有用户资料时补充1-2条）

    核心原则：从 ResearchCard 提炼是根本解，避免直接透传残缺字段。
    """
    if not asset_name:
        return _DEFAULT_MOCK_RESEARCH

    # ── 1. ResearchCard 提炼（主来源）────────────────────────────────────────
    card_research = _distill_research_cards(session, asset_name)

    # ── 2. ResearchViewpoint 补充高置信度字段（action_suggestion 等）──────────
    # 不再透传 thesis/supporting_points（容易残缺），只读操作建议和失效条件
    vp_supplement: list[str] = []
    viewpoints = (
        session.query(ResearchViewpoint)
        .filter(ResearchViewpoint.object_name.ilike(f"%{asset_name}%"))
        .order_by(ResearchViewpoint.updated_at.desc())
        .limit(3)
        .all()
    )
    for vp in viewpoints:
        if vp.action_suggestion and len(vp.action_suggestion.strip()) >= 15:
            vp_supplement.append(f"[用户资料] 操作建议：{vp.action_suggestion.strip()}")
        if vp.invalidation_conditions and len(vp.invalidation_conditions.strip()) >= 15:
            vp_supplement.append(f"[用户资料] 止损条件：{vp.invalidation_conditions.strip()}")
    vp_supplement = vp_supplement[:2]

    user_research = card_research + vp_supplement

    # ── 3. 联网搜索补充──────────────────────────────────────────────────────
    if not user_research:
        online = _search_research_online(asset_name)
        return online if online else _DEFAULT_MOCK_RESEARCH
    else:
        online = _search_research_online(asset_name)
        return user_research + online[:2]


def _safe_pct(value, default: float) -> float:
    """
    安全读取百分比字段，处理 None 和 > 1 的情况。

    规则：
    - None     → 返回默认值
    - 负值     → 抛出 ValueError（非法输入）
    - 0        → 返回 0.0（合法边界值）
    - (0, 1]   → 直接返回
    - (1, 100] → 除以 100
    """
    if value is None:
        return default
    v = float(value)
    if v < 0:
        raise ValueError(
            f"百分比字段包含非法负值：{value}。请检查策略配置，确保所有百分比 ≥ 0。"
        )
    if v > 1.0:
        v = v / 100.0
    return v


class _MockPortfolio:
    """当数据库中没有 Portfolio 时使用的默认值对象。
    所有值与 app/discipline/config.py (RULES) 对齐，确保行为一致。"""
    # 单标上限：对齐 RULES["single_asset_limits"]["max_position_pct"] = 0.40 → 40.0
    max_single_stock_pct = 40.0
    # 权益上限：对齐 RULES["asset_allocation_ranges"]["equity_max"] = 0.80 → 80.0
    max_equity_pct = 80.0
    # 杠杆：对齐 RULES["leverage_limits"]["leverage_ratio_max"] = 1.0 → 100.0
    max_leverage_ratio = 100.0
    # 以下为资产配置目标区间默认值（非规则校验字段）
    min_cash_pct = 0.0
    min_equity_pct = 40.0
    min_fixed_income_pct = 20.0
    max_fixed_income_pct = 60.0
    max_cash_pct = 100.0
    min_alternative_pct = 0.0
    max_alternative_pct = 20.0


def _mock_portfolio() -> _MockPortfolio:
    return _MockPortfolio()
