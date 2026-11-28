# PR #92 历史差量收口

结论 `INTEGRATE_MINIMAL_DELTA`。审查固定原候选 `3239ff87fd24c31a5279851329aacc1416511f9f`，对照主线 `2614965e614e9b26f7d7ba4974cdb4d2f7a19af4` 的 L1 v2、财务 V2 和 S3。原 Draft 无整合价值，结案后以本候选为唯一集成入口。

月度交付保留 SEC 真实发行人、已声明附件、实际发布和抓取时钟、原件哈希及月度期间。在原有采集范围内遇到符合版式的正式公告时提取，不新增请求或专项端点。仅 LI/XPEV 已验证版式；按同月实际数值核对，冲突月不形成合格事实。季度、累计和车型数字不能替代独立月度总交付。L1 v2 选择最近两个仍在 60 日内的合格月份，使用原证据引用和终审。冻结验证证明可消费性，不承诺每月有 SEC 申报、网络可达或生产已取得。

美团 HKEX PDF 和发行人目录能作为换代开发线索，但旧解析锁死 2026 Q2、2025 Q2 对比期和 PDF 页码。它不是可持续的下一季自动来源，集团与分部数此次无合格生产接入。交 4.1 另行评估按正式目录发布日期和披露期持续发现、期间/币种/列标题/分部勾稽及失败降级。管理层旧竞争段落是对少数公告的硬编码复述，不能作为独立反证。L1 v2 已承接研究命题、对照和报告阅读投影，旧终审与政策投影不用再移植。

| 原净差量文件 | 分类 | 处理及理由 |
| --- | --- | --- |
| `backend/graph/tools.py` | C | 舍弃单公司、单季度 HKEX 接线 |
| `backend/services/investment_policy/consumers.py` | A | L1 v2 已有现行政策终审与阅读投影 |
| `backend/services/market_data/evidence.py` | B/C | 保留月度字段、期间及资格；舍弃固定 HKEX 目录规则与专属分部字段 |
| `backend/services/market_data/financial_research.py` | C | 舍弃旧分部利润率配套；本候选仅增加月度显示名称 |
| `backend/services/market_data/output_policy.py` | A | 使用现行 L1 v2 输出与 Evidence 重核 |
| `backend/services/position_analysis.py` | A/B | 舍弃已有 v2 承接的报告结构；保留月度显示口径 |
| `docs/CAPABILITY_REGISTRY.md` | B/C | 仅更新保留能力和当前 Owner |
| `docs/V42_IMPLEMENTATION.md` | A | 旧 PR-C 阶段报告留在历史 PR；交付以本清单为准 |
| `research_v2/official_disclosures.py` | B/C | 保留月度提取；舍弃单季度 HKEX 解析和管理层固定摘要 |
| `research_v2/sec_disclosures.py` | B/C | 保留已声明 6-K 月度提取接线；舍弃旧管理层竞争短语规则 |
| `tests/fixtures/monthly_deliveries/0001104659-26-120858.txt.gz` | B | 保留 LI 完整冻结原件 |
| `tests/fixtures/monthly_deliveries/0001193125-26-411251.txt.gz` | B | 保留 XPEV 完整冻结原件 |
| `tests/fixtures/monthly_deliveries/manifest.json` | B | 保留原件身份、抓取时钟和哈希 |
| `tests/fixtures/official_hkex/meituan_2026_q2.pdf` | C | 仅留历史 PR，当前候选不迁入 |
| `tests/fixtures/official_hkex/meituan_index.html.gz` | C | 同上 |
| `tests/test_p0_quality_semantics.py` | A | 舍弃随旧报告变化的断言 |
| `tests/test_pr_c_quality_closure.py` | A/C | 舍弃旧报告与固定竞争文本回归 |
| `tests/test_sec_real_original.py` | B/C | 月度正反例移入 `test_sec_monthly_deliveries.py`；舍弃旧叙事测试 |
| `tests/test_v42_pr_c_hkex_evidence.py` | C | 随固定 HKEX 解析舍弃 |

## 4.1 共享数据归属

#154 的美股当期经营桥与 NVDA 分派资格仍归 4.1。月度交付使用既有通用 Owner 的非货币观察，仅在 LI/XPEV 已声明 6-K 附件消费，无新财务口径、采集端点或独立数据 Owner。同文件集成由项目管理 4 结合 #154 精确候选处理。
