# Pattern Evidence v1 Release Closure Report

Date: 2026-08-29

## A. Executive Conclusion

The accepted six-Pattern development line passed the complete release gate and
was reconciled to `main` by fast-forward only. The immutable release reference
is the annotated tag `wealthpilot-pattern-evidence-v1.0`.

```text
PATTERN_EVIDENCE_V1_RELEASED
PATTERN_BRANCH_GOVERNANCE_CLEAN
LONG_TERM_BASELINE_READY
```

This release contains six launch Pattern families and nine approved exact
Pattern × Asset Class runtime scopes. Pattern evidence remains read-only
supporting context and has no Decision, ranking, Broker, Order, Portfolio, or
ExecutionPlan authority.

## B. Release Scope

The release closes the accepted line from Pattern Data and Pattern Core through
detectors, calibration/evaluation governance, the exact runtime registry,
Decision and governed AI context, SSE/persistence, and the read-only frontend
presentation.

Launch families:

- Breakout
- Breakdown
- Rectangle
- Ascending Triangle
- Double Top
- Double Bottom

Approved runtime scopes:

- EQUITY: all six launch families;
- FIXED_INCOME: Breakout, Ascending Triangle, and Double Top.

No new Pattern behavior, detector threshold, calibration, Dataset v2 content,
runtime scope, product feature, dependency version, or schema design was added
during release management.

## C. Pre-release Git State

```text
accepted branch = codex/pattern-final-isolated-acceptance
accepted content HEAD = f0d0e345527ff11149b566604dff5e68acdd7c76
main HEAD = 5492b1dbead99677e0e84d8a177a78e6e7157036
origin/main HEAD = 5492b1dbead99677e0e84d8a177a78e6e7157036
accepted vs main = ahead 44 / behind 0
accepted vs origin/main = ahead 44 / behind 0
working tree = clean
```

`main` and `origin/main` were fetched and rechecked immediately before release
closure. Neither had advanced. No Pattern release tag existed locally or
remotely before this release.

## D. Mainline Reconciliation / Rebase

`main` and `origin/main` were identical and both were ancestors of the accepted
release branch. Rebase was therefore neither required nor performed. There
were no conflicts and no accepted semantics needed reconciliation.

The closure report is the sole release-management commit after the accepted
content HEAD. It changes documentation only.

## E. Regression Results

All automated Python gates used a fresh SQLite path outside the repository with
`DATABASE_URL` unset. No real IBKR E2E was rerun; the final isolated V3 E2E
remains the release acceptance authority.

| Gate | Result |
| --- | --- |
| Full pytest | 880 passed, 7 skipped, 0 failed |
| Technical Pattern + Pattern Data | 341 passed |
| Window invariance | 12 passed |
| Runtime registry + promotion + Decision/AI | 74 passed |
| Immutable Dataset v2 tests | 5 passed |
| Dataset artifacts / partitions | 17 / 51 validated |
| `compileall` | passed |
| Frontend lint | passed, 0 errors/warnings |
| Frontend build | passed; existing bundle-size advisory only |
| Pattern Evidence UI | 6 passed |
| Offline M5 | 18/18, `offline_fixture`, public network attempts 0 |
| `git diff --check` | passed |

Dataset v2 remained unchanged:

```text
logical manifest hash
= 032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c

dataset manifest file SHA-256
= b35f449834c5af43ca039b5678ef3fa0607845438cdb3a83b0871c36a57a6192

runtime validation manifest file SHA-256
= 00bc6ed04f5d91e6cd1bc480a9b2b757585f2633d3098275d313b9f3edbaea36

combined artifact-hash stream SHA-256
= 8b705427a78b9544c10617d55f8faf33b3680fc9021c79db5130236cf4b277ca
```

```text
public network attempts = 0
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Dataset v2 mutation = 0
```

## F. Final Diff Scope

The complete `origin/main...release` diff was reviewed: 44 accepted commits,
293 files, 374,272 insertions, and 39 deletions before this closure report.
Every changed file was classified into one of these release-owned areas:

- Pattern Data and immutable historical-data contracts;
- Pattern Core, Candidate Identity v2, lifecycle, geometry, and boundaries;
- six detectors, calibration contracts, and validation governance;
- runtime provider and exact promotion registry;
- Decision integration and governed AI explanation context;
- read-only frontend Pattern Evidence presentation;
- tests and golden parity fixtures;
- governance/evaluation reports, manifests, SVG evidence, and immutable
  Dataset v2 artifacts.

No unrelated feature, trading path, Portfolio feature, broker mutation,
Consumption Analysis work, or dependency upgrade was present. The only
dependency declaration added by the accepted Pattern line is its frozen TA-Lib
indicator authority.

## G. Main Merge

`main` was updated from `origin/main` with `--ff-only`, then advanced to the
release closure commit with:

```text
git merge --ff-only codex/pattern-final-isolated-acceptance
```

No merge commit and no squash were created. Post-merge `git diff --check`, the
targeted Pattern smoke, and working-tree checks passed. The release branch and
local `main` pointed to the same commit before branch cleanup.

## H. Push Result

`main` was pushed without force. The remote was fetched again and
`origin/main == main` was verified. No remote-advance or push-rejection
reconciliation was needed.

