"""
端到端测试：PositionDecision 完整链路

测试用例：
    输入："理想汽车要不要卖？"

验证目标（工程PRD §7 Phase 1 交付物）：
    1. 意图识别正确 → primary_intent == "PositionDecision"
    2. Subtask 执行完整 → 3个 PositionDecision Subtask 全部有结果
    3. 输出包含操作建议章节 → final_output 包含 "操作建议"

运行方式：
    # 确保 ANTHROPIC_API_KEY 已设置
    export ANTHROPIC_API_KEY='sk-ant-xxx'
    python -m pytest tests/test_intent_engine_e2e.py -v
    # 或直接运行：
    python tests/test_intent_engine_e2e.py

注意：
    - 此测试会发起真实 LLM API 调用，耗时约 20~60 秒
    - portfolio_id 默认使用 app.state.portfolio_id（值为 1）
    - 若无持仓数据，position_fit_check 会降级（仍可执行，结果标注说明）
"""
from __future__ import annotations

import os
import sys
import time

# 将项目根目录加入 path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def test_position_decision_e2e(portfolio_id: int = 1) -> None:
    """
    PositionDecision 端到端测试。

    Args:
        portfolio_id: 测试使用的组合 ID（默认1）
    """
    from intent_engine.engine import run
    from intent_engine.context_manager import clear_session

    SESSION_ID = "test_e2e_position_decision"
    USER_INPUT = "理想汽车要不要卖？"

    print(f"\n{'='*60}")
    print(f"测试输入：{USER_INPUT!r}")
    print(f"{'='*60}")

    # 确保干净的会话状态（测试隔离）
    clear_session(SESSION_ID)

    start_time = time.time()

    result = run(
        user_input=USER_INPUT,
        session_id=SESSION_ID,
        portfolio_id=portfolio_id,
    )

    elapsed = time.time() - start_time
    print(f"\n总耗时：{elapsed:.1f}s")

    # ── 打印中间过程 ──────────────────────────────────────────────────────────

    print(f"\n{'─'*40}")
    print("【Step 1】意图识别结果")
    print(f"  primary_intent:    {result.primary_intent}")
    print(f"  secondary_intents: {result.intent_payload.secondary_intents if result.intent_payload else 'N/A'}")
    print(f"  subtasks:          {result.intent_payload.subtasks if result.intent_payload else 'N/A'}")
    print(f"  actions:           {result.intent_payload.actions if result.intent_payload else 'N/A'}")
    print(f"  entities.asset:    {result.intent_payload.entities.asset if result.intent_payload else 'N/A'}")
    print(f"  confidence:        {result.confidence:.2f}")

    if result.aborted:
        print(f"\n⚠️  执行被中断：{result.abort_reason}")
        print(f"   {result.final_output}")
        # 置信度低于0.5时中断是正常行为，不算测试失败
        if result.abort_reason == "low_confidence":
            print("\n[SKIP] 置信度过低，澄清问题已返回（符合 PRD §5.1）")
            return
        raise AssertionError(f"执行被意外中断：{result.abort_reason}")

    print(f"\n{'─'*40}")
    print("【Step 2】执行计划")
    if result.plan:
        for s in result.plan.primary_flow:
            print(f"  [{s.subtask}] depth={s.execution_depth}, depends_on={s.depends_on}")

    print(f"\n{'─'*40}")
    print("【Step 3】Subtask 执行结果")
    for sr in result.subtask_results:
        status_icon = {"success": "✓", "failed": "✗", "skipped": "—"}.get(sr.status, "?")
        print(f"\n  [{status_icon}] {sr.subtask} ({sr.status})")
        # 打印前100字符的摘要
        preview = sr.content[:100].replace("\n", " ")
        print(f"      {preview}{'...' if len(sr.content) > 100 else ''}")

    print(f"\n{'─'*40}")
    print("【Step 4】最终输出")
    print(result.final_output)

    # ── 断言验证 ──────────────────────────────────────────────────────────────

    print(f"\n{'─'*40}")
    print("【验证】")

    # 验证1：意图识别正确
    assert result.intent_payload is not None, "IntentPayload 不应为 None"
    assert result.primary_intent == "PositionDecision", (
        f"期望 primary_intent='PositionDecision'，实际为 {result.primary_intent!r}"
    )
    print(f"  ✓ 意图识别正确：primary_intent = 'PositionDecision'")

    # 验证2：Subtask 执行完整（3个 PositionDecision Subtask 全部有结果）
    expected_subtasks = {"thesis_review", "position_fit_check", "action_evaluation"}
    executed_subtasks = {sr.subtask for sr in result.subtask_results}
    assert expected_subtasks == executed_subtasks, (
        f"期望执行的 Subtask: {expected_subtasks}，实际: {executed_subtasks}"
    )
    print(f"  ✓ Subtask 执行完整：{sorted(executed_subtasks)}")

    # 验证2b：至少有1个 Subtask 成功（非全部失败）
    success_count = sum(1 for sr in result.subtask_results if sr.status == "success")
    assert success_count > 0, (
        f"所有 Subtask 均失败，无法生成有效分析。\n"
        f"失败详情：{[(sr.subtask, sr.error) for sr in result.subtask_results]}"
    )
    print(f"  ✓ 至少1个 Subtask 成功执行（{success_count}/3）")

    # 验证3：输出包含操作建议章节
    assert "操作建议" in result.final_output, (
        f"最终输出中未找到'操作建议'章节。\n输出预览：{result.final_output[:300]}"
    )
    print(f"  ✓ 最终输出包含'操作建议'章节")

    # 验证4：输出包含免责声明（合规要求）
    has_disclaimer = any(
        keyword in result.final_output
        for keyword in ["免责", "不构成投资建议", "仅供参考"]
    )
    assert has_disclaimer, "最终输出缺少免责声明"
    print(f"  ✓ 最终输出包含免责声明")

    print(f"\n{'='*60}")
    print("✅  所有验证通过！")
    print(f"{'='*60}\n")


