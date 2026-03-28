"""
SubtaskRunner — 子任务执行模块

对应工程PRD §3.4。

职责：
    按 ExecutionPlan 执行每个 Subtask，包括数据拉取和 LLM 分析调用。

每个 Subtask 的执行步骤（PRD §3.4）：
    1. 拉取所需数据（DataRequirement 列表）
    2. 构建 Subtask Prompt（注入数据 + 上下文）
    3. 调用 LLM（单次）
    4. 返回 SubtaskResult

LLM 调用规格（PRD §3.4）：
    - 模型：claude-sonnet-4-20250514
    - 超时：15s
    - 重试：最多1次

硬约束（开发规范要求）：
    每个 Subtask Prompt 中必须包含：
    "只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全"

数据降级策略（PRD §3.4）：
    - 标的信息查不到：LLM 基于通识分析，加免责声明
    - 持仓数据不存在（未登录/空组合）：跳过持仓相关 Subtask
    - 市场数据接口超时：使用缓存/mock，加时效说明

Phase 1 数据来源：
    - portfolio_data: 通过 decision_engine.data_loader.load() 获取真实持仓
    - market_data / news: mock 数据（TODO Phase 2: 接入真实市场数据 API）
"""
from __future__ import annotations

import json
import traceback
from typing import Dict, List, Optional

import openai

from .types import (
    ExecutionContext,
    ExecutionPlan,
    SubtaskExecution,
    SubtaskResult,
)
from ._llm_client import MODEL_MAIN, get_client, reset_client

# 每个 Subtask 的 LLM 输出最大 token 数
_MAX_TOKENS = 600


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run(plan: ExecutionPlan, ctx: ExecutionContext) -> List[SubtaskResult]:
    """
    按 ExecutionPlan 顺序执行所有 Subtask（Phase 1: sequential only）。

    执行规则（PRD §3.3 并行执行同步规则）：
    - 若某 Subtask 依赖的前置 Subtask 失败，该 Subtask 标记为 skipped
    - 不中断整体流程，OutputRenderer 感知 skipped 状态并在对应章节注明

    TODO Phase 2: 支持并行执行（PortfolioReview 的并行 Subtask 组）
    """
    results: Dict[str, SubtaskResult] = {}

    # Phase 1: 只执行 primary_flow（secondary_flow 为空）
    for subtask_exec in plan.primary_flow:
        # 检查前置依赖（PRD §3.3 并行同步规则）
        if _has_failed_dependency(subtask_exec, results):
            results[subtask_exec.subtask] = SubtaskResult(
                subtask=subtask_exec.subtask,
                status="skipped",
                content="前置 Subtask 执行失败，该步骤已跳过。",
            )
            continue

        result = _execute_subtask(subtask_exec, ctx, results)
        results[subtask_exec.subtask] = result

    return list(results.values())


# ── 单个 Subtask 执行 ─────────────────────────────────────────────────────────

