---
name: wp-assess-position-evidence
description: Qualify existing single-position evidence and L1-L4 output granularity without writes or trading.
version: 1.0.0
type: function_call
entry_point: backend.graph.tools:call_tool
tool_name: assess_position_evidence
---

# wp-assess-position-evidence

Consumed by the original PEER orchestrator after market/Pattern collection and before Expressing.
Inputs are existing market_data, research, fixed asset_view, symbol, planner trace and principle availability.
Returns provenance records, conflict codes, missing evidence, deterministic L1-L4 qualification and a confidence ceiling.
Does not fetch/sync assets, alter investment discipline, prepare trades or create orders. L4 stays blocked in 4.2.
