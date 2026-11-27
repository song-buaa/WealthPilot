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

The compatible model is the professional operating PE path with zero distributions. Revaluation retains the original five-calendar-year endpoint and rounded frozen terminal wealth. At decision time `t`, remaining years are 5 × seconds(t, endpoint) / seconds(original decision, endpoint). Total return is wealth / new price − 1, annualization uses the remaining term, and conditional price discounts over the remaining term. An identical price and decision clock preserves original scenarios exactly. The implied terminal hurdle uses the remaining term. Implied operating growth still refers to the original operating window. Nonzero payout needs a future dated cash-flow method. Scenario probabilities are outside this contract. Pair differences and tie bands are research comparisons, not position sizing. The existing research groups and quality gates apply. Phase 1.1 also evaluates bounded shared and issuer-specific rank perturbations.

The current price, source digests and provider counts are stored with the child; historic price-dependent inputs and quality retain their original clocks. The path uses neither L1 nor a language model. Captured provider counters identify the SEC/IR Owner invocations and AV transport attempts, and do not represent upstream retries hidden inside an Owner. Frozen rehearsal mode has no provider calls. No model or full research fallback is available.

Original bundles are never rewritten. Current-method readback recomputes the child from retained parent and freshly frozen inputs; method drift requires original version replay. Use `scripts/s3_relative_value_verify.py <child bundle.json>` for independent arithmetic and integrity checks using the same archive parent location. This verifier does not independently recertify SEC authenticity.

## Acceptance boundaries

The policy pool must preserve each configured issuer, compare only qualified same-endpoint cards, and leave account sizing unknown. Test class and policy resolution, method drift, same-price/clock equivalence, price changes, fixed endpoint, per-issuer refusal and legacy calls. Isolated evidence can qualify arithmetic and replay. Current live provider collection, eight-company financial coverage and the business quality of the product report require distinct evidence.

## Phase 1.1

Policy-scoped research adds separately versioned operating profiles and a company-quality dossier. Each native quality dimension carries a fact, an analyst judgment, a falsification condition and source proof. Qualified filing envelopes support business research only. Quality dossiers never override valuation, class, entitlement or price gates. New valuation parameters require explicit profile revision.

The 30-day price-reuse window starts at the operating profile review, even if a fresh parent has been saved since that review. The five-year terminal date still follows the parent decision clock. Relative stress compares shared revenue/PE shifts, company-specific shifts and operating-margin changes on the qualified subset. It reports return-spread reversals within the existing tie band and research ranks under the tested scenarios. These are finite stress tests without probabilities.

The approved denominator for Berkshire's candidate is the latest reviewed Class A and B cover share count expressed in Class B-equivalents at the economic ratio of 1:1,500. The numerator reconciles the issuer's six after-tax operating earnings categories for FY25 plus current nine months less comparable prior nine months. Separate growth inputs cover insurance underwriting, investment income, rail, energy, manufacturing/service/retail, and Other. Terminal earnings are capitalized once with no separately added investment portfolio, cash, or float. Shares, multiplicative exit value, and zero interim distributions settle via the existing deterministic S3 scenario. Berkshire has its own SEC-only repricing eligibility; no insurance IR market-monitoring claim is made. This method does not qualify the ordinary nonfinancial S3 quality floors.

NVDA's latest public results support the business dossier and current risks while the original SEC/EPS/flow gate continues to determine comparability. GOOG and META require separate ordinary entitlements with their preferred/forward structures. TSLA keeps only supported current operating facts while its separately bound EPS bridge is incomplete. ANT can resolve the issuer through exact SEC ticker, CIK and registration/prospectus evidence for qualitative research and a Class A quote. Its operating cash and equity economics remain a separate qualification.

## Phase 1.2 qualification evidence

The original 4.1 SEC, Evidence and Financial Research owners retain fact admission. NVDA's current 8-K contains quarterly statement values, but the frozen shared-owner replay emitted zero October observations. TSLA's original Exhibit 99.1 contains $0.24 directly reported GAAP Class ordinary diluted Q4 EPS in embedded machine-readable text. The shared table route emitted no observation. Neither value may be manually injected into the S3 qualified TTM. These two fixes require the bounded shared-owner contracts in the Phase 1.2 handoff. No use of annual-minus-three-quarter EPS or OCR.

The existing versioned dossier can attach `qualification_components` to a withheld security. Source labels must resolve to the same issuer's already qualified archival disclosures. Values represent components of an unresolved entitlement and confer no valuation or settlement eligibility. Alphabet and Meta reuse only the arithmetic from depositary units to preferred shares, annual coupon equivalent and gross conversion endpoints. Both retain actual cash/stock election, event-specific conversion and transaction roll-forward gates. Meta additionally evaluates the actual 46 million forward reference shares and the current Class C issuance deferral. The issuer's pre-trigger settlement and Class C case remain separate.

