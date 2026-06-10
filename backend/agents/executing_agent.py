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

import json
import logging
import os
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
from backend.agents.skill_reconcile import reconcile_executing_skills

logger = logging.getLogger(__name__)


def _use_skill_retrieve_principles() -> bool:
    """C2 双轨 flag：默认开（走 invoke_skill），显式设 =0 切回老直连。"""
    return os.environ.get("WP_USE_SKILL_RETRIEVE_PRINCIPLES", "1") != "0"


def _adapt_retrieve_result(raw: dict) -> list:
    """将 invoke_skill("wp-retrieve-principles") 返回的 dict 转回 list[RetrievedChunk]。"""
    from backend.knowledge.schemas import RetrievedChunk
    chunks_data = raw.get("chunks", [])
    return [RetrievedChunk.model_validate(c) for c in chunks_data]


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
            elif route == "general":
                self._execute_general(out, planning_output, user_query)
            elif route in ("clarify", "low_confidence"):
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

            # v3.8.1: 对账层——只读 out.invoked_skills 和 planning_output，不写 out
            self._reconcile_and_log(planning_output, out)

            return out

        except Exception as e:
            logger.exception(f"[ExecutingAgent] task={out.task_id} 异常: {e}")
            out.mark_failed(str(e))
            return out

    # ────────────────────────────────────────────────────────
    # v3.8.1 对账层（旁路观测，不改 out，异常不冒泡）
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _reconcile_and_log(planning_output: PlanningOutput, out: ExecutionOutput) -> None:
        """对账层入口。只读 planning_output 和 out.invoked_skills，不写 out 任何字段。"""
        try:
            report = reconcile_executing_skills(
                route=planning_output.route,
                selected_skills=planning_output.selected_skills,
                invoked_skills=out.invoked_skills,
            )
            payload = {
                "route": report.route,
                "declared_exec": report.declared_exec,
                "invoked_exec": report.invoked_exec,
                "matched": report.matched,
                "declared_not_invoked": report.declared_not_invoked,
                "invoked_not_declared": report.invoked_not_declared,
                "pseudo_observed": report.pseudo_observed,
                "unknown_declared": report.unknown_declared,
                "unknown_invoked": report.unknown_invoked,
                "is_consistent": report.is_consistent,
                "has_unknown": report.has_unknown,
            }
            line = "[SKILL_RECONCILE] " + json.dumps(payload, ensure_ascii=False)
            if report.is_consistent and not report.has_unknown:
                logger.info(line)
            else:
                logger.warning(line)
        except Exception:
            logger.exception("[SKILL_RECONCILE_ERROR] reconciliation failed, ignored")

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

        v3.4 M8.2: 新建仓分支——当 loaded.is_new_entry=True 时:
        - 港股/未识别标的 → abort + 友好消息
        - 美股 → 跳过 signal_engine,跳过 weight_mismatch,保留 rule_engine 部分校验
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

        # ── M8.2: 新建仓分支(港股拦截 / 未识别标的) ──────────────
        if loaded.is_new_entry and loaded.market_not_supported_message:
            out.mark_aborted(
                reason="new_entry_market_not_supported",
                chat_answer=loaded.market_not_supported_message,
            )
            return

        # ── M8.2: 新建仓分支(美股新建仓评估) ─────────────────────
        if loaded.is_new_entry:
            self._execute_new_entry(out, loaded, asset_name, portfolio_id)
            return

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

        # ── Steps 4-6: 并行执行规则校验 + 信号生成 + 市场数据加载 ──
        # 这些步骤只读 Phase 1 输出（loaded / asset_name / wp_symbol），
        # 彼此无数据依赖，用 ThreadPoolExecutor 并行化。
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import sys, os as _os
        _bd = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _bd not in sys.path:
            sys.path.insert(0, _bd)

        # 准备市场数据参数
        wp_symbol = None
        if asset_name and loaded.target_position:
            tp = loaded.target_position
            ticker = getattr(tp, "ticker", "") or ""
            if ticker:
                from utils.symbol import infer_symbol_from_ticker
                currency = getattr(tp, "currency", "USD") or "USD"
                wp_symbol = infer_symbol_from_ticker(ticker, currency)

        from backend.core.demo_mode import PUBLIC_DEMO_MODE as _DEMO_EA

        if _DEMO_EA:
            # demo 模式：券商凭证行情源替换为 AKShare + 种子数据
            from backend.services.demo_market_service import fetch_demo_quote, fetch_demo_kline
            fetch_quote = lambda sym: fetch_demo_quote(sym)  # noqa: E731
            fetch_kline = lambda sym: fetch_demo_kline(sym)  # noqa: E731
            # fundamentals/capital_flow: 不可用，prompt 层声明
            fetch_fundamentals = lambda *a, **kw: None  # noqa: E731
            fetch_capital_flow = lambda *a, **kw: None  # noqa: E731
            logger.info("[ExecutingAgent] PUBLIC_DEMO_MODE — 行情走 AKShare/种子; fundamentals/capital_flow 不可用")
        else:
            _futu_available = _is_futu_opend_available() if wp_symbol else False
            if _futu_available:
                from services.market_data.futu_quote_service import fetch_quote
                from services.market_data.futu_capital_flow_service import fetch_capital_flow
            else:
                fetch_quote = lambda *a, **kw: None  # noqa: E731
                fetch_capital_flow = lambda *a, **kw: None  # noqa: E731
                if wp_symbol:
                    logger.info("[ExecutingAgent] Futu OpenD 未运行，跳过 Futu 数据源")
            from services.market_data.av_fundamentals_service import fetch_fundamentals
            from services.market_data.tiger_kline_service import fetch_kline

        # 定义并行任务
        def _task_discipline():
            return discipline_output_to_rule_result(invoke_skill(
                "wp-check-discipline",
                asset_name=asset_name, portfolio_id=portfolio_id, action_type="HOLD",
            ))

        def _task_signals():
            so = invoke_skill(
                "wp-generate-signals",
                asset_name=asset_name, portfolio_id=portfolio_id,
                action_type=intent_obj.action_type or "持有评估",
            )
            if so.error:
                raise RuntimeError(f"信号生成异常: {so.error}")
            return signals_output_to_signal_result(so)

        def _task_quote():
            return fetch_quote(wp_symbol) if wp_symbol else None

        def _task_capital_flow():
            return fetch_capital_flow(wp_symbol) if wp_symbol else None

        def _task_fundamentals():
            return fetch_fundamentals(wp_symbol) if wp_symbol else None

        def _task_kline():
            return fetch_kline(wp_symbol) if wp_symbol else None

        # 提交并行任务
        out.invoked_skills.extend(["wp-check-discipline", "wp-generate-signals"])
        if wp_symbol:
            out.invoked_skills.extend([
                "wp-fetch-realtime-quote", "wp-fetch-fundamentals",
                "wp-fetch-capital-flow", "wp-fetch-kline",
            ])

        rule_result = None
        signal_result = None
        quote = None
        fundamentals = None
        capital_flow = None
        technical = None

        with ThreadPoolExecutor(max_workers=6) as executor:
            fut_discipline = executor.submit(_task_discipline)
            fut_signals = executor.submit(_task_signals)
            fut_quote = executor.submit(_task_quote)
            fut_capital_flow = executor.submit(_task_capital_flow)
            fut_fundamentals = executor.submit(_task_fundamentals)
            fut_kline = executor.submit(_task_kline)

            # 收集结果（每个任务独立异常处理）
            try:
                rule_result = fut_discipline.result()
            except Exception as e:
                out.mark_failed(f"规则校验异常: {e}")
                return

            try:
                signal_result = fut_signals.result()
            except Exception as e:
                out.mark_failed(f"信号生成异常: {e}")
                return

            # 市场数据：失败不阻塞
            try:
                quote = fut_quote.result()
            except Exception as e:
                logger.warning(f"[ExecutingAgent] Futu 行情失败(不阻塞): {wp_symbol}: {e}")
            try:
                fundamentals = fut_fundamentals.result()
            except Exception as e:
                logger.warning(f"[ExecutingAgent] AV 基本面失败(不阻塞): {wp_symbol}: {e}")
            try:
                capital_flow = fut_capital_flow.result()
            except Exception as e:
                logger.warning(f"[ExecutingAgent] Futu 资金流向失败(不阻塞): {wp_symbol}: {e}")
            try:
                technical = fut_kline.result()
            except Exception as e:
                logger.warning(f"[ExecutingAgent] Tiger K线失败(不阻塞): {wp_symbol}: {e}")

        # ── 后置校验（依赖 rule_result）──

        # 未持仓 + 非买入 → ABORT
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
            out.mark_aborted(reason="not_in_portfolio_non_buy", chat_answer=chat_answer)
            return

        # 仓位口径一致性检查
        if (loaded.target_position is not None
                and not getattr(loaded.target_position, "is_virtual", False)):
            tp_weight = loaded.target_position.weight
            rule_weight = rule_result.current_weight
            if abs(tp_weight - rule_weight) > 0.005:
                out.mark_aborted(
                    "weight_mismatch",
                    f"仓位口径不一致：持仓={tp_weight:.2%} 规则={rule_weight:.2%}",
                )
                return

        # ── 写入结果 ──
        out.rule_result = rule_result
        out.skill_results["wp-check-discipline"] = {
            "violation": rule_result.violation,
            "warning": rule_result.warning,
            "current_weight": rule_result.current_weight,
        }
        out.signal_result = signal_result
        out.skill_results["wp-generate-signals"] = signal_result.to_dict()

        if wp_symbol:
            from services.market_data.schema import MarketDataBundle
            from datetime import datetime, timezone
            out.market_data = MarketDataBundle(
                symbol=wp_symbol, quote=quote, fundamentals=fundamentals,
                capital_flow=capital_flow, technical=technical,
                fetched_at=datetime.now(timezone.utc),
            )
            out.skill_results["wp-fetch-realtime-quote"] = {"available": quote is not None}
            out.skill_results["wp-fetch-fundamentals"] = {"available": fundamentals is not None}
            out.skill_results["wp-fetch-capital-flow"] = {"available": capital_flow is not None}
            out.skill_results["wp-fetch-kline"] = {"available": technical is not None}
            _has_kline = technical is not None and (not hasattr(technical, 'empty') or not technical.empty)
            logger.info(
                f"[ExecutingAgent] 市场数据: {wp_symbol} "
                f"quote={'✅' if quote else '❌'} "
                f"fundamentals={'✅' if fundamentals else '❌'} "
                f"capital_flow={'✅' if capital_flow else '❌'} "
                f"kline={'✅' if _has_kline else '❌'}"
            )

    # ────────────────────────────────────────────────────────
    # 新建仓路径（v3.4 M8.2）
    # ────────────────────────────────────────────────────────

    def _execute_new_entry(
        self,
        out: ExecutionOutput,
        loaded,
        asset_name: str,
        portfolio_id: int,
    ) -> None:
        """美股新建仓评估分支。

        与持仓分析的差异:
        - 跳过 pre_check(持仓数据校验对新建仓无意义)
        - 跳过 signal_engine(无历史持仓,无买卖信号)
        - 跳过 weight_mismatch 检查(虚拟持仓 weight=0)
        - 保留 rule_engine 的"组合熔断"部分(熔断时禁止新建仓)
        - av_fundamentals 已由 data_loader M8.3 加载,直接传递给 ExpressingAgent

        跳过项审计依据:
        - signal_engine: 需要 K 线历史数据,新建仓标的无持仓无 K 线,signal 无意义
        - pre_check: 校验"三要素齐全(画像+持仓+纪律)"对新建仓不适用(目标标的不在持仓)
        - weight_mismatch: 虚拟持仓 weight=0 vs rule_engine 的 current_weight=0 恒等,无需校验
        - M8.5 会接通完整的投资纪律 RAG 适配
        """
        logger.info(
            "[ExecutingAgent] 新建仓分支: asset=%s av_fundamentals=%s",
            asset_name,
            "available" if loaded.av_fundamentals else "unavailable",
        )

        # 标记新建仓特有的 skill 调用
        out.invoked_skills.append("m8-new-entry-analysis")

        # 部分 rule_engine: 仅检查组合熔断(M8.2 简版)
        # 完整纪律 RAG 适配留给 M8.5
        out.invoked_skills.append("wp-check-discipline-partial")
        try:
            discipline_output = invoke_skill(
                "wp-check-discipline",
                asset_name=asset_name,
                portfolio_id=portfolio_id,
                action_type="BUY",
            )
            rule_result = discipline_output_to_rule_result(discipline_output)
            out.rule_result = rule_result
            out.skill_results["wp-check-discipline"] = {
                "violation": rule_result.violation,
                "warning": rule_result.warning,
                "current_weight": 0,
                "is_new_entry": True,
            }
        except Exception as e:
            logger.warning("[ExecutingAgent] 新建仓 rule_engine 部分校验失败(不阻塞): %s", e)
            out.rule_result = None

        # signal_engine 显式跳过(不构造空 SignalResult,让下游知道"无 signal")
        out.signal_result = None
        out.skill_results["wp-generate-signals"] = {
            "skipped": True,
            "reason": "新建仓场景,无历史持仓数据,跳过信号生成",
        }

        # 市场数据: 对新建仓标的,AV fundamentals 已在 data_loader 加载
        # 尝试补充实时行情(如果 infer_symbol 能推断出 wp_symbol)
        if asset_name and loaded.target_position:
            tp = loaded.target_position
            ticker = getattr(tp, "ticker", "") or ""
            if ticker:
                try:
                    import sys, os as _os
                    _bd = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                    if _bd not in sys.path:
                        sys.path.insert(0, _bd)

                    from services.market_data.av_fundamentals_service import fetch_fundamentals
                    from services.market_data.schema import MarketDataBundle
                    from datetime import datetime, timezone
                    from utils.symbol import infer_symbol_from_ticker

                    wp_symbol = infer_symbol_from_ticker(ticker, "USD")
                    if wp_symbol:
                        fundamentals = loaded.av_fundamentals or fetch_fundamentals(wp_symbol)
                        out.market_data = MarketDataBundle(
                            symbol=wp_symbol,
                            quote=None,
                            fundamentals=fundamentals,
                            capital_flow=None,
                            technical=None,
                            fetched_at=datetime.now(timezone.utc),
                        )
                        out.skill_results["wp-fetch-fundamentals"] = {
                            "available": fundamentals is not None,
                            "is_new_entry": True,
                        }
                except Exception as e:
                    logger.warning("[ExecutingAgent] 新建仓市场数据加载失败(不阻塞): %s", e)

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
    # General 路径（v3.6 新增）
    # ────────────────────────────────────────────────────────

    # 投资相关关键词（用于子意图判断，避免闲聊被知识污染）
    _INVESTMENT_KEYWORDS = [
        "资产配置", "再平衡", "配置原则", "五大类", "资产类别",
        "投资纪律", "投资风格", "杠杆", "止损", "仓位",
        "建仓", "加仓", "减仓", "组合", "持仓",
        "权益", "固收", "另类", "衍生", "货币",
    ]

    def _execute_general(
        self,
        out: ExecutionOutput,
        planning_output: PlanningOutput,
        user_query: str = "",
    ) -> None:
        """
        v3.6 新增：general 路由的轻量执行。

        不调用 wp-load-context（不需要持仓/纪律），
        仅在 query 命中投资关键词时召回 allocation_principles。
        构造轻量 LoadedData（rules=None）。
        """
        from decision_engine.data_loader import LoadedData, UserProfile

        retrieved_principles = []

        if self._should_retrieve_principles(user_query):
            out.invoked_skills.append("wp-retrieve-principles")
            try:
                if _use_skill_retrieve_principles():
                    raw = invoke_skill(
                        "wp-retrieve-principles",
                        query=user_query,
                        source_types=["allocation_principles"],
                        top_k=3,
                    )
                    results = _adapt_retrieve_result(raw)
                else:
                    from backend.knowledge.store import KnowledgeStore
                    store = KnowledgeStore.get_instance()
                    if store.is_ready():
                        results = store.retrieve(
                            query=user_query,
                            source_types=["allocation_principles"],
                            top_k=3,
                        )
                    else:
                        results = []
                retrieved_principles = results
                out.skill_results["wp-retrieve-principles"] = {
                    "chunks_count": len(results),
                    "source_types": ["allocation_principles"],
                    "triggered_by": "investment_keyword_match",
                }
            except Exception as e:
                logger.warning(f"[ExecutingAgent] general 路由知识检索失败（不阻断）: {e}")
                out.skill_results["wp-retrieve-principles"] = {"error": str(e)}
        else:
            out.skill_results["wp-retrieve-principles"] = {
                "skipped": True,
                "reason": "non_investment_query",
            }

        out.loaded_data = LoadedData(
            profile=UserProfile(),
            positions=[],
            target_position=None,
            rules=None,
            research=[],
            total_assets=0.0,
            retrieved_principles=retrieved_principles,
            retrieved_research_views=[],
        )
        out.mark_completed()

    @staticmethod
    def _should_retrieve_principles(user_query: str) -> bool:
        """判断 general 路由下是否需要召回原则知识。"""
        if not user_query or len(user_query) < 2:
            return False
        return any(
            kw in user_query
            for kw in ExecutingAgent._INVESTMENT_KEYWORDS
        )

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
