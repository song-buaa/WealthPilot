# WealthPilot 4.2 P0.1 — Unified US financial sources

## P1 专业研究扩展（2026-10-29 开发中）

从main f0be1b1复用原SEC/AV、Evidence、PEER和Output Policy。独立核验的历史季度单列为historical-only引用，不冒充最新事实，不改变现有150/450日当前字段及L2–L4门槛。新增可比季度增长、利润率、现金转化及输入完整时的基础估值，TTM窗口/求和继续复用原AV纯计算函数；不同会计口径、币种、期间或冲突不拼接。披露中明确的ADS diluted EPS保持每ADS单位，不把集团利润或普通股EPS当作上市单位EPS。无法核验股份、换算、FX、净现金时分别UNKNOWN。

原Expressing扩展合同允许模型通过结构化数值绑定引用owner计算结果，最终由Output Policy填入带字段标签/期间/币种的数值，不开放任意数字。原ExitPlan增加明确research-only的目标/比例/条件/观察区间/情景及用户复核期限；用户声明不变为实时持仓或服务端正式目标。无新Agent、Schema、财务数据owner、估值平台或记忆库；PR56生命周期、E1、资产及交易不变。开发离线/公开数据证据与内容质量提升分开，8分目标须部署后原生报告评估。

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
6. Reads are HTTPS-only, strict SEC host/path allowlists, no redirects/query parameters/arbitrary card URLs, 8s per read, no fixed SEC byte ceiling, no retries, quarterly/event sources before annual fallback. P1 extends event selection to the recent 550-day comparable window, retaining the original four financial-record bound; non-financial events cannot crowd out earnings. Comparative CompanyFacts periods are explicit durations before the actual filing report date, never guessed current periods. Once quarterly facts exist, a large annual original is not prerequisite. An individual original read/parse failure skips that file and preserves independent successful facts. Existing LI IR limits remain unchanged. SEC directory/facts/record caching uses the existing cache owner and TTL; source fetched_at is preserved. Demo/mock never opens the network.
7. Only server-injected source lookup grants trust. Record/card claims of SEC URL, identity, hash, date or matching AV amounts do not. Evidence carries source/document identity, original hash, source locator, issuer, currency, accounting basis and separate source/fiscal/fetch clocks.

## Field fusion and degradation

AV remains the standardized input and existing TTM arithmetic owner. SEC supplements independently verified raw quarter/annual L1 facts when AV is absent. P1 reuses that same pure four-quarter window/summation over verified source components; no second TTM engine, quarterly subtraction or synthetic Q4 is added. Quarterly diluted EPS is summed only with four consistent currency/listed-unit components; differing ADS ratios block the estimate. A static price/EPS ratio explicitly discloses unverified subsequent share-count/split changes and is not exact current valuation or fair value. Missing FX, shares, debt/restricted-cash attribution and incomplete periods remain separate UNKNOWNs.

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

## 第一轮集中质量修正 P0

基线 b386bab10a72ee09253bfa5bf746cb89d130354b；复用现有 Owner，不新建财务、权限、TTM 或验收系统。仅开发/公开只读核查，未调用付费模型、AV或券商，不部署、不改资产或纪律。

