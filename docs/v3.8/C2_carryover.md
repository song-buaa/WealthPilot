# C2 遗留待办（C0 验收中发现，先于 C0 存在）

## (a) user_query 透传 bug

**位置**: `backend/agents/executing_agent.py` L80-81

**现象**: `ExecutingAgent.run()` 收到 `user_query` 参数，但 L81 调用
`_execute_general(out, planning_output)` 时未透传。`_execute_general` 内部
退而求其次从 `intent["user_query"]` 取值（L626-631），但 orchestrator
构建的 `intent_payload`（`decision_graph.py` L303-309）从未写入 `user_query` 字段，
导致取到空串 `""`。

**影响**: general 路由的投资关键词匹配（`_should_retrieve_principles`）永远失败，
`wp-retrieve-principles` 在真实前端请求中从未被触发。

**修复预案**: `run()` 把 `user_query` 传进 `_execute_general`，
`_execute_general` 优先用传入的 `user_query`，不再依赖不存在的 `intent["user_query"]`。

---

## (b) 历史基线污染

**现象**: `docs/v3.8/v3.8.1_reconcile_validation_report.md` 和
`v3.8.2_validation_report.md` 中 Case5（general 投资关键词命中）和
Case6（general 非投资话题）的对账值来自 `tests/test_skill_reconcile.py`
的硬编码单元测试输入（`invoked=["wp-retrieve-principles"]` / `invoked=[]` +
`route="general"`），不是真实请求日志。

**证据**:
1. Case5 的 `matched=["wp-retrieve-principles"]` 在真实请求下因 (a) 的 bug 不可能产出
2. Case6 的 `route=general` 在真实请求下实际是 `low_confidence`
3. 报告值与单元测试的硬编码输入/断言值逐字段完全吻合

**要求**: C2 修复 (a) 后，必须用真实请求重建 general case 基线：
- 修复前预期: `invoked_exec=[]`, `is_consistent=False`
- 修复后预期: `invoked_exec=["wp-retrieve-principles"]`, `is_consistent=True`
- Case1-4（position / portfolio）的真实基线已在 C0 验收中验证可信，无需重建
