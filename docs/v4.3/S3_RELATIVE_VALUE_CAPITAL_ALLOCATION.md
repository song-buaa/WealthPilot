# S3 Relative Value & Capital Allocation

WealthPilot 4.5 Phase 1 adds policy-scoped company research and price revaluation to the existing S3 company pool. This is an L2 cross-contract change. Phase 2 would add Chinese direct holdings and ETF opportunity costs. Phase 3 would combine research with 4.4 portfolio evidence and execution feasibility. Both require separate authorization.

The 4.3 Policy Owner resolves the current ACTIVE policy with the configured portfolio scope. S3 projects its direct US issuer targets using the 4.1 issuer registry, pins the policy reference and effective time, and searches no account positions. Alphabet contributes one issuer slot. The deterministic representative is the first sorted listed ticker, currently GOOG (Class C). Its economic return cannot be transferred to GOOGL. ANT resolves through the existing specific Class A identity. Any unresolved or duplicate issuer fails closed. No names, budget, or targets are embedded in the product pool.

## Calls and results

Use the existing `start_company_pool_research` with one new request ID and a request such as:

```json
{"scope":"active_us_direct","method":"professional","purpose":"Compare the ACTIVE direct US issuers and review the NVDA/ANT and MSFT/META hypotheses."}
```

For price revaluation add `parent_artifact_id` from a prior verified policy-scoped professional result. Do not pass symbols or model inputs in policy mode. The legacy explicit-symbol path still supports standard and professional. Poll the same job ID with `get_company_pool_research_result` and use `read_company_pool_research` for durable readback. Parent and child reside in the same private archive. Retain both together for independent replay. Free-form questions are interpreted by the host. The service computes a comparison and evidence boundaries, and does not interpret a requested hypothesis as an approved change.

The first operation reuses the S3 Evidence, quality floors, SEC/Price owners and the reviewed P2.2 operating dossier. The product projection exposes quality dimensions, methodology, reasons for unknowns, and policy targets. Qualitative judgment and strategic target changes require owner review. Existing research coverage only qualifies the three reviewed dossiers when their evidence still matches. A retained card with unknowns is not a passing professional assessment.

## Revaluation

The child checks parent integrity, exact method hashes and replay, policy pin, profile age (30 calendar days, a Phase 1 engineering limit), SEC issuer/class, recent filings and approved IR body fingerprints. It obtains current raw daily bars and split history through the original owners and requires the latest completed eligible session. This is a bounded check of observed sources. Unverified new competition or thesis changes require fresh review. Changed or missing evidence blocks the affected issuer. A changed policy pin requires an explicit new first research. No Policy, portfolio, or execution state is written.

The compatible model is the professional operating PE path with zero distributions. Revaluation retains the original five-calendar-year endpoint and rounded frozen terminal wealth. At decision time `t`, remaining years are 5 × seconds(t, endpoint) / seconds(original decision, endpoint). Total return is wealth / new price − 1, annualization uses the remaining term, and conditional price discounts over the remaining term. An identical price and decision clock preserves original scenarios exactly. The implied terminal hurdle uses the remaining term. Implied operating growth still refers to the original operating window. Nonzero payout needs a future dated cash-flow method. Scenario probabilities are outside this contract. Pair differences and tie bands are research comparisons, not position sizing. The existing research groups and quality gates apply. Joint scenario values are repriced, while joint rank perturbations are not replayed in this version.

The current price, source digests and provider counts are stored with the child; historic price-dependent inputs and quality retain their original clocks. The path uses neither L1 nor a language model. Captured provider counters identify the SEC/IR Owner invocations and AV transport attempts, and do not represent upstream retries hidden inside an Owner. Frozen rehearsal mode has no provider calls. No model or full research fallback is available.

Original bundles are never rewritten. Current-method readback recomputes the child from retained parent and freshly frozen inputs; method drift requires original version replay. Use `scripts/s3_relative_value_verify.py <child bundle.json>` for independent arithmetic and integrity checks using the same archive parent location. This verifier does not independently recertify SEC authenticity.

## Acceptance boundaries

The policy pool must preserve each configured issuer, compare only qualified same-endpoint cards, and leave account sizing unknown. Test class and policy resolution, method drift, same-price/clock equivalence, price changes, fixed endpoint, per-issuer refusal and legacy calls. Isolated evidence can qualify arithmetic and replay. Current live provider collection, eight-company financial coverage and the business quality of the product report require distinct evidence.
