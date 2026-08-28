# Real IBKR Pattern Runtime Promotion Matrix

> Dataset: `wp-real-ibkr-pattern-dataset-v2`
>
> Market / timeframe: `US / 1d`
>
> Overall status: `PATTERN_RUNTIME_PROMOTION_PARTIAL`

| Pattern | EQUITY | FIXED_INCOME |
| --- | --- | --- |
| Breakout | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-level-break-runtime-candidate-v2`<br>hash `ab60f9e3b8658518a4fecc7e62feb2d9d58db695bdf381b61c44ed935d99480b`<br>Holdout `PASS` · Untouched `PASS` | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-level-break-runtime-candidate-v2`<br>hash `2e856e31464f473164c82b3c1be2cc7b61cb1eb17ffc58a942d8cea9b4a2833e`<br>Holdout `PASS` · Untouched `PASS` |
| Breakdown | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-level-break-runtime-candidate-v2`<br>hash `4216ade03fe0f85af86b77b5e031cf81c5e84853d27d9dd42ed31ce9b9c87be0`<br>Holdout `PASS` · Untouched `PASS` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-level-break-runtime-candidate-v2`<br>hash `fded71751965d05d82fd1824296b1b4a259e5c56589e5022013367350d33ea6f`<br>Holdout `INSUFFICIENT` · Untouched `NOT_OPENED` |
| Rectangle | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-rectangle-runtime-candidate-v2`<br>hash `5991ac29246a1d6361c54a937b0841014368cb185c015884e73f5b912aa1f544`<br>Holdout `PASS` · Untouched `PASS` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-rectangle-runtime-candidate-v2`<br>hash `870b704aeecacd93054108dd42b68317866e1fa73914dc1dd62b9b12b542aabc`<br>Holdout `INSUFFICIENT` · Untouched `NOT_OPENED` |
| Ascending Triangle | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-ascending-triangle-runtime-candidate-v2`<br>hash `eb195624b7bd3785fc98040b45bc1dc09792d52c0755d211504a28964fc5ee72`<br>Holdout `PASS` · Untouched `PASS` | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-ascending-triangle-runtime-candidate-v2`<br>hash `75c8f3b2c951d9e9d9ed00ec5a26bdc786874bb557c685778d25b7824ada293a`<br>Holdout `PASS` · Untouched `PASS` |
| Double Top | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-double-reversal-runtime-candidate-v2`<br>hash `0c2efd7cc2ece9effce945ba0c3fbf1db37455594f78269c44a5ee92b1a96eea`<br>Holdout `PASS` · Untouched `PASS` | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-double-reversal-runtime-candidate-v2`<br>hash `a4f9e86ace4e503f0266ce7d23c79959ea3b15b6f615167e0a67deb48d97c629`<br>Holdout `PASS` · Untouched `PASS` |
| Double Bottom | `READY_FOR_RUNTIME_PROMOTION`<br>`wp-us-double-reversal-runtime-candidate-v2`<br>hash `e6846f7c75645c03bc20444154fc0e7ab6d731ac07760cf649e644b4402d21ec`<br>Holdout `PASS` · Untouched `PASS` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-double-reversal-runtime-candidate-v2`<br>hash `1847abc4fc732f617f7485580317b9384e42e3a407120fbcaa9c747799952f9a`<br>Holdout `INSUFFICIENT` · Untouched `NOT_OPENED` |

## Verdict summary

- `READY_FOR_RUNTIME_PROMOTION`: 9
- `INSUFFICIENT_REAL_CASE_EVIDENCE`: 3
- `NEEDS_RECALIBRATION`: 0
- `DATA_QUALITY_BLOCKED`: 0

All nine ready scopes used immutable Dataset v2 artifacts for Development,
Holdout, and Untouched. The three insufficient scopes were not reopened. Only
the nine exact ready scopes are present in the runtime registry; no fallback is
permitted.
