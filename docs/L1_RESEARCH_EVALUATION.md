# L1 Research Evaluation v1.0 — development candidate

## Product scope and authority

Research quality archive, separate from E1 formal investment decisions. PositionJobs still owns execution/idempotency, PositionStore owns original Artifact integrity, Evidence/S3/ACTIVE own factual/calculation/policy qualification. This feature does not invoke PEER, models, Providers or account services. It does not change Job v2/report v1, study prompts or trading permissions.

Fixed rubric `l1-research-evaluation.v1`: financial_evidence (财务事实与证据可信度), business_drivers (经营分析与商业价值驱动), competition_counterevidence (竞争优势与最强反证), valuation_returns (估值与长期潜在回报), decision_opportunity_cost (投资判断、机会成本与决策价值). Each is Decimal 0–2; server total 0–10, quality threshold >=8. Each score requires concrete basis, deductions and report references; these are reviewer statements, not independent verification of report truth. CI, claim acceptance and pipeline completion never become quality scores. No assessment means UNEVALUATED; a missing actual report means N/R (null total), never zero. Historical 7.6/10 FAIL remains FAIL.

## Private sample and storage contract

Accepted asynchronous L1 jobs automatically record their question and restricted original AnalysisRequest after the durable job claim, before Worker launch. Full unrelated `history` is excluded; only count and original request fingerprint remain. The question and explicit research_context may themselves contain personal research information: this archive is private, not an upload/export to Git. Never put credentials/full account details into questions. No broker credentials, account database or unrelated chat is read. Recovered legacy jobs without requests are UNKNOWN, not reconstructed.

Sample ID is SHA256 of schema identity plus opaque Job ID. The immutable request record binds job/run, fingerprint, code revision and creation clock. Derived lifecycle state may refresh after interrupted receipt writes; a finalized Artifact hash cannot change. Native/Reader versions include exact content hash; texts <=128 KiB are retained, larger reports use immutable original Artifact references. Large Evidence payloads are never copied. Original Artifact clocks are preserved; archive capture time is not fetch time. Missing source clocks/identity stay UNKNOWN.

Use the operator-provisioned analysis results root and a sibling `<analysis-results-dir>.evaluation` (same private operator boundary). Reuse existing PositionStore nofollow/private ownership checks, 0700 directory/0600 files, flock, fsync and atomic writes. Separate 64 MiB quality archive quota; full capacity stops quality writes, never evicts original Artifacts, receipts or evaluations. Queries expose usage/quota. Capacity increase or private backup/rotation is an operator action, not automatic deletion. Back up the evaluation sibling together with original results; copying only one loses either request/reviews or original evidence. No DB migration, dependency, policy activation or new production env var.

Each evaluation/feedback is an immutable JSON record with idempotency key. Same key/different body rejects. Re-evaluation requires revision link/reason; score difference is server computed. AI initial scores are `UNCONFIRMED_AI`; human declarations are `HUMAN_REPORTED`, not cryptographically verified identity. Subsequent reviews never overwrite history. Targets native, reader, client_draft, client_final_human_reported have distinct report hashes; distributions separate target and scorer. Feedback may link an evaluation, and cannot change its score.

Evaluation failures emit safe SAMPLE_CAPTURE_UNAVAILABLE diagnostic and cannot change normal research completion. Captures are retried from existing committed Artifact on ordinary Job reads; no research restarts. A failed initial request capture cannot be reconstructed from the old receipt fingerprint and is explicitly UNKNOWN. Recovery never invents an original question.

## MCP contract and client workflow

Four tools under the existing private MCP access boundary:

1. `query_research_evaluations`: filter canonical symbol, sample_id or job_id; fixed rubric, report hashes, status and cross-symbol distribution. No original report TTL.
2. `export_research_evaluations`: same filters, private original request, native/Reader content or immutable references, complete review/feedback history. Follow next_offset for samples; follow each history_next_offset using that sample_id/history_offset. History pages are independently bounded. This is a private paginated export, not a public URL or second Artifact store.
3. `save_research_evaluation`: exact sample/report hash, five scores plus evidence/basis, readability, factual/valuation checks, tags/suggestions, scorer/model/time; server recomputes total. Missing report uses null hash/ratings and N/R reason. Corrections require new request_id/revision_of/revision_reason.
4. `save_research_feedback`: append user comment or client draft; explicit human-reported final text can be added later. It does not overwrite the original report.

After displaying normal research, ChatGPT reads its sample and actual report, applies the fixed rubric and saves an explicitly labelled AI initial evaluation. A proposed outgoing text is `CLIENT_SUBMITTED_DRAFT_UNVERIFIED`: backend cannot observe ChatGPT's actual final reply. Even human-added final text is `HUMAN_REPORTED_FINAL_UNVERIFIED`; do not infer actual display from tool success. Exact report hash prevents evaluating a different version. Native and Reader assessment must not be relabelled as client-delivered assessment.

The new save tools are private quality-record writes (non-destructive/idempotent), not read-only investment tools. They grant no E1/policy/asset/order permission. No autonomous judge/paid calls. Live ChatGPT tool discovery and save/readback must be tested **after separately authorized deployment**, reusing an existing report; local MCP Client roundtrip is engineering evidence only. It must not be reported as production ChatGPT workflow PASS or 4.2 concentrated development CLOSED.

## Historical initialization and release boundary

Historical import must use actual local originals and record their real hash/time/version. Known score-only metadata with no actual report is a historical reference, not newly verified quality. Do not invent missing questions, dimensions, scoring basis, source fetch clocks or final client text. Production historical backfill is separately authorized. This PR performs only isolated import/replay and records unavailable material in its private delivery evidence.

Pre-release: four CI Gates, independent review and sample/review/export/protocol regressions. Deployment candidate: preserve results root, back up sibling, verify permissions, runtime exact revision and four new registered tools; reuse existing completed Job, save AI evaluation, read back deterministic total/revision/feedback, then business Chat confirms real workflow. No new paid research, E1 writes, policy mutation or trades required. Rollback code leaves original results and additive archive intact; do not delete receipts or archive.
