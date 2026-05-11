"""
ExecutingAgent - PEER 4 Agent 之 Executing 角色。

职责（对标 agentUniverse ExecutingAgent）：
1. 按 PlanningOutput.selected_skills 顺序调用数据获取/计算分析类能力
2. 输出 ExecutionOutput 供 ExpressingAgent 消费
3. 在数据加载或前置校验失败时优雅 ABORT

设计哲学：
- 同步函数（非流式），接收 PlanningOutput → 输出 ExecutionOutput
- 内部子步骤按 route 分支执行
- 复用 v2.6 已稳定的底层实现（data_loader / rule_engine / signal_engine）
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.agents.contracts import (
    PlanningOutput,
    ExecutionOutput,
    AgentTaskStatus,
)
from backend.skills import invoke_skill
from backend.agents.adapters import (
    discipline_output_to_rule_result,
    signals_output_to_signal_result,
)

logger = logging.getLogger(__name__)


def _is_futu_opend_available(host: str = "127.0.0.1", port: int = 11111, timeout: float = 0.5) -> bool:
    """预检 Futu OpenD 是否可达（快速 socket 探测，不触发 SDK 重试）。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


class ExecutingAgent:
    """
    PEER 4 Agent 之 Executing 角色。

    使用方式：
        agent = ExecutingAgent()
        exec_out = agent.run(planning_output, user_query)
    """

    def __init__(self):
        pass

    def run(
        self,
        planning_output: PlanningOutput,
        user_query: str = "",
    ) -> ExecutionOutput:
        """
        执行 Executing 阶段。

        根据 PlanningOutput.route 分 3 类执行路径：
        - position_single / position_multi → 完整单标管道
        - portfolio → 组合级数据加载
        - general / clarify / low_confidence → 直通（不加载数据）
        """
        out = ExecutionOutput(task_id=planning_output.task_id)
        out.status = AgentTaskStatus.IN_PROGRESS

        try:
            route = planning_output.route

            if route in ("position_single", "position_multi"):
                self._execute_position(out, planning_output, user_query)
            elif route == "portfolio":
                self._execute_portfolio(out, planning_output, user_query)
            elif route in ("general", "clarify", "low_confidence"):
                self._execute_passthrough(out, planning_output)
            else:
                logger.warning(f"[ExecutingAgent] 未知路由: {route}")
                out.mark_aborted("unknown_route", f"未知路由 {route}")
                return out

            if not out.aborted and out.status == AgentTaskStatus.IN_PROGRESS:
                out.mark_completed()

            logger.info(
                f"[ExecutingAgent] task={out.task_id} route={route} "
                f"invoked={out.invoked_skills} aborted={out.aborted} "
                f"duration={out.duration_ms}ms"
            )
            return out

        except Exception as e:
            logger.exception(f"[ExecutingAgent] task={out.task_id} 异常: {e}")
            out.mark_failed(str(e))
            return out

    # ────────────────────────────────────────────────────────
    # 单标决策路径（PositionDecision）
    # ────────────────────────────────────────────────────────

    def _execute_position(
        self,
        out: ExecutionOutput,
        planning_output: PlanningOutput,
        user_query: str,
    ) -> None:
        """
        单标决策：data_loader.load → pre_check → rule_engine.check → signal_engine.generate

        复用 v2.6 decision_flow._run_pipeline 步骤 2-5 的实现逻辑。
        """
        from decision_engine import data_loader, rule_engine, signal_engine, pre_check
        from decision_engine.types import IntentResult

        intent_dict = planning_output.intent or {}
        asset_name = intent_dict.get("asset", "") if isinstance(intent_dict, dict) else ""
        portfolio_id = planning_output.portfolio_id

        # 构造 IntentResult（ExecutingAgent 需要它来调用 rule_engine/signal_engine）
        intent_obj = IntentResult(
            asset=asset_name,
            action_type=intent_dict.get("action_type", "持有评估") if isinstance(intent_dict, dict) else "持有评估",
            time_horizon="未知",
            trigger=None,
            confidence_score=intent_dict.get("confidence", 0.9) if isinstance(intent_dict, dict) else 0.9,
        )

        # ── Step 2: 数据加载（v3.0：通过 wp-load-context 组合 Skill）──
        out.invoked_skills.append("wp-load-context")
        try:
            ctx_output = invoke_skill(
                "wp-load-context",
                asset_name=asset_name,
                portfolio_id=portfolio_id,
                user_query=user_query,
            )

            if ctx_output.error:
                out.mark_failed(f"数据加载失败: {ctx_output.error}")
                return

            loaded = ctx_output.loaded_data
            if loaded is None:
                out.mark_failed("数据加载失败: loaded_data 为 None")
                return

        except Exception as e:
            out.mark_failed(f"数据加载失败: {e}")
            return

        if loaded.ambiguous_matches:
            out.mark_aborted(
                "ambiguous_match",
                f"标的匹配到多个候选，需要用户澄清",
            )
            out.skill_results["ambiguous_matches"] = [
                p.name for p in loaded.ambiguous_matches
            ]
            return

        if loaded.has_data_errors:
            error_msgs = [w.message for w in loaded.data_warnings if w.level == "error"]
            out.mark_aborted(
                "data_quality_error",
                "; ".join(error_msgs) or "数据质量异常",
            )
            return

        out.loaded_data = loaded
        out.skill_results["wp-fetch-holdings"] = {
            "positions_count": len(loaded.positions),
            "total_assets": loaded.total_assets,
        }
        out.skill_results["wp-fetch-research"] = {
            "items_count": len(loaded.research or []),
        }

        # ── Step 3: 前置校验 ──
        try:
            pre = pre_check.check(loaded)
            out.pre_check_result = pre  # 存到 output 供 _DECISION_STORE 使用
            if not pre.passed:
                out.mark_aborted("pre_check_failed", pre.message or "前置校验未通过")
                return
        except Exception as e:
            out.mark_failed(f"前置校验异常: {e}")
            return

        # ── Step 4: 规则校验（v3.0：通过 invoke_skill + Adapter）──
        out.invoked_skills.append("wp-check-discipline")
        try:
            discipline_output = invoke_skill(
                "wp-check-discipline",
                asset_name=asset_name,
                portfolio_id=portfolio_id,
                action_type="HOLD",
            )
            rule_result = discipline_output_to_rule_result(discipline_output)
        except Exception as e:
            out.mark_failed(f"规则校验异常: {e}")
            return

        # 未持仓 + 非买入 → ABORT（调 LLM 生成智能引导文本，对齐 v2 decision_flow.py）
        _buy_actions = ("买入判断", "加仓判断")
        if loaded.target_position is None and intent_obj.action_type not in _buy_actions:
            try:
                from decision_engine import llm_engine
                chat_answer = llm_engine.respond_not_in_portfolio(
                    user_query=user_query,
                    asset_name=asset_name or "该标的",
                )
            except Exception as e:
                logger.warning(f"[ExecutingAgent] respond_not_in_portfolio 失败: {e}")
                chat_answer = (
                    f"您当前的投资账户中没有「{asset_name}」的持仓记录。"
                    f"如需分析此标的，建议先在投资账户中录入相关信息，"
                    f"或告诉我您持仓的其他标的，我可以帮您分析。"
                )
            out.mark_aborted(
                reason="not_in_portfolio_non_buy",
                chat_answer=chat_answer,
            )
            return

        out.rule_result = rule_result
        out.skill_results["wp-check-discipline"] = {
            "violation": rule_result.violation,
            "warning": rule_result.warning,
            "current_weight": rule_result.current_weight,
        }

        # ── Step 5: 信号生成（v3.0：通过 invoke_skill + Adapter）──
        out.invoked_skills.append("wp-generate-signals")
        try:
            signals_output = invoke_skill(
                "wp-generate-signals",
                asset_name=asset_name,
                portfolio_id=portfolio_id,
                action_type=intent_obj.action_type or "持有评估",
            )

            if signals_output.error:
                out.mark_failed(f"信号生成异常: {signals_output.error}")
                return

            signal_result = signals_output_to_signal_result(signals_output)

        except Exception as e:
            out.mark_failed(f"信号生成异常: {e}")
            return

        # 仓位口径一致性检查
        if loaded.target_position is not None:
            tp_weight = loaded.target_position.weight
            rule_weight = rule_result.current_weight
            if abs(tp_weight - rule_weight) > 1e-6:
                out.mark_aborted(
                    "weight_mismatch",
                    f"仓位口径不一致：持仓={tp_weight:.2%} 规则={rule_weight:.2%}",
                )
                return

        out.signal_result = signal_result
        out.skill_results["wp-generate-signals"] = signal_result.to_dict()

        # ── Step 6: 市场数据加载（M1-b 新增,失败不阻塞）──
        if asset_name:
            try:
                import sys, os as _os
                _bd = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                if _bd not in sys.path:
                    sys.path.insert(0, _bd)

                # 预检 Futu OpenD 是否可达（0.5s 超时，不可达则跳过 Futu 数据源）
                _futu_available = _is_futu_opend_available()
                if _futu_available:
                    from services.market_data.futu_quote_service import fetch_quote
                    from services.market_data.futu_capital_flow_service import fetch_capital_flow
                else:
                    fetch_quote = lambda *a, **kw: None  # noqa: E731
                    fetch_capital_flow = lambda *a, **kw: None  # noqa: E731
                    logger.info("[ExecutingAgent] Futu OpenD 未运行（127.0.0.1:11111 不可达），跳过 Futu 数据源")

                from services.market_data.av_fundamentals_service import fetch_fundamentals
                from services.market_data.tiger_kline_service import fetch_kline
                from services.market_data.schema import MarketDataBundle
                from datetime import datetime, timezone

                # 构造 WP 内部 symbol（asset_name 可能是"特斯拉"而非 ticker）
                wp_symbol = None
                if loaded.target_position:
                    tp = loaded.target_position
                    ticker = getattr(tp, "ticker", "") or ""
                    if ticker:
                        # 推断市场后缀
                        ac = getattr(tp, "asset_class", "")
                        platforms = getattr(tp, "platforms", []) or []
                        if any("老虎" in p or "富途" in p or "雪盈" in p for p in platforms):
                            # 从已有 symbol 推断 .US / .HK
                            if ticker.isdigit() and len(ticker) >= 4:
                                wp_symbol = f"{ticker}.HK"
                            else:
                                wp_symbol = f"{ticker}.US"

                if wp_symbol:
                    out.invoked_skills.append("wp-fetch-realtime-quote")
                    quote = fetch_quote(wp_symbol)

                    out.invoked_skills.append("wp-fetch-fundamentals")
                    fundamentals = fetch_fundamentals(wp_symbol)

                    out.invoked_skills.append("wp-fetch-capital-flow")
                    capital_flow = fetch_capital_flow(wp_symbol)

                    out.invoked_skills.append("wp-fetch-kline")
                    technical = fetch_kline(wp_symbol)

                    out.market_data = MarketDataBundle(
                        symbol=wp_symbol,
                        quote=quote,
                        fundamentals=fundamentals,
                        capital_flow=capital_flow,
                        technical=technical,
                        fetched_at=datetime.now(timezone.utc),
                    )
                    out.skill_results["wp-fetch-realtime-quote"] = {
                        "available": quote is not None,
                    }
                    out.skill_results["wp-fetch-fundamentals"] = {
                        "available": fundamentals is not None,
                    }
                    out.skill_results["wp-fetch-capital-flow"] = {
                        "available": capital_flow is not None,
                    }
                    out.skill_results["wp-fetch-kline"] = {
                        "available": technical is not None,
                    }
                    logger.info(
                        f"[ExecutingAgent] 市场数据: {wp_symbol} "
                        f"quote={'✅' if quote else '❌'} "
                        f"fundamentals={'✅' if fundamentals else '❌'} "
                        f"capital_flow={'✅' if capital_flow else '❌'} "
                        f"kline={'✅' if technical else '❌'}"
                    )
            except Exception as e:
                logger.warning(f"[ExecutingAgent] 市场数据加载失败(不阻塞): {e}")

    # ────────────────────────────────────────────────────────
    # 组合级路径
    # ────────────────────────────────────────────────────────

    def _execute_portfolio(
        self,
        out: ExecutionOutput,
        planning_output: PlanningOutput,
        user_query: str,
    ) -> None:
        """
        组合级：data_loader.load(asset_name=None) + 意图特化 research 处理。
        """
        from decision_engine import data_loader

        portfolio_id = planning_output.portfolio_id
        intent_dict = planning_output.intent or {}
        primary_intent = intent_dict.get("primary_intent", "") if isinstance(intent_dict, dict) else ""

        # ── 数据加载（v3.0：通过 wp-load-context 组合 Skill）──
        out.invoked_skills.append("wp-load-context")
        try:
            ctx_output = invoke_skill(
                "wp-load-context",
                asset_name=None,
                portfolio_id=portfolio_id,
                user_query=user_query,
            )

            if ctx_output.error:
                out.mark_failed(f"组合数据加载失败: {ctx_output.error}")
                return

            loaded = ctx_output.loaded_data
            if loaded is None:
                out.mark_failed("组合数据加载失败: loaded_data 为 None")
                return

        except Exception as e:
            out.mark_failed(f"组合数据加载失败: {e}")
            return

        if loaded.has_data_errors or not loaded.positions:
            out.mark_aborted(
                "empty_or_error_portfolio",
                "投资账户中暂无持仓数据或数据异常",
            )
            return

        # ── 意图特化 research ──
        if primary_intent == "PortfolioReview":
            out.invoked_skills.append("wp-fetch-research")
            try:
                macro = data_loader.search_portfolio_research(loaded.positions)
                if macro:
                    loaded.research = macro
                out.skill_results["wp-fetch-research"] = {
                    "type": "macro", "items_count": len(macro or []),
                }
            except Exception as e:
                logger.warning(f"[ExecutingAgent] 宏观搜索失败（不阻断）: {e}")
                loaded.research = []

        elif primary_intent == "PerformanceAnalysis":
            loaded.research = []
            out.skill_results["wp-fetch-research"] = {"type": "cleared"}

        out.loaded_data = loaded
        out.skill_results["wp-fetch-holdings"] = {
            "positions_count": len(loaded.positions),
            "total_assets": loaded.total_assets,
        }

    # ────────────────────────────────────────────────────────
    # 直通路径
    # ────────────────────────────────────────────────────────

    def _execute_passthrough(
        self,
        out: ExecutionOutput,
        planning_output: PlanningOutput,
    ) -> None:
        """通用对话 / Clarify / 低置信度：不需要执行数据加载。"""
        out.status = AgentTaskStatus.SKIPPED
        out.skill_results["passthrough"] = {"route": planning_output.route}


# ════════════════════════════════════════════════════════════
# 模块级别快捷函数
# ════════════════════════════════════════════════════════════

_default_agent: Optional[ExecutingAgent] = None


def get_executing_agent() -> ExecutingAgent:
    """获取全局 ExecutingAgent 单例。"""
    global _default_agent
    if _default_agent is None:
        _default_agent = ExecutingAgent()
    return _default_agent