For ANT the branch reconciles disclosed pre-deal pro forma A/N shares and primary closing issuance, excludes secondary transfers from issuance, and separately shows the subsidiary-held legal shares. Its historical CFO-minus-specified-capital-outlays subtotal does not replace ordinary FCFE. Actual post-IPO dilution, net liquidity and all-in annual infrastructure cash timings remain unresolved. No whole-equity or stock-price valuation runs on these components.

Comparability and reprice paths are otherwise unchanged. Existing frozen bundles retain their original snapshots and read rules. Phase 1.2 neither adds recurring cash-flow settlement nor expands Policy or production access.

## 4.1 Financial Sources V2 consumer candidate

The professional ACTIVE US direct pool opts in to the existing current source
Owner for NVDA and TSLA. The frozen input stores AV observations and SEC
identity, metadata and original bytes with their true source clocks. The
official-disclosures current collector replays over those bytes, returns its
receipt to Evidence, and Financial Research derives the four-quarter EPS and
current operating bridge. Vendor-only observations stay in the current
receipt. Explicit standard and professional requests continue to use their
existing source contract. Older bundle bytes are immutable and method-drift
receipts still point to original-version replay.

NVDA's current GAAP EPS and partial operating bridge are reportable. A missing
parent-attributable net-income flow still withholds the EBIT-to-parent-income method.
TSLA uses the existing operating earnings method with listed GAAP EPS and an
issuer-specific reviewed scenario. Return is conditional on its specified
operating path against the entire listed close. Potential autonomous driving,
robotics and nonoperating investment asset values are unquantified. Projected
conversion does not repeat unrealized SpaceX gains. Current GAAP is retained
rather than adjusted without a qualifying tax/earnings bridge. Its bounded
price-only revalidation checks SEC submissions, listed class, split interval
and fixed endpoint. This scope lacks issuer IR and continuous event monitoring.

Dependency for PM3 integration: preserve PR #149 as the shared Owner and
PR #139 as the existing relative value candidate. The consumer delta needs
both reviewed histories. No changes to policy activation or execution rights.
## 4.5 多方法估值后继候选

方法应独立证明自身的现金与证券输入。完整持有人回报、限定经营回报和无数值资格分别展示；冻结的历史路径继续以原版本复算。

| 发行人 | 优先方法 | 当前证据及最小缺口 |
| --- | --- | --- |
| NVDA | 保留 GAAP 经营桥和 PE，显式现金分配 | 4.1 current V2 同窗 EPS、营业利润、CFO 可用，归母流量仍缺来源资格。FCFE 替代需要同窗完整资本现金支出、利息/净融资和基本普通股经济权益。11 月 17 日已宣告的 $0.25 属独立真实分配。 |
| GOOG | 正常化 EBIT 与 EV/EBIT；再桥接 Class C | 9M 及单季营业利润和集团现金披露可用于企业级情景，不依赖 Class C Q4 EPS。4.1 确认同窗 EBIT、已发生资产/债务、普通股股本与已发生 ATM；4.5 建立分状态优先股、Capped Calls 及普通股分配桥。 |
| META | 正常化经营价值或 FCFE；Class A 持有包价值 | 分派暂缓及 46m 远期合同已证实。4.1 提供已发行股份、优先股关闭及后续融资变动；4.5 需要 A 与 A+C 状态、各状态现金/股数调整、融资结算和最终每份 A 持有权益。 |
| ANT | 条件现金流恢复与融资稀释 | SEC 424B4 和交割证据支持收入、历史现金桥及 pro forma 股本。4.1 核实实际交割后经济股数、现金债务及合同；4.5 建立算力和租赁付款不重复、融资需求及剩余归属现金流。 |
| TSLA | 保留已运营业务，选择性独立建模 | 需自治和机器人可核验商业化与资本路径、已披露非经营投资的经济归属、税和退出限制；估计项可单列压力，不得混入既有估值基准。 |
| BRK.B | 保留 B 股经营盈利，研究避免重复计价的分部桥 | 需要可调配母公司现金、分部净债务/资本要求、承保与投资资产归属及剔除相关投资收益；继续保留原有限方法。 |
| AAPL | 现有经营盈利模型及普通股分配 | 保留合格经营档案；官方股息为未来条件分配假设的可追溯锚。 |
| MSFT | 现有经营盈利模型及普通股分配 | 保留合格经营档案；非经营投资价值仍有重要未知，分配以合格宣告为锚。 |

