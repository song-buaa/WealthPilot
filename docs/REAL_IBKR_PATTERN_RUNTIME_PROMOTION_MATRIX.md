# Real IBKR Pattern Runtime Promotion Matrix

> Stage: `2E-1A/1B`
>
> Market / timeframe: `US / 1d`
>
> Overall status: `PATTERN_RUNTIME_PROMOTION_BLOCKED`

| Pattern | EQUITY | FIXED_INCOME |
| --- | --- | --- |
| Breakout | `DATA_QUALITY_BLOCKED`<br>`wp-us-level-break-runtime-candidate-v1`<br>hash `0aeea84fccc730f4d3ebb5067b317e767aa195a0d2d39bf752aa8c29faa42d8e`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `DATA_QUALITY_BLOCKED`<br>`wp-us-level-break-runtime-candidate-v1`<br>hash `9779614203c8bb4c64dea3ea22f7aebd92fe4c025e888dd9f0498c5d04ddbe74`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` |
| Breakdown | `DATA_QUALITY_BLOCKED`<br>`wp-us-level-break-runtime-candidate-v1`<br>hash `42e4f448b4ec7b695f4deb63261cf51e503ceae50ba055a59f71fde83b81e0a8`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-level-break-runtime-candidate-v1`<br>hash `edcd922c9cbab4f2dfe2a88ec4d3186932806e45f438822fc25c51466357bcc5`<br>Holdout `INSUFFICIENT` (5 detected + 4 controls)<br>Untouched `NOT_OPENED` |
| Rectangle | `DATA_QUALITY_BLOCKED`<br>`wp-us-rectangle-runtime-candidate-v1`<br>hash `e4a7c775559834e702334249477ab5296f120c0be739dbf0071601a841235752`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-rectangle-runtime-candidate-v1`<br>hash `1720a0096f2b6e9808ec2c4c9f3418393d5f6867798a9f33cb31a0588c5ca87e`<br>Holdout `INSUFFICIENT` (3 detected + 5 controls)<br>Untouched `NOT_OPENED` |
| Ascending Triangle | `DATA_QUALITY_BLOCKED`<br>`wp-us-ascending-triangle-runtime-candidate-v1`<br>hash `1d733373f04205a5ef2aa27c8fea9120d4c3a36006b19107b530b0a06a7c4ba8`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `DATA_QUALITY_BLOCKED`<br>`wp-us-ascending-triangle-runtime-candidate-v1`<br>hash `223477aef2a692f1f16c2e2c8cd50fc7b2bdf4b4f0ad755bce7cfab1c244e0cc`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` |
| Double Top | `DATA_QUALITY_BLOCKED`<br>`wp-us-double-reversal-runtime-candidate-v1`<br>hash `65c8e15c2fbe8f3a6c7f32c63555444c4fcedc6698f4905e9b82a52f821413d8`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `DATA_QUALITY_BLOCKED`<br>`wp-us-double-reversal-runtime-candidate-v1`<br>hash `bb955e984433cfc4465697c819e7fbae48af577bdf8c9fe581902f6d59fce121`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` |
| Double Bottom | `DATA_QUALITY_BLOCKED`<br>`wp-us-double-reversal-runtime-candidate-v1`<br>hash `7564ba6a91676504709eb6d84f21be0fce453f44fb8ba7449c026f84274936b6`<br>Holdout `PASS` (5 detected + 5 controls)<br>Untouched `BLOCKED: source-hash drift` | `INSUFFICIENT_REAL_CASE_EVIDENCE`<br>`wp-us-double-reversal-runtime-candidate-v1`<br>hash `52ee41188dac96f7a1038634f4a3dcfc0bdebae65375e744cc99239e5c9ff243`<br>Holdout `INSUFFICIENT` (5 detected + 4 controls)<br>Untouched `NOT_OPENED` |

## Verdict summary

- `READY_FOR_RUNTIME_PROMOTION`: 0
- `DATA_QUALITY_BLOCKED`: 9
- `INSUFFICIENT_REAL_CASE_EVIDENCE`: 3
- `NEEDS_RECALIBRATION`: 0

The three insufficient Fixed Income scopes stopped after Holdout. The other nine
scopes passed Holdout, but their exact pre-registered Untouched universe could
not be reproduced: 16 of 17 source hashes drifted. LQD was the sole match, which
is not permission to shrink or cherry-pick either asset-class universe.

No scope entered the approved runtime registry. The existing unavailable
provider remains the safe application default.
