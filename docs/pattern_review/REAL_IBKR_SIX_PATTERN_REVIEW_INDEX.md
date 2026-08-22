# Real IBKR Six-Pattern Human Chart Review Index

> Gate: `READY_FOR_HUMAN_CHART_REVIEW`

Codex generated detector evidence and blank review fields. A human reviewer must inspect every selected chart and fill only the manifest fields `human_review_label`, `human_review_notes`, `reviewer`, and `reviewed_at`.

Allowed labels: `PASS`, `FALSE_POSITIVE`, `FALSE_NEGATIVE`, `AMBIGUOUS`, `REVIEW_DISAGREEMENT`.

## Scope Inventory

| Pattern | Asset class | Detected | Selected detected | Negative controls | Status |
| --- | --- | ---: | ---: | ---: | --- |
| breakout | EQUITY | 304 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakout | FIXED_INCOME | 84 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakdown | EQUITY | 171 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakdown | FIXED_INCOME | 194 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| rectangle | EQUITY | 19 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| rectangle | FIXED_INCOME | 13 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| ascending_triangle | EQUITY | 112 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| ascending_triangle | FIXED_INCOME | 79 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_top | EQUITY | 267 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_top | FIXED_INCOME | 63 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_bottom | EQUITY | 223 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_bottom | FIXED_INCOME | 53 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |

## Review Cases