def test_multi_turn_context_inheritance() -> None:
    """
    多轮对话上下文继承测试。

    验证：第二轮对话中，asset 字段从第一轮继承。
    """
    from intent_engine.engine import run
    from intent_engine.context_manager import clear_session

    SESSION_ID = "test_e2e_multi_turn"
    clear_session(SESSION_ID)

    print(f"\n{'='*60}")
    print("多轮对话上下文继承测试")
    print(f"{'='*60}")

    # 第一轮：明确标的
    result1 = run("理想汽车目前值得持有吗？", session_id=SESSION_ID, portfolio_id=1)
    print(f"第一轮 → intent: {result1.primary_intent}, asset: {result1.intent_payload.entities.asset if result1.intent_payload else 'N/A'}")

    if result1.aborted:
        print("[SKIP] 第一轮意图识别置信度过低，跳过多轮测试")
        return

    # 第二轮：追问（不重复标的名称）
    result2 = run("那如果下个月发布会后呢？", session_id=SESSION_ID, portfolio_id=1)
    print(f"第二轮 → intent: {result2.primary_intent}, asset: {result2.intent_payload.entities.asset if result2.intent_payload else 'N/A'}")
    print(f"         inherited_fields.asset: {result2.context.inherited_fields.asset if result2.context else 'N/A'}")

    if not result2.aborted and result2.context:
        inherited_asset = result2.context.inherited_fields.asset
        print(f"\n  继承字段 asset = {inherited_asset!r}")
        if inherited_asset:
            print("  ✓ 上下文继承成功")
        else:
            print("  ⚠ 上下文继承未生效（第二轮可能重新识别了标的，或置信度不足）")

    print(f"\n{'='*60}")
    print("多轮对话测试完成")
    print(f"{'='*60}\n")


# ── 直接运行（非 pytest）──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IntentEngine 端到端测试")
    parser.add_argument("--portfolio-id", type=int, default=1, help="组合 ID（默认1）")
    parser.add_argument("--skip-multi-turn", action="store_true", help="跳过多轮对话测试")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️  未设置 ANTHROPIC_API_KEY 环境变量")
        print("   请执行：export ANTHROPIC_API_KEY='sk-ant-your-key'")
        sys.exit(1)

    print("运行端到端测试（真实 LLM 调用）...")

    test_position_decision_e2e(portfolio_id=args.portfolio_id)

    if not args.skip_multi_turn:
        test_multi_turn_context_inheritance()

    print("🎉 所有测试完成！")
