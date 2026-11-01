---
name: wp-compare-quality-valuation
description: Compare an explicit US company pool using existing S3 qualified evidence, deterministic quality gates, scenarios and immutable research artifacts. Research Only.
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: compare_company_pool
---

# wp-compare-quality-valuation

Original invoke_skill entry for an explicit company-pool research request (symbols and purpose).
Operator-controlled archive/source paths are supplied only by the isolated internal Worker, never MCP client arguments.
Reuses SEC, Price, Evidence and S3 Owners; no new financial calculations, model forecasts or scoring policy.
Writes only the explicit private S3 frozen archive. Returns original source references, clock, assumptions, quality gates, noncomparables and ranking.
Candidate assumptions are not verified forecasts. A finite-window quality PASS is not comprehensive enterprise quality.
Company research priority is not portfolio allocation. Does not read holdings, activate policy, create orders or authorize execution.
This deterministic research Skill does not claim completion of four PEER model stages.
