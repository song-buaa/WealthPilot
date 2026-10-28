# WealthPilot 4.2 P0.1 — Unified US financial sources

## Scope and owner boundaries

Baseline: main `41e851bec2b1dc0610adb699b4f70b808df5f5fd` (PR #48).

| Owner | Reused contract / smallest change |
|---|---|
| `backend/services/instruments/issuers.py` | Adds exact SEC directory + Submissions financial-source resolution. Existing exposure mappings, positions, quantities and trading identity are unchanged. |
| `research_v2/sec_disclosures.py` | Public SEC discovery and standard facts/inline-XBRL adapter, bounded source reads and cache. No assets/database/financial calculation owner. |
| `research_v2/official_disclosures.py` | Unified collection entry, retaining reviewed LI IR/table layout as optional source-specific formatting. Cards cannot nominate SEC accessions. |
| `backend/graph/tools.py` | Original evidence Skill collects US official facts even when AV is completely unavailable. |
| Existing Evidence Registry | Independently verified per-field quarterly/annual observations; original AV matching/conflict and TTM rules. |
| Original PEER / Output Policy | Same qualified records, references, explanations and final filtering; no model/Prompt/research engine replacement. |

## Discovery and trust

1. Exact US ticker from SEC `company_tickers.json` resolves to one CIK; duplicate/no mapping fails closed. No fuzzy names, guessed CIK or manual per-symbol code.
2. The CIK's official Submissions must agree on CIK and ticker. Actual filing forms distinguish domestic (10-Q/10-K) and foreign-private (20-F/6-K) regimes; ambiguous transitions remain unknown. Existing LI/PDD issuer IDs are reused; other issuers get `SEC_CIK_<10-digit CIK>` for financial-source identity only.
3. Submissions provides accession, form, filing day, acceptance timestamp, report date and primary document. Missing acceptance time retains filing-day precision; fiscal end is never used as publication. Original full submissions must independently match CIK, accession, form and acceptance clock.
4. CompanyFacts is preferred, selecting entity-wide standard US-GAAP/IFRS concepts by exact accession/form/filing day, duration, actual currency/unit and context. Quarterly and annual values stay distinct. A recent event-only filing cannot crowd out structured financials.
5. Missing fields can use original full submissions including their attachments. Inline-XBRL requires the same issuer CIK, explicit duration and currency, no dimensional contexts, explicit supported concepts/transformations. Contexts cannot cross exhibits. Existing LI table parsing is reused after SEC envelope verification; no new LI-specific SEC parser.
6. Reads are HTTPS-only, strict SEC host/path allowlists, no redirects/query parameters/arbitrary card URLs, 8s per read, 16 MiB SEC maximum, no retries, at most four original submissions. Existing LI IR limits remain unchanged. SEC directory/facts/record caching uses the existing cache owner and TTL; source fetched_at is preserved. Demo/mock never opens the network.
7. Only server-injected source lookup grants trust. Record/card claims of SEC URL, identity, hash, date or matching AV amounts do not. Evidence carries source/document identity, original hash, source locator, issuer, currency, accounting basis and separate source/fiscal/fetch clocks.

## Field fusion and degradation

AV remains the standardized input and the sole existing TTM computation path. SEC supplements independently verified raw quarter/annual L1 facts when AV is absent. No SEC-only TTM engine, quarterly subtraction or synthetic Q4 is added.

For AV fields without publication metadata, the existing linker needs every exact quarterly component (including prior-year comparisons and both OCF/CapEx for FCF), with matching issuer, period, accounting basis, currency, unit and value. Annual observations cannot certify quarterly TTM; any conflicting same-basis source prevents selecting only a matching source. Source amounts are never overwritten. Legacy disclosures lacking an explicit accounting basis keep the existing conservative conflict-warning behavior; that warning cannot grant source/publication qualification. Explicitly different bases are not equated.

US-GAAP `NetIncomeLoss` is explicitly parent-attributable, separate from consolidated `ProfitLoss`; it may support a labelled L1 fact but cannot supply the consolidated AV net-income clock or generate a false same-basis conflict. Negative operating cash flow remains valid risk evidence. Payment concepts require nonnegative spending; no blanket absolute-value operation is used. Reviewed LI negative CapEx normalization still requires its original cash-flow reconciliation.

Existing 150-day quarter and 450-day annual field policies are used; existing TTLs and L2–L4 gates are not relaxed. Unknown publication, YTD/mixed/instant/dimensional contexts, unsupported concepts/currencies/attachments, conflicting aliases, missing sources and access failures remain unknown. No PDF/OCR or broad text/AI extraction is added.

## Representative validation and honest limits

| Case | Offline verification | Public-source verification in this task |
|---|---|---|
| AAPL | Exact CIK 320193, 10-Q/10-K/8-K API contracts, quarterly/annual facts, original inline-XBRL, AV missing-field/clock and rejection paths. Financial amounts in API-contract tests are synthetic. | Official 2025-06-28 10-Q address/accession found. No successful local Submissions/CompanyFacts download; those real API results are UNKNOWN. |
| LI | CIK 1791706, 6-K discovery/envelope, existing real 2026 Q2 frozen IR table inside synthetic SEC envelope; four actual original amounts/FCF sign match. Existing real IR regression is retained. | SEC full submission `0001104659-26-101065` independently readable through web source: filer/form/acceptance and attached financial release confirmed. Local SEC raw download denied; the composite test is not represented as a downloaded SEC fixture. |
| PDD | Exact different CIK 1737806, foreign-private regime, 20-F discovery/annual facts and unsupported attachments. | Official Submissions and 2025 20-F accessible via web source. Local API download denied; no claim of real financial extraction from that API. |

Public read diagnostics: local official reads encountered HTTP 403 or TLS verification errors; TLS verification was not disabled and no access-control bypass was used. Those failures do not make the API-contract fixtures real API snapshots. No paid model, AV, broker or production research was called. A future authorized runtime check must establish SEC access from the actual release environment before claiming full live source acceptance. No new purchase/key is required by SEC's API.

Official references:
- [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [AAPL actual 10-Q](https://www.sec.gov/Archives/edgar/data/320193/000032019325000073/aapl-20250628.htm)
- [LI Q2 full 6-K](https://www.sec.gov/Archives/edgar/data/1791706/000110465926101065/0001104659-26-101065.txt)
- [PDD Submissions](https://data.sec.gov/submissions/CIK0001737806.json)
- [PDD 20-F](https://www.sec.gov/Archives/edgar/data/1737806/000110465926050727/pdd-20251231x20f.htm)

## Adding an ordinary US stock

A ticker present unambiguously in SEC's directory/Submissions with supported standard concepts needs no symbol-specific code: it enters this same discovery/qualification/PEER path. This does not promise every issuer/field/layout is supported. Missing standard taxonomy/ambiguous identity/oversized or unsupported source stays unknown. Recent Submissions history is used; arbitrary historical archive crawling is outside this bounded adapter.

## Authorization and release

This PR changes no async MCP, IBKR OHLCV, model/PEER configuration, asset/target/discipline, Schema, broker synchronization, budget ledger or trading authority. Public-source/local offline evidence is not production acceptance. Merge and deployment require separate authorization.

Final test/CI results are recorded in the PR and private delivery evidence under `/Users/songbin/.codex/outputs/wealthpilot-us-financial-20261028/`.

## PR49 blocking follow-up: operator identity and actual full-submission layout

- SEC HTTP requests now require **operator-supplied real** `WEALTHPILOT_SEC_ORGANIZATION` and `WEALTHPILOT_SEC_CONTACT_EMAIL`. No default organization/mailbox is invented. Syntax/placeholder checks cannot establish actual mailbox ownership; the operator must configure truthful values. Missing/invalid identity prevents a send. Backend uses its environment; isolated Business Worker uses those same keys in the existing private analysis-env JSON. Only that configuration allowlist changes, not async MCP jobs, IPC or model policy.
- HTTPS verification, official host/path allowlists, no redirects, 8-second timeout, per-process 4/s limit and no retries remain. Access failures stop further SEC fetches; valid JSON without standard facts may use bounded same-filing originals. Safe categories distinguish identity missing, HTTP403/other HTTP, TLS, DNS, proxy connection, timeout/network, invalid JSON and missing fields. They expose neither identity values nor raw errors/bodies, and reuse existing evidence issues/infoNeeded. AV independence remains field-specific; a source failure cannot create official evidence or upgrade qualifications.
- Shared statement parsing now binds adjacent external formal titles/units, actual split date/year headers, explicit reporting currency and accounting parentheses across adjacent cells in the same column group. It rejects rounded summaries/non-GAAP reconciliations where formal statements exist. Native RMB takes precedence over convenience USD translations; a supported standalone USD statement works without a LI identity condition.
- Full SEC submissions must bind CIK, accession, form, primary filename/sequence, acceptance clock and original hash. EX-99 attachments must match the actual primary-document declaration and carry their filename/type/sequence in each fact locator. Unsupported PDF/graphics are not parsed. No PDF/OCR, ticker-specific SEC branch or new TTM engine is added.
- **Exact real fixture**: Mac mini's existing 520,544-byte Q2 full 6-K, raw SHA256 `9b3170dbdde2e9a37417f4132d619f390ec799569b68e1908fa3824d00528865`, transferred without network refresh and frozen losslessly as gzip. Original bytes, envelope and numerical cells are unchanged. Same-byte baseline comparison: PR49 = 0 observations, follow-up = 12 observations in three actual periods; Q2 has revenue CNY 25,666,890,000, consolidated net income -1,705,257,000, OCF 15,025,000, positive CapEx spend 1,315,790,000. Original reconciliation remains OCF minus spend = FCF -1,300,765,000.
- **Clock limitation retained**: the archival download did not save network `fetched_at`. The saved `2026-10-28T06:04:22.283703+00:00` is request start only. Historical fetch time stays UNKNOWN; file mtime/request start is not substituted. Offline qualification-contract tests label their frozen processing clock explicitly, and unknown-fetch input remains rejected. These tests prove source parsing and qualification logic, not a recovered historical fetch clock or successful live API route.
- Remaining runtime prerequisites: configure truthful operator identity in the actual runtime; after separate authorization perform one normal SEC reachability/read check and record actual completion time. A compliant identity does not guarantee that 403/network restrictions disappear. No live SEC, model, AV, broker or production analysis request was made for this development follow-up.