def _execute_subtask(
    subtask_exec: SubtaskExecution,
    ctx: ExecutionContext,
    prior_results: Dict[str, SubtaskResult],
) -> SubtaskResult:
    """执行单个 Subtask：拉取数据 → 构建 Prompt → 调用 LLM → 返回结果。"""
    subtask = subtask_exec.subtask

    # 1. 拉取数据
    try:
        fetched_data = _fetch_data(subtask_exec, ctx)
    except Exception as e:
        return SubtaskResult(
            subtask=subtask,
            status="failed",
            content="数据拉取失败，该部分分析暂时不可用。",
            error=str(e),
        )

    # 2. 构建 Prompt
    prompt = _build_prompt(subtask, ctx, fetched_data, prior_results)

    # 3. 调用 LLM（最多1次重试，PRD §3.4）
    for attempt in range(2):
        try:
            client = get_client()
            response = client.chat.completions.create(
                model=MODEL_MAIN,
                max_tokens=_MAX_TOKENS,
                timeout=15,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content.strip()
            return SubtaskResult(subtask=subtask, status="success", content=content)

        except EnvironmentError:
            raise

        except openai.APITimeoutError:
            return SubtaskResult(
                subtask=subtask,
                status="failed",
                content="该部分分析暂时不可用（请求超时）。",
                error="API timeout",
            )

        except Exception as e:
            if attempt == 0:
                reset_client()
                continue
            tb = traceback.format_exc()
            print(f"[SubtaskRunner:{subtask}] LLM 调用失败:\n{tb}", flush=True)
            return SubtaskResult(
                subtask=subtask,
                status="failed",
                content="该部分分析暂时不可用。",
                error=str(e),
            )

    # 不应到达此处
    return SubtaskResult(subtask=subtask, status="failed", content="未知错误。")


# ── 数据拉取 ──────────────────────────────────────────────────────────────────

def _fetch_data(subtask_exec: SubtaskExecution, ctx: ExecutionContext) -> dict:
    """
    按 DataRequirement 列表拉取数据（PRD §3.4 各 Subtask 的数据依赖）。

    返回 dict，键为 requirement.type，值为对应数据。
    """
    data: dict = {}
    asset = ctx.inherited_fields.asset or ctx.intent_payload.entities.asset

    for req in subtask_exec.data_requirements:
        if req.type == "portfolio_data":
            data["portfolio_data"] = _load_portfolio_data(ctx)

        elif req.type == "market_data":
            # TODO Phase 2: 接入真实市场数据 API（当前使用 mock）
            data["market_data"] = _mock_market_data(asset)

        elif req.type == "news":
            # TODO Phase 2: 接入真实新闻 API
            data["news"] = _mock_news(asset)

        elif req.type == "user_profile":
            data["user_profile"] = {
                "risk_level": ctx.user_profile.risk_level,
                "goal": ctx.user_profile.goal,
            }

    return data


def _load_portfolio_data(ctx: ExecutionContext) -> dict:
    """
    加载持仓数据（复用 decision_engine.data_loader）。
    PRD §3.4 数据降级：持仓数据不存在时返回空结构，后续 prompt 中说明。
    """
    try:
        from decision_engine.data_loader import load as de_load

        portfolio_id_str = ctx.inherited_fields.portfolio_id
        pid = int(portfolio_id_str) if portfolio_id_str else 1
        asset = ctx.inherited_fields.asset or ctx.intent_payload.entities.asset

        loaded = de_load(asset_name=asset, pid=pid)
        positions = [
            {
                "name": p.name,
                "ticker": p.ticker,
                "asset_class": p.asset_class,
                "weight": f"{p.weight:.1%}",
                "market_value_cny": f"¥{p.market_value_cny:,.0f}",
                "profit_loss_rate": f"{p.profit_loss_rate:+.1%}",
                "platforms": p.platforms,
            }
            for p in loaded.positions
        ]

        target = None
        if loaded.target_position:
            tp = loaded.target_position
            target = {
                "name": tp.name,
                "weight": f"{tp.weight:.1%}",
                "market_value_cny": f"¥{tp.market_value_cny:,.0f}",
                "profit_loss_rate": f"{tp.profit_loss_rate:+.1%}",
                "platforms": tp.platforms,
            }

        return {
            "positions": positions,
            "target_position": target,
            "total_assets": f"¥{loaded.total_assets:,.0f}",
            "max_single_position": f"{loaded.rules.max_single_position:.0%}",
            "research": loaded.research,
            "has_data": len(loaded.positions) > 0,
        }

    except Exception as e:
        # 数据加载失败降级（PRD §3.4）
        print(f"[SubtaskRunner] 持仓数据加载失败: {e}", flush=True)
        return {"has_data": False, "error": str(e)}


def _mock_market_data(asset: Optional[str]) -> dict:
    """
    Mock 市场基本面数据（PRD §3.4: Phase 1 使用 mock）。
    TODO Phase 2: 替换为真实市场数据 API 调用。
    """
    if not asset:
        return {"note": "未指定标的，无市场数据"}

    mock_db = {
        "理想汽车": {
            "company": "理想汽车（NASDAQ: LI / HK: 2015）",
            "sector": "新能源汽车",
            "pe_ratio": "32x（TTM）",
            "revenue_growth_yoy": "+15%",
            "cash_position": "约1000亿港元",
            "key_products": ["L9", "L8", "L6", "MEGA"],
            "recent_delivery": "2025年1月交付量约4.5万辆",
            "analyst_consensus": "中性偏正面，目标价区间分歧较大",
            "note": "以上为 mock 数据，仅供测试。TODO Phase 2: 接入真实数据源",
        },
        "腾讯": {
            "company": "腾讯控股（HK: 0700）",
            "sector": "互联网科技",
            "pe_ratio": "18x（TTM）",
            "revenue_growth_yoy": "+8%",
            "key_business": ["微信/WeChat", "游戏", "金融科技", "云服务"],
            "note": "以上为 mock 数据，TODO Phase 2: 接入真实数据源",
        },
    }

    for key, data in mock_db.items():
        if key in asset or asset in key:
            return data

    return {
        "company": asset,
        "note": f"暂无 {asset} 的结构化数据（mock 库未收录），将基于通识进行分析。",
        "disclaimer": "以下分析基于通识，不基于实时数据，请结合实际情况判断。",
    }


def _mock_news(asset: Optional[str]) -> list:
    """
    Mock 近期新闻（PRD §3.4: Phase 1 使用 mock）。
    TODO Phase 2: 替换为真实新闻 API 调用。
    """
    if not asset:
        return ["未指定标的，无新闻数据"]

    mock_news_db = {
        "理想汽车": [
            "2025-03: 理想汽车发布新款 L9 Pro，配置升级，售价维持不变",
            "2025-03: 受竞争加剧，2月销量环比下滑约10%，但同比仍增长",
            "2025-02: 公司宣布全面押注智能驾驶，追加10亿研发投入",
            "注：以上为 mock 新闻，TODO Phase 2: 接入真实新闻数据源",
        ],
        "腾讯": [
            "2025-03: 腾讯游戏海外收入持续增长，《PUBG Mobile》流水创历史新高",
            "2025-02: 微信月活跃用户突破13亿",
            "注：以上为 mock 新闻，TODO Phase 2: 接入真实新闻数据源",
        ],
    }

    for key, news in mock_news_db.items():
        if key in asset or asset in key:
            return news

    return [
        f"暂无 {asset} 的近期新闻（mock 库未收录）。",
        "以下分析基于通识，请结合实际情况判断。",
    ]


# ── Prompt 构建 ───────────────────────────────────────────────────────────────

def _build_prompt(
    subtask: str,
    ctx: ExecutionContext,
    fetched_data: dict,
    prior_results: Dict[str, SubtaskResult],
) -> str:
    """按 Subtask 类型构建分析 Prompt，每个 Prompt 必须包含硬约束声明。"""
    asset = ctx.inherited_fields.asset or ctx.intent_payload.entities.asset or "未指定标的"
    actions = ctx.intent_payload.actions
    action_str = "、".join(actions) if actions else "ANALYZE"

    if subtask == "thesis_review":
        return _prompt_thesis_review(asset, fetched_data, ctx)
    elif subtask == "position_fit_check":
        return _prompt_position_fit_check(asset, fetched_data, ctx)
    elif subtask == "action_evaluation":
        return _prompt_action_evaluation(asset, action_str, prior_results, ctx)
    else:
        return _prompt_generic(subtask, asset, fetched_data, ctx)


def _prompt_thesis_review(asset: str, data: dict, ctx: ExecutionContext) -> str:
    market = data.get("market_data", {})
    news = data.get("news", [])
    history_summary = _format_history(ctx)

    return f"""\
你是专业投资分析师。

【硬约束】只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全。
如数据不足，必须在结论中明确说明"数据不足，以下分析仅供参考"。

# 标的基本面数据（结构化）
{json.dumps(market, ensure_ascii=False, indent=2)}

# 近期相关新闻
{json.dumps(news, ensure_ascii=False)}

# 用户投资目标
- 风险偏好：{ctx.user_profile.risk_level}
- 投资目标：{ctx.user_profile.goal}
{history_summary}

# 任务：对 {asset} 进行投资逻辑评估（thesis_review）
1. 当前投资逻辑是否仍然成立？请基于上述数据说明
2. 关键支撑因素有哪些（仅列举数据中有依据的）
3. 有哪些重大风险或逻辑破坏因素（仅列举数据中有依据的）

输出要求：200字以内，简洁清晰，不重复引用原始数据条目\
"""


def _prompt_position_fit_check(asset: str, data: dict, ctx: ExecutionContext) -> str:
    portfolio = data.get("portfolio_data", {})
    history_summary = _format_history(ctx)

    if not portfolio.get("has_data"):
        # 持仓数据不可用时的降级 prompt（PRD §3.4）
        return f"""\
你是投资组合分析师。

【硬约束】只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全。

# 持仓数据状态
当前无法获取用户持仓数据（{portfolio.get('error', '未登录或组合为空')}）。

# 任务：{asset} 的组合适配性评估（position_fit_check）
由于缺少持仓数据，本次评估跳过持仓相关分析。

请输出：
"当前无法获取持仓数据，组合适配性分析不可用。建议用户登录并确认持仓后再进行评估。"\
"""

    positions = portfolio.get("positions", [])
    target = portfolio.get("target_position")
    research = portfolio.get("research", [])

    target_str = json.dumps(target, ensure_ascii=False) if target else f"用户当前不持有 {asset}"
    positions_str = json.dumps(positions[:5], ensure_ascii=False, indent=2)
    research_str = json.dumps(research[:3], ensure_ascii=False)

    return f"""\
你是投资组合分析师。

【硬约束】只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全。

# 用户持仓数据（TOP5，按市值排序）
{positions_str}

# 目标标的 {asset} 持仓情况
{target_str}

# 投研观点
{research_str}

# 组合配置上限
单一标的上限：{portfolio.get('max_single_position', '40%')}
总资产规模：{portfolio.get('total_assets', '未知')}

# 用户投资目标
- 风险偏好：{ctx.user_profile.risk_level}
- 投资目标：{ctx.user_profile.goal}
{history_summary}

# 任务：评估 {asset} 在当前组合中的适配性（position_fit_check）
1. 当前仓位是否合理（参考单一标的上限约束）
2. 与组合整体的集中度和相关性是否匹配用户风险偏好
3. 结合投研观点，该持仓是否与用户投资目标一致

输出要求：200字以内，直接给出评估结论\
"""


def _prompt_action_evaluation(
    asset: str,
    action_str: str,
    prior_results: Dict[str, SubtaskResult],
    ctx: ExecutionContext,
) -> str:
    thesis_content = _get_prior_content(prior_results, "thesis_review")
    position_content = _get_prior_content(prior_results, "position_fit_check")
    history_summary = _format_history(ctx)

    # Action 中文映射
    action_cn_map = {
        "SELL": "卖出", "BUY": "买入", "ADD": "加仓", "REDUCE": "减仓",
        "HOLD": "持有", "REBALANCE": "调仓", "TAKE_PROFIT": "止盈", "STOP_LOSS": "止损",
        "ANALYZE": "分析评估",
    }
    actions_cn = "、".join(
        action_cn_map.get(a, a) for a in ctx.intent_payload.actions
    ) if ctx.intent_payload.actions else "持有评估"

    return f"""\
你是投资决策助手。

【硬约束】只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全。
基于以下两项分析结果做出综合判断，不得引入任何结构化数据以外的信息。

# 投资逻辑评估结果（thesis_review）
{thesis_content}

# 组合适配性评估结果（position_fit_check）
{position_content}

# 用户操作意向
标的：{asset}
操作类型：{actions_cn}（{action_str}）
风险偏好：{ctx.user_profile.risk_level}
{history_summary}

# 任务：综合评估用户的 {actions_cn} 操作是否合理（action_evaluation）
1. 操作方向是否与投资逻辑一致
2. 当前时机是否合适（基于上述分析，不引入外部推断）
3. 最终操作评估：
   - 建议：支持 / 不支持 / 部分支持（必须明确选一）
   - 理由：简洁说明（2-3条关键理由）

输出要求：250字以内，结论明确，避免模糊措辞\
"""


def _prompt_generic(subtask: str, asset: str, data: dict, ctx: ExecutionContext) -> str:
    """
    通用 Subtask Prompt（为 Phase 1 未实现的 Subtask 提供基础分析能力）。
    TODO Phase 2/3: 为各 Subtask 实现专用 prompt。
    """
    return f"""\
你是专业投资分析师。

【硬约束】只能基于以下结构化数据作答，数据中未提供的内容不得推测或补全。

# 分析数据
{json.dumps(data, ensure_ascii=False, indent=2)}

# 用户信息
- 标的：{asset}
- 风险偏好：{ctx.user_profile.risk_level}
- 投资目标：{ctx.user_profile.goal}

# 任务
执行 {subtask} 分析，给出简洁专业的结论（200字以内）。\
"""


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _has_failed_dependency(
    subtask_exec: SubtaskExecution,
    results: Dict[str, SubtaskResult],
) -> bool:
    """
    检查是否有失败的前置依赖（PRD §3.3 并行执行同步规则）。
    注：只要有一个前置 failed，下游就标记为 skipped。
    """
    for dep in subtask_exec.depends_on:
        if dep in results and results[dep].status == "failed":
            return True
    return False


def _get_prior_content(results: Dict[str, SubtaskResult], subtask: str) -> str:
    """从前置 Subtask 结果中提取内容，供依赖 Subtask 的 prompt 使用。"""
    r = results.get(subtask)
    if r is None:
        return f"（{subtask} 尚未执行）"
    if r.status != "success":
        return f"（{subtask} 执行失败或已跳过：{r.content}）"
    return r.content


def _format_history(ctx: ExecutionContext) -> str:
    """格式化最近几轮对话历史，注入 prompt 作为背景（PRD §3.2 会话历史）。"""
    if not ctx.conversation_history:
        return ""
    lines = ["# 最近对话背景"]
    for turn in ctx.conversation_history[-3:]:  # 最多注入最近3轮
        lines.append(f"- 第{turn.turn_index}轮 [{turn.intent}]: {turn.summary}")
    return "\n".join(lines)