The candidate freezes modeled holder cash separately from operating profitability.
It uses one listed unit, USD, an unchanged five-year endpoint, pre-tax cash retained
without reinvestment and payment-date present value. A purchase at or after the
eligibility instant receives no pending historical distribution. AAPL and MSFT
use flat research schedules anchored to existing reviewed cash declarations; the
future schedule is illustrative. MSFT remains bounded because the professional
projection has no separate value for material nonoperating investments. TSLA and
BRK.B remain bounded; they have no formal position in the complete holder cohort.

GOOG's separate enterprise model uses nine-month revenue annualized as a
scenario convention and stresses revenue, EBIT margin and exit EV/EBIT. It never
uses the unqualified Class C EPS. Its preferred conversion states are gross
contract components, with no net common-share value until actual claims and
cash bridge reconcile. META's standstill and post-distribution packages are
distinct; no 50/50 split and no prebooking physical forward settlement. The
TSLA asset stress uses disclosed carrying value and cover shares plus existing
future share assumptions, separately from operating returns. It does not add
autonomy or robotics value. GOOG/META enterprise or claim states only describe
the original review on a price refresh.

A price refresh never re-collects a full dossier or rewrites the schedule.
A complete holder cohort gets formal ranks and pairwise stress. Bounded cards
retain their conditional numbers without a mixed-scope rank. Archived artifacts
use their original method version when source hashes differ.

### Holder scope and incremental issuer value bridges

Complete qualification uses reviewed ordinary-holder economic scope, source
hashes and a separately evidenced residual envelope when relevant. Residuals
are tested in decision space against the return threshold, cautious downside
and relative comparison, without a fixed asset percentage. Historical
transaction-scale sensitivity alone cannot supply a bounded residual envelope.

MSFT uses issuer-reconciled quarterly OpenAI-adjusted EPS and the matched
after-tax TTM earnings adjustment. Azure/royalty operating economics remain.
A separate exit stress uses public transaction scales without inferring a
realizable value from the as-converted stake. It retains the ordinary cash
schedule and withholds full qualification pending the material cash waterfall.

GOOG maps the existing operating EV to a conditional Class C value using the
September balance snapshot, the two verified EUR/CAD financing events,
implied cash retention, explicit tax/liquidity recoveries, ordinary share
change and unadjusted preferred conversion. The later contract overlay distinguishes
unused ATM capacity from issued stock, and applies a cash or net-share
capped-call offset once. It separately pairs assumed Waymo borrowing with
retained proceeds and records financing, conversion and liquidity as scenario
variables. The Class C path requires a qualified listed close and no historical EPS
substitute. Price-only replay verifies the issuer and filing clock again
and reuses the fixed terminal bridge and its cash schedule.

### 股东价值范围、估值资格与结论稳健性

公司卡保留数据资格、方法资格、股东价值范围和结论稳健性的区别。具备完整条件价值范围的公司可进入同口径比较。回报门槛或排序对重大已建模情景敏感时，保留条件回报并报告敏感性。真实经济条款缺失且无法限定重大影响时，显示限定回报或 UNKNOWN。口径完整不构成买入或配置依据。

- GOOG Class C 以经营 EV 加现金及投资回收，扣利息债务、租赁、少数股东权益及股息，以计入普通股变动、优先股转换、条件 ATM 与对冲结算后的股数折算。已发生的外币债务和未执行的融资授权独立处理；当前价格和历史 Class C EPS 资格保持独立。当前资料下的现金回收、用途、换汇和转换均为情景，九格压力测试改变经营增速和退出倍数。
- MSFT 维持剔除 OpenAI 投资损益的 $18.04 TTM EPS。额外压力情景同时检验未来私募权益退出、权益稀释、可兑现及税后比例、余下资本承诺和经营端分成到期。未有证据闭合当前私募工具的分配顺位和处置约束，完整范围仍不成立。
- NVDA 保留原经营桥的归母要求。独立上市 EPS 方法以同一时点的普通股稀释 EPS 为起点，将预测收入、营业利润率、条件税负、非经营转换和相对股数分开估算。以 $0.25 已宣告分配校准独立年度假设。买入者资格和付款仍待证，声明本身不计入现金收益。非经营投资资产未形成合格回收范围，输出限定股东回报。价格重估只更新证券资格、价格和剩余期限，保留原 EPS 与现金情景。
- ANT 的 4.1 单次估值报价收据有独立决策时点。上市 Class A 原始收盘通过该时点的估值观察资格；技术、动量和回测历史仍不达标。4.5 不把该收据回贴到另一时点或用来替代期初经济股本和净现金。
