# Real IBKR Six-Pattern AI-assisted Engineering Review

> Gate: `READY_FOR_GOVERNANCE_REVIEW`

This report records an AI-assisted engineering consistency review. It does not record independent human chart review and does not authorize production promotion.

## A. Summary

- Total cases: **120**.
- Detected candidates: **60**.
- Negative controls: **60**.
- Labels: **PASS=120**; all other allowed labels=0.
- Reviewer: `AI-assisted-engineering-review`.
- Reviewed at: `2026-08-24T21:08:35+08:00`.
- `human_review_complete` remains `false`; no independent human sign-off is claimed.

## B. Integrity Checks

- 120/120 case IDs reproduce from the frozen identity material and are unique.
- Canonical identity CSV matches the manifest 1:1; every row is `VALID`.
- 120/120 SVG files exist, parse as SVG, and carry the matching case ID, symbol, Pattern type, and source-bar hash.
- Detected candidates contain detector output, geometry facts, structure facts, source lineage, causal ordinals, and valid lifecycle observations.
- Negative controls retain the preregistered no-detection window contract and matching anchor date.
- Development is the only detector-opened partition; Holdout and Untouched Validation remain unopened.

## C. Pattern Family Findings

### Breakout

- Obvious contract issues: none found.
- Finding: Price-break and boundary evidence are internally consistent. Pending direction or volume facts remain explicit rather than being promoted to confirmation.
- Governance-attention cases: 5; listed below and not treated as production approval.

### Breakdown

- Obvious contract issues: none found.
- Finding: Support-break evidence and bearish confirmation states are internally consistent; this review does not infer short-trade semantics.
- Governance-attention cases: 4; listed below and not treated as production approval.

### Rectangle

- Obvious contract issues: none found.
- Finding: Range geometry, alternating touches, neutral direction, and NOT_REQUIRED direction confirmation are consistent.
- Governance-attention cases: 10; listed below and not treated as production approval.

### Ascending Triangle

- Obvious contract issues: none found.
- Finding: Horizontal resistance, rising support, convergence, and causal availability evidence are present.
- Governance-attention cases: 10; listed below and not treated as production approval.

### Double Top

- Obvious contract issues: none found.
- Finding: Peak similarity, reaction, neckline, lifecycle, and contextual volume evidence are internally consistent.
- Governance-attention cases: 8; listed below and not treated as production approval.

### Double Bottom

- Obvious contract issues: none found.
- Finding: Trough similarity, reaction, neckline, and required volume-gate semantics are internally consistent.
- Governance-attention cases: 6; listed below and not treated as production approval.

## D. Edge Case List

### Breakout

- `review_24ee797530a37a1035be` (JNJ): boundary authority below confirmation gate; volume gate pending; direction confirmation pending
- `review_40e6edf6a6d2dec98ff6` (MSFT): boundary authority below confirmation gate; volume gate pending; direction confirmation pending
- `review_4a5f16512cca933cb330` (AAPL): boundary authority below confirmation gate; volume gate pending; direction confirmation pending
- `review_4afbcf6655d0a8a07ab7` (IWM): volume gate pending; direction confirmation pending
- `review_5841889047deba67e095` (AGG): volume gate pending; direction confirmation pending

### Breakdown

- `review_8c951fe88002669c0953` (AGG): direction confirmation pending
- `review_a1ba38e0ada295052140` (IWM): boundary authority below confirmation gate; volume gate pending; direction confirmation pending
- `review_aee24f4982643240b8c9` (JPM): direction confirmation pending
- `review_d275bc5f25c6b94a03b6` (LQD): direction confirmation pending

### Rectangle

- `review_25fa041e89e48427ee23` (IEF): touch count at accepted minimum
- `review_30557d7e0fa7c76ab97b` (SHY): touch count at accepted minimum
- `review_46d61df7140cbf5a1b59` (AGG): touch count at accepted minimum
- `review_7972a8c09946af4b737a` (JNJ): touch count at accepted minimum
- `review_81697ac008f55cc563a0` (IEF): touch count at accepted minimum
- `review_948a8e66cebd6596fa2c` (LQD): touch count at accepted minimum
- `review_ae64e18123942089403b` (MSFT): touch count at accepted minimum
- `review_b7bae06eb6c19cc171d6` (JPM): touch count at accepted minimum
- `review_d75e491fcb6e77a9d74f` (QQQ): touch count at accepted minimum
- `review_e5bb95439df1010ecdf6` (SPY): touch count at accepted minimum

### Ascending Triangle

- `review_2e436aff320ee2a48496` (AAPL): one boundary touch count at accepted minimum; direction confirmation pending
- `review_37281afa17f3be770b56` (IEF): one boundary touch count at accepted minimum
- `review_3ed69893508aa67ec4d8` (IWM): one boundary touch count at accepted minimum
- `review_5845ee74b7a488dc1b4f` (JNJ): one boundary touch count at accepted minimum
- `review_857d1cf551ddf382248a` (SHY): one boundary touch count at accepted minimum; direction confirmation pending
- `review_a6c8932b42cc8c6b712f` (AGG): direction confirmation pending
- `review_bc5409decb1d483b842a` (TLT): one boundary touch count at accepted minimum; direction confirmation pending
- `review_bdbc9e23eb853b36aa2a` (LQD): one boundary touch count at accepted minimum; confirmation after 80% apex progress; direction confirmation pending
- `review_cf86fd63680ee41112a1` (JPM): one boundary touch count at accepted minimum; direction confirmation pending
- `review_d4377f90230c017036d3` (MSFT): one boundary touch count at accepted minimum; confirmation after 80% apex progress; direction confirmation pending

### Double Top

- `review_024b06864a69af139dc0` (IEF): reaction depth below 2%
- `review_0648e864d538822b39a2` (IWM): extreme similarity near tolerance boundary
- `review_191ca5e496e695a48d86` (TLT): reaction depth below 2%
- `review_2d4669f4a6e599086c7e` (MSFT): direction confirmation pending
- `review_70fcd2371253f7227058` (LQD): reaction depth below 2%
- `review_71bf31d595583cc3d37f` (SHY): reaction depth below 2%
- `review_dcbdada719e73983d6a5` (AAPL): direction confirmation pending
- `review_f13733f290cdffb34507` (AGG): reaction depth below 2%

### Double Bottom

- `review_0ff8c4f45e39104d21d1` (IWM): volume evidence close to hard gate
- `review_1b7cff46b823749f9f27` (AAPL): volume evidence close to hard gate
- `review_6a247bc8ffffc609e063` (LQD): reaction depth below 2%
- `review_ab09c2950b77ddd16c93` (AGG): reaction depth below 2%; direction confirmation pending
- `review_c1cb9864d69f3e991958` (AGG): reaction depth below 2%
- `review_ffd353f97db1ca6e6e8f` (IEF): reaction depth below 2%

These entries identify slope/touch/reaction/volume/boundary conditions close to a gate, or candidates whose direction confirmation remains pending. They are not detector failures and no parameters were changed.

## E. Limitations

AI-assisted review is not equivalent to human chart review. Production promotion requires governance approval.

The review checks evidence consistency, manifest integrity, and Pattern contracts. It does not supply independent visual judgment, optimize returns, inspect sealed validation partitions, change detector logic, or change calibration parameters.

## Safety

- Broker mutation = 0
- Order mutation = 0
- Portfolio mutation = 0
- ExecutionPlan mutation = 0
- Production DB change = 0
- Decision integration = 0
- Tovest modification = 0

Final status: `READY_FOR_GOVERNANCE_REVIEW`