| 项目 | 已确认根因与最小修复 | 未恢复/待验证 |
|---|---|---|
| PDD 最新季度 | 正式6-K的附件标签为“Exhibit 99.1—Press Release (Earnings Release)”，原规则仅接受纯99.1；表格内部换行及Revenues/generated from operating activities标签也未支持。按精确文件名+附件编号/类型接受描述性标签，归一化空白并增加通用字段标签 | 无此前运行原始响应，不能认定当时实际网络缓存/请求路径；正式原文离线可复现并验证修复 |
| PDD 字段资格 | 2026 Q2营收112,358,000,000 CNY、合并净利润27,182,000,000 CNY及经营现金流25,670,000,000 CNY恢复原L1字段合同；SEC接受时点来自2026-08-25原始header，不使用2026-08-24 IR日或财期代替。现金流表CNY标头有25,670与3,783（million）歧义，但同一原文明确季度及RMB25.7 billion；仅当原文币种、季度和四舍五入精度唯一支持一个既有精确表值时保留该表值，并记录关联出处 | 不用AV选择冲突列，不将正文四舍五入值作为精确字段；无独立同文支持、期间/币种不一致或多个表值均符合精度时继续UNKNOWN。CapEx未明确披露，零值/FCF不获资格；不补齐TTM的其他季度 |
| HK 日周线 | 同一完整性布尔值错误地把当前未闭合周的缺口传播给历史周线；窗口首部半周被当完整周；周线关闭只看下周一，未使用实际最后交易日收盘。改为按原calendar的各周期范围校验，分别列出缺失日期；根数不足与覆盖缺口分别标识 | 未取得美团300条原日期，不能确认这些机制是该次失败的全部原因，也不能猜测缺失日或停牌 |
| HK 正式财务 | AV原生不覆盖HK，AKShare fundamental实际为公司概况；正式披露Owner及字段资格目前覆盖US的季度/年度 | 美团IR有正式PDF报告，但缺官方PDF字段验证与半年累计period合同、经营利润/利润率口径；本轮明确HK_OFFICIAL_FINANCIAL_ADAPTER_UNAVAILABLE，仍允许L1。没有新增HKEX平台或OCR |
| 默认纪律/信号 | 原40%为缺配置时的默认值；现有来源记录不提供正式个人纪律的可信确认版本。原fundamental_signal只数研究文本关键词，未检查财务。只读Skill呈现未核验状态，保留原值为诊断及原风险数值；报告明确历史单证券比例与当前公司集中度不同 | 不把聊天6%/8%写为正式规则，不创建目标、确认或新规则系统。专业经营解释继续只走原Evidence/PEER |

财务报告按字段/期间/币种/口径整理相同事实的显示，所有原来源与record_id保留在evidence。历史年度字段明确为经营基线，不能冒充最新季度或为全部TTM补公告日；研究卡的90日时钟与独立年度字段的原资格分开说明，不改TTL。

公开来源：[PDD正式6-K](https://www.sec.gov/Archives/edgar/data/1737806/000110465926100534/0001104659-26-100534.txt)、[PDD官方IR](https://pinduoduo.gcs-web.com/news-releases/news-release-details/pdd-holdings-announces-second-quarter-2026-unaudited-financial)、[HKEX 2026交易日及半日市](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf)、[美团正式报告目录](https://www.meituan.com/en-US/investor/reports)。固定交易日历exchange-calendars 4.13.2与上述2026假期/半日市核对一致，未引入自造日历或忽略真实应交易日期。停牌证据不存在时按真实缺口保留UNKNOWN。当前KlineRegistry的broker实现实际包装Tiger SDK，AV仅支持US；IBKR/Futu不是该Registry的HK历史K线fallback，不能仅凭broker来源标签推断券商。本轮不调用券商或声称可以补齐本次缺口。

PDD夹具为公开SEC text/plain页面的完整浏览器DOM UTF-8表示，包含真实SEC header与两份原文文件，非合成envelope；manifest保留抓取表现形式、长度与哈希。原始HTTP字节哈希及历史网络完成时间UNKNOWN，测试明确使用冻结离线处理时钟，不补造生产时钟。

如下一轮需核实美团真实缺口，最小只读提取为：从已保存的run ddfd5d1c7c2e4e80bae4d147ab914d11结果/私有记录导出technical.source、provenance、series.day的date/OHLCV、day/week的范围/parameters/indicators/资格，不含账户/持仓/成本。若记录不存在即注明，不启动新研究、券商连接、同步或Provider请求。PDD对应run ab08ede8cd09479eb6ddf9d31e672c04也不重新执行。

旧冻结LI/PDD周线含窗口首部半周，按当前规则重核时旧周线指标保持拒绝，日线仍合格；使用同一原始日线经原生产入口重算后，日周线均合格。回归不改原始夹具、不沿用错误周线指标；已有缓存若仍含旧聚合，应等待原缓存到期或正常进程更新，不修改生产缓存或事实。