## I. Stable Tag

The new annotated tag is:

```text
wealthpilot-pattern-evidence-v1.0
```

It points to the final pushed `main` release closure commit and records the six
launch Patterns, nine approved scopes, immutable Dataset v2, Candidate Identity
v2, Decision/AI/UI integration, final isolated E2E acceptance, and the three
fail-closed Fixed Income limitations. The tag was pushed once and was not moved
or reused.

## J. Branch Governance Cleanup

All local `codex/*` branches related to the Pattern program were checked by
ancestry and by unique-commit count. Fully absorbed temporary branches were
deleted with normal `git branch -d` only after `origin/main` and the annotated
tag were published. None of these temporary branches existed remotely.

Five separate audit/probe branches have genuine commits absent from the release
line (`git cherry` reported `+`). They were retained exactly as required; they
are not active release branches and must be integrated or archived in a
separate governance task before deletion.

| Branch | Final HEAD | Merged into main | Unique commits remaining | Local deleted | Remote deleted | Action |
| --- | --- | --- | ---: | --- | --- | --- |
| `codex/ascending-triangle-calibration-pilot` | `c225717` | yes | 0 | yes | n/a | deleted safely |
| `codex/ascending-triangle-detector-migration` | `e68d803` | yes | 0 | yes | n/a | deleted safely |
| `codex/breakdown-calibration-pilot` | `545d55a` | yes | 0 | yes | n/a | deleted safely |
| `codex/breakout-breakdown-detector-migration` | `4002dab` | yes | 0 | yes | n/a | deleted safely |
| `codex/breakout-calibration-pilot` | `4eb69ab` | yes | 0 | yes | n/a | deleted safely |
| `codex/double-bottom-calibration-pilot` | `36f565e` | yes | 0 | yes | n/a | deleted safely |
| `codex/double-reversal-detector-migration` | `92f2666` | yes | 0 | yes | n/a | deleted safely |
| `codex/double-top-calibration-pilot` | `17fff33` | yes | 0 | yes | n/a | deleted safely |
| `codex/ibkr-pattern-data-adapter` | `982e640` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-core-foundation-migration` | `186740a` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-dataset-v2-rebaseline` | `da134f9` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-detector-framework-migration` | `d1d17c4` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-evidence-ai-explanation` | `4880436` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-evidence-decision-integration` | `69e2170` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-evidence-decision-integration-audit` | `d329ca8` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-evidence-ui` | `b68b36a` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-final-e2e-acceptance` | `de9f25b` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-final-e2e-rerun` | `cfab698` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-final-isolated-acceptance` | release closure commit | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-runtime-promotion` | `80beb21` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-runtime-promotion-v2` | `b1308b8` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-source-hash-drift-fix` | `c76e631` | yes | 0 | yes | n/a | deleted safely |
| `codex/pattern-window-invariance-remediation` | `45e72fb` | yes | 0 | yes | n/a | deleted safely |
| `codex/real-ibkr-six-pattern-calibration-review` | `1babc55` | yes | 0 | yes | n/a | deleted safely |
| `codex/rectangle-calibration-pilot` | `261315b` | yes | 0 | yes | n/a | deleted safely |
| `codex/rectangle-detector-migration` | `f29a5c9` | yes | 0 | yes | n/a | deleted safely |
| `codex/us-pattern-calibration-validation` | `4659569` | yes | 0 | yes | n/a | deleted safely |
| `codex/ibkr-pattern-data-contract-probe` | `f65b6fc` | no | 1 | no | n/a | retained: unique probe evidence |
| `codex/talib-consolidation-prd-revision` | `ac9ac1f` | no | 2 | no | n/a | retained: unique PRD/audit work |
| `codex/technical-pattern-evidence-readiness-audit` | `84e1ffa` | no | 1 | no | n/a | retained: unique audit work |
| `codex/tovest-pattern-core-migration-audit` | `2cd1af3` | no | 1 | no | n/a | retained: unique audit work |
| `codex/tovest-pattern-reuse-audit` | `1f06798` | no | 1 | no | n/a | retained: unique audit work |

`codex/hide-futu-sync-ui` was inspected but excluded from Pattern cleanup as an
unrelated feature branch. `main`, `master`, and `feat/v3.14-kline-provider`
were also outside this cleanup scope.

## K. Final Repository State

```text
current long-term branch = main
origin/main = main
working tree = clean
stable Pattern reference = wealthpilot-pattern-evidence-v1.0
active Pattern release branch required = no
```

The five protected unique-history branches in section J are governance
exceptions, not alternate long-term development baselines. They were not
silently destroyed to manufacture an empty branch list.

## L. Known Limitations

The following exact scopes remain intentionally unpromoted and fail closed:

```text
Breakdown / FIXED_INCOME
Rectangle / FIXED_INCOME
Double Bottom / FIXED_INCOME
```

This release is six launch Pattern families and nine approved Pattern × Asset
Class runtime scopes, not 12/12 scopes.

## M. Next Development Baseline

```text
NEXT_DEVELOPMENT_BASELINE = main at wealthpilot-pattern-evidence-v1.0
```

Future Pattern expansion and the separate Consumption Analysis line must each
start from the latest `main` on a new short-lived branch. No Consumption
Analysis branch was created by this release task.