| Case | Pattern | Asset | Kind | Symbol | Detector status | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `review_024b06864a69af139dc0` | double_top | FIXED_INCOME | DETECTED_CANDIDATE | IEF | invalidated | [SVG](../../reports/pattern-review/review_024b06864a69af139dc0.svg) |
| `review_0648e864d538822b39a2` | double_top | EQUITY | DETECTED_CANDIDATE | IWM | invalidated | [SVG](../../reports/pattern-review/review_0648e864d538822b39a2.svg) |
| `review_0f022c854c57d36bc6d8` | ascending_triangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_0f022c854c57d36bc6d8.svg) |
| `review_0ff8c4f45e39104d21d1` | double_bottom | EQUITY | DETECTED_CANDIDATE | IWM | invalidated | [SVG](../../reports/pattern-review/review_0ff8c4f45e39104d21d1.svg) |
| `review_12c1a35099b90abbbd4b` | breakdown | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | MSFT | NO_PATTERN | [SVG](../../reports/pattern-review/review_12c1a35099b90abbbd4b.svg) |
| `review_15494b5c92d52123561d` | breakdown | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_15494b5c92d52123561d.svg) |
| `review_159cf9885d60b82a77d9` | breakdown | EQUITY | DETECTED_CANDIDATE | JNJ | invalidated | [SVG](../../reports/pattern-review/review_159cf9885d60b82a77d9.svg) |
| `review_1717b96bcc7c0a867e77` | breakout | FIXED_INCOME | DETECTED_CANDIDATE | SHY | expired | [SVG](../../reports/pattern-review/review_1717b96bcc7c0a867e77.svg) |
| `review_191ca5e496e695a48d86` | double_top | FIXED_INCOME | DETECTED_CANDIDATE | TLT | invalidated | [SVG](../../reports/pattern-review/review_191ca5e496e695a48d86.svg) |
| `review_1af80f3ae1eb45cffd88` | rectangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_1af80f3ae1eb45cffd88.svg) |
| `review_1b7cff46b823749f9f27` | double_bottom | EQUITY | DETECTED_CANDIDATE | AAPL | invalidated | [SVG](../../reports/pattern-review/review_1b7cff46b823749f9f27.svg) |
| `review_1f9adcfc68454ce58932` | ascending_triangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_1f9adcfc68454ce58932.svg) |
| `review_20b0d8a772317c006392` | double_bottom | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_20b0d8a772317c006392.svg) |
| `review_23f1422afb80010a9aa3` | rectangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | AAPL | NO_PATTERN | [SVG](../../reports/pattern-review/review_23f1422afb80010a9aa3.svg) |
| `review_24ee797530a37a1035be` | breakout | EQUITY | DETECTED_CANDIDATE | JNJ | expired | [SVG](../../reports/pattern-review/review_24ee797530a37a1035be.svg) |
| `review_25fa041e89e48427ee23` | rectangle | FIXED_INCOME | DETECTED_CANDIDATE | IEF | invalidated | [SVG](../../reports/pattern-review/review_25fa041e89e48427ee23.svg) |
| `review_260d7fa088d7d16e026c` | breakout | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_260d7fa088d7d16e026c.svg) |
| `review_27bf9f65256acea0038a` | rectangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_27bf9f65256acea0038a.svg) |
| `review_27ef742e155ecfa870cb` | ascending_triangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_27ef742e155ecfa870cb.svg) |
| `review_2d4669f4a6e599086c7e` | double_top | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_2d4669f4a6e599086c7e.svg) |
| `review_2e436aff320ee2a48496` | ascending_triangle | EQUITY | DETECTED_CANDIDATE | AAPL | invalidated | [SVG](../../reports/pattern-review/review_2e436aff320ee2a48496.svg) |
| `review_2e6bfd15d67d4f8b31ed` | rectangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_2e6bfd15d67d4f8b31ed.svg) |
| `review_30557d7e0fa7c76ab97b` | rectangle | FIXED_INCOME | DETECTED_CANDIDATE | SHY | expired | [SVG](../../reports/pattern-review/review_30557d7e0fa7c76ab97b.svg) |
| `review_37281afa17f3be770b56` | ascending_triangle | FIXED_INCOME | DETECTED_CANDIDATE | IEF | invalidated | [SVG](../../reports/pattern-review/review_37281afa17f3be770b56.svg) |
| `review_3744a0c94c5e032987e3` | breakout | FIXED_INCOME | DETECTED_CANDIDATE | TLT | invalidated | [SVG](../../reports/pattern-review/review_3744a0c94c5e032987e3.svg) |
| `review_38365f55a6350ff47c95` | ascending_triangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_38365f55a6350ff47c95.svg) |
| `review_3926a42cc144e0aa6df2` | breakout | FIXED_INCOME | DETECTED_CANDIDATE | LQD | expired | [SVG](../../reports/pattern-review/review_3926a42cc144e0aa6df2.svg) |
| `review_3c570d826c1c5c669d8d` | double_top | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_3c570d826c1c5c669d8d.svg) |
| `review_3cc0adb16f385c8a48c7` | breakout | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_3cc0adb16f385c8a48c7.svg) |
| `review_3ed69893508aa67ec4d8` | ascending_triangle | EQUITY | DETECTED_CANDIDATE | IWM | invalidated | [SVG](../../reports/pattern-review/review_3ed69893508aa67ec4d8.svg) |
| `review_40154bdb4f645561df76` | breakout | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_40154bdb4f645561df76.svg) |
| `review_40e28a58066393d44ec0` | breakdown | FIXED_INCOME | DETECTED_CANDIDATE | IEF | invalidated | [SVG](../../reports/pattern-review/review_40e28a58066393d44ec0.svg) |
| `review_40e6edf6a6d2dec98ff6` | breakout | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_40e6edf6a6d2dec98ff6.svg) |
| `review_41187d5547c7f869e494` | double_bottom | EQUITY | DETECTED_CANDIDATE | JPM | invalidated | [SVG](../../reports/pattern-review/review_41187d5547c7f869e494.svg) |
| `review_46d61df7140cbf5a1b59` | rectangle | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_46d61df7140cbf5a1b59.svg) |
| `review_49921a8c1432f42cbb36` | breakout | FIXED_INCOME | DETECTED_CANDIDATE | IEF | expired | [SVG](../../reports/pattern-review/review_49921a8c1432f42cbb36.svg) |
| `review_4a5505316a9d9e02e10e` | breakdown | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_4a5505316a9d9e02e10e.svg) |
| `review_4a5f16512cca933cb330` | breakout | EQUITY | DETECTED_CANDIDATE | AAPL | expired | [SVG](../../reports/pattern-review/review_4a5f16512cca933cb330.svg) |
| `review_4afbcf6655d0a8a07ab7` | breakout | EQUITY | DETECTED_CANDIDATE | IWM | invalidated | [SVG](../../reports/pattern-review/review_4afbcf6655d0a8a07ab7.svg) |
| `review_4e06743cfce424c67ab5` | double_bottom | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_4e06743cfce424c67ab5.svg) |
| `review_4e348f879d487fac009a` | rectangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_4e348f879d487fac009a.svg) |
| `review_4fb8b402476b0fa90257` | breakout | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_4fb8b402476b0fa90257.svg) |
| `review_52f5b4bde6776a4ade12` | double_top | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_52f5b4bde6776a4ade12.svg) |
| `review_5810bee3e0b4ce54020e` | breakdown | EQUITY | DETECTED_CANDIDATE | AAPL | invalidated | [SVG](../../reports/pattern-review/review_5810bee3e0b4ce54020e.svg) |
| `review_5841889047deba67e095` | breakout | FIXED_INCOME | DETECTED_CANDIDATE | AGG | expired | [SVG](../../reports/pattern-review/review_5841889047deba67e095.svg) |
| `review_5845ee74b7a488dc1b4f` | ascending_triangle | EQUITY | DETECTED_CANDIDATE | JNJ | invalidated | [SVG](../../reports/pattern-review/review_5845ee74b7a488dc1b4f.svg) |
| `review_668623f8e579255cd807` | breakout | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_668623f8e579255cd807.svg) |
| `review_66b5ab72f68c73fb1092` | double_bottom | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_66b5ab72f68c73fb1092.svg) |
| `review_68673e3a84350ba84e75` | breakdown | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_68673e3a84350ba84e75.svg) |
| `review_6a247bc8ffffc609e063` | double_bottom | FIXED_INCOME | DETECTED_CANDIDATE | LQD | invalidated | [SVG](../../reports/pattern-review/review_6a247bc8ffffc609e063.svg) |
| `review_70fcd2371253f7227058` | double_top | FIXED_INCOME | DETECTED_CANDIDATE | LQD | invalidated | [SVG](../../reports/pattern-review/review_70fcd2371253f7227058.svg) |
| `review_71bf31d595583cc3d37f` | double_top | FIXED_INCOME | DETECTED_CANDIDATE | SHY | expired | [SVG](../../reports/pattern-review/review_71bf31d595583cc3d37f.svg) |
| `review_74a04142f6edce879f71` | double_top | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_74a04142f6edce879f71.svg) |
| `review_7972a8c09946af4b737a` | rectangle | EQUITY | DETECTED_CANDIDATE | JNJ | invalidated | [SVG](../../reports/pattern-review/review_7972a8c09946af4b737a.svg) |
| `review_7a17c7d857bd66769587` | breakout | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | MSFT | NO_PATTERN | [SVG](../../reports/pattern-review/review_7a17c7d857bd66769587.svg) |
| `review_7ec09853f5fe24cfb21d` | breakdown | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_7ec09853f5fe24cfb21d.svg) |
| `review_81697ac008f55cc563a0` | rectangle | FIXED_INCOME | DETECTED_CANDIDATE | IEF | invalidated | [SVG](../../reports/pattern-review/review_81697ac008f55cc563a0.svg) |
| `review_832f243a2a07fc50ca33` | breakout | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_832f243a2a07fc50ca33.svg) |
| `review_857d1cf551ddf382248a` | ascending_triangle | FIXED_INCOME | DETECTED_CANDIDATE | SHY | invalidated | [SVG](../../reports/pattern-review/review_857d1cf551ddf382248a.svg) |
| `review_8a863d0a91d0a3773b54` | ascending_triangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_8a863d0a91d0a3773b54.svg) |
| `review_8bec98a3f5c0fb669332` | ascending_triangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | IEF | NO_PATTERN | [SVG](../../reports/pattern-review/review_8bec98a3f5c0fb669332.svg) |
| `review_8c029d0b77c040804b9b` | breakout | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_8c029d0b77c040804b9b.svg) |
| `review_8c951fe88002669c0953` | breakdown | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_8c951fe88002669c0953.svg) |
| `review_948a8e66cebd6596fa2c` | rectangle | FIXED_INCOME | DETECTED_CANDIDATE | LQD | invalidated | [SVG](../../reports/pattern-review/review_948a8e66cebd6596fa2c.svg) |
| `review_9638418b11c5d0a54ce9` | breakdown | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_9638418b11c5d0a54ce9.svg) |
| `review_97ddb9ee804b88d29b16` | double_bottom | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_97ddb9ee804b88d29b16.svg) |
| `review_987429745ee4b536cbc2` | breakdown | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_987429745ee4b536cbc2.svg) |
| `review_9a4ff8d892bd6128944d` | rectangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_9a4ff8d892bd6128944d.svg) |
| `review_a108fdbb9790ed703249` | double_top | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_a108fdbb9790ed703249.svg) |
| `review_a1ba38e0ada295052140` | breakdown | EQUITY | DETECTED_CANDIDATE | IWM | invalidated | [SVG](../../reports/pattern-review/review_a1ba38e0ada295052140.svg) |
| `review_a3334640128751f6ffc0` | breakdown | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_a3334640128751f6ffc0.svg) |
| `review_a6c8932b42cc8c6b712f` | ascending_triangle | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_a6c8932b42cc8c6b712f.svg) |
| `review_ab09c2950b77ddd16c93` | double_bottom | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_ab09c2950b77ddd16c93.svg) |
| `review_ae64e18123942089403b` | rectangle | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_ae64e18123942089403b.svg) |
| `review_aee24f4982643240b8c9` | breakdown | EQUITY | DETECTED_CANDIDATE | JPM | expired | [SVG](../../reports/pattern-review/review_aee24f4982643240b8c9.svg) |
| `review_af5a7b0f38aa724b7d17` | breakdown | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | AAPL | NO_PATTERN | [SVG](../../reports/pattern-review/review_af5a7b0f38aa724b7d17.svg) |
| `review_afcaad9b99b8ed67d9c9` | rectangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_afcaad9b99b8ed67d9c9.svg) |
| `review_b03effbe92f45a4dab05` | double_top | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JNJ | NO_PATTERN | [SVG](../../reports/pattern-review/review_b03effbe92f45a4dab05.svg) |
| `review_b33a9cf128dff19e1f52` | breakdown | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_b33a9cf128dff19e1f52.svg) |
| `review_b4b7470da40393434900` | double_bottom | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_b4b7470da40393434900.svg) |
| `review_b7bae06eb6c19cc171d6` | rectangle | EQUITY | DETECTED_CANDIDATE | JPM | invalidated | [SVG](../../reports/pattern-review/review_b7bae06eb6c19cc171d6.svg) |
| `review_bc5409decb1d483b842a` | ascending_triangle | FIXED_INCOME | DETECTED_CANDIDATE | TLT | invalidated | [SVG](../../reports/pattern-review/review_bc5409decb1d483b842a.svg) |
| `review_bdbc9e23eb853b36aa2a` | ascending_triangle | FIXED_INCOME | DETECTED_CANDIDATE | LQD | invalidated | [SVG](../../reports/pattern-review/review_bdbc9e23eb853b36aa2a.svg) |
| `review_bdcadbb54ec189f9b70d` | double_bottom | FIXED_INCOME | DETECTED_CANDIDATE | TLT | invalidated | [SVG](../../reports/pattern-review/review_bdcadbb54ec189f9b70d.svg) |
| `review_bed9be26a05f869a9680` | breakdown | FIXED_INCOME | DETECTED_CANDIDATE | SHY | expired | [SVG](../../reports/pattern-review/review_bed9be26a05f869a9680.svg) |
| `review_bf9ca36217e35986ab4f` | double_bottom | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | JPM | NO_PATTERN | [SVG](../../reports/pattern-review/review_bf9ca36217e35986ab4f.svg) |
| `review_c0113f7f31c6ee49d2d6` | double_top | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_c0113f7f31c6ee49d2d6.svg) |
| `review_c03d5970ecdc1d309f31` | double_top | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_c03d5970ecdc1d309f31.svg) |
| `review_c1cb9864d69f3e991958` | double_bottom | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_c1cb9864d69f3e991958.svg) |
| `review_c26fe0684249d0d07562` | breakdown | FIXED_INCOME | DETECTED_CANDIDATE | TLT | invalidated | [SVG](../../reports/pattern-review/review_c26fe0684249d0d07562.svg) |
| `review_c27f366dc71477729058` | double_bottom | EQUITY | DETECTED_CANDIDATE | JNJ | expired | [SVG](../../reports/pattern-review/review_c27f366dc71477729058.svg) |
| `review_c528160570a762e69b5c` | double_top | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | AAPL | NO_PATTERN | [SVG](../../reports/pattern-review/review_c528160570a762e69b5c.svg) |
| `review_c5c4d7b03f9e5e26c0fc` | double_bottom | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | QQQ | NO_PATTERN | [SVG](../../reports/pattern-review/review_c5c4d7b03f9e5e26c0fc.svg) |
| `review_c5e08c5e1ebe91c10a55` | double_top | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_c5e08c5e1ebe91c10a55.svg) |
| `review_c618c032431b3c43f66b` | breakout | EQUITY | DETECTED_CANDIDATE | JPM | expired | [SVG](../../reports/pattern-review/review_c618c032431b3c43f66b.svg) |
| `review_caf3821d15f9b7d4e854` | double_bottom | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_caf3821d15f9b7d4e854.svg) |
| `review_cbc0d233f4a2309df66c` | double_top | EQUITY | DETECTED_CANDIDATE | JNJ | invalidated | [SVG](../../reports/pattern-review/review_cbc0d233f4a2309df66c.svg) |
| `review_cc866ceb31bc9634f3ac` | breakdown | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | TLT | NO_PATTERN | [SVG](../../reports/pattern-review/review_cc866ceb31bc9634f3ac.svg) |
| `review_cf86fd63680ee41112a1` | ascending_triangle | EQUITY | DETECTED_CANDIDATE | JPM | invalidated | [SVG](../../reports/pattern-review/review_cf86fd63680ee41112a1.svg) |
| `review_d275bc5f25c6b94a03b6` | breakdown | FIXED_INCOME | DETECTED_CANDIDATE | LQD | expired | [SVG](../../reports/pattern-review/review_d275bc5f25c6b94a03b6.svg) |
| `review_d41d99c8841ef63b19d8` | double_top | EQUITY | DETECTED_CANDIDATE | JPM | invalidated | [SVG](../../reports/pattern-review/review_d41d99c8841ef63b19d8.svg) |
| `review_d4377f90230c017036d3` | ascending_triangle | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_d4377f90230c017036d3.svg) |
| `review_d75e491fcb6e77a9d74f` | rectangle | EQUITY | DETECTED_CANDIDATE | QQQ | invalidated | [SVG](../../reports/pattern-review/review_d75e491fcb6e77a9d74f.svg) |
| `review_d8cab0cd0dbbb35263a6` | rectangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_d8cab0cd0dbbb35263a6.svg) |
| `review_dc5beb3e760fb1d74eef` | double_bottom | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | LQD | NO_PATTERN | [SVG](../../reports/pattern-review/review_dc5beb3e760fb1d74eef.svg) |
| `review_dcbdada719e73983d6a5` | double_top | EQUITY | DETECTED_CANDIDATE | AAPL | invalidated | [SVG](../../reports/pattern-review/review_dcbdada719e73983d6a5.svg) |
| `review_dd5941e7aa641d45b916` | ascending_triangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | SHY | NO_PATTERN | [SVG](../../reports/pattern-review/review_dd5941e7aa641d45b916.svg) |
| `review_dd76a51da290d2320e40` | rectangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | MSFT | NO_PATTERN | [SVG](../../reports/pattern-review/review_dd76a51da290d2320e40.svg) |
| `review_df6705444cc6326fa5ed` | breakout | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | NVDA | NO_PATTERN | [SVG](../../reports/pattern-review/review_df6705444cc6326fa5ed.svg) |
| `review_e3d8848a96102ede88ae` | double_bottom | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | NVDA | NO_PATTERN | [SVG](../../reports/pattern-review/review_e3d8848a96102ede88ae.svg) |
| `review_e47399d503b5539b2b50` | ascending_triangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_e47399d503b5539b2b50.svg) |
| `review_e5bb95439df1010ecdf6` | rectangle | EQUITY | DETECTED_CANDIDATE | SPY | invalidated | [SVG](../../reports/pattern-review/review_e5bb95439df1010ecdf6.svg) |
| `review_e68dd1e64e06d7f2c9bc` | double_bottom | EQUITY | DETECTED_CANDIDATE | MSFT | invalidated | [SVG](../../reports/pattern-review/review_e68dd1e64e06d7f2c9bc.svg) |
| `review_eb57701cd88fab273e47` | breakout | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | IWM | NO_PATTERN | [SVG](../../reports/pattern-review/review_eb57701cd88fab273e47.svg) |
| `review_f036b9e0809a0529404d` | ascending_triangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | MSFT | NO_PATTERN | [SVG](../../reports/pattern-review/review_f036b9e0809a0529404d.svg) |
| `review_f13733f290cdffb34507` | double_top | FIXED_INCOME | DETECTED_CANDIDATE | AGG | invalidated | [SVG](../../reports/pattern-review/review_f13733f290cdffb34507.svg) |
| `review_f5ddd03b74f0ef0157d5` | rectangle | FIXED_INCOME | NEGATIVE_CONTROL_NO_DETECTION | AGG | NO_PATTERN | [SVG](../../reports/pattern-review/review_f5ddd03b74f0ef0157d5.svg) |
| `review_fcc6d177358a1d25fa94` | ascending_triangle | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | AAPL | NO_PATTERN | [SVG](../../reports/pattern-review/review_fcc6d177358a1d25fa94.svg) |
| `review_ff5bebdc1fcc879a3d43` | double_top | EQUITY | NEGATIVE_CONTROL_NO_DETECTION | NVDA | NO_PATTERN | [SVG](../../reports/pattern-review/review_ff5bebdc1fcc879a3d43.svg) |
| `review_ffd353f97db1ca6e6e8f` | double_bottom | FIXED_INCOME | DETECTED_CANDIDATE | IEF | expired | [SVG](../../reports/pattern-review/review_ffd353f97db1ca6e6e8f.svg) |

## Gate Boundary

- Human labels remain `null`.
- Holdout detector run: `false`.
- Untouched Validation detector run: `false`.
- No Production Promotion verdict has been issued.
