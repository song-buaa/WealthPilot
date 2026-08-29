# Pattern Core Candidate Identity Contract v2

> Identity schema: `wp-pattern-candidate-identity-v2`
>
> Core identity version: `WP-PATTERN-CORE-IDENTITY-2.0`
>
> Scope: prospectively generated Pattern candidates

## 1. Identity authority

A candidate ID identifies one technical market structure independently of the
source envelope used to replay it. The canonical material is:

```text
identity schema version
instrument identity
timeframe
Pattern family / type / direction
formation session date
availability session date
ordered source Pivot availability dates
ordered source Boundary availability dates
Pattern-specific stable bar anchors
detector version
parameter-set identity
```

Pattern-specific anchors are derived from canonical source bar IDs. Level-break
patterns bind the boundary and trigger bar; Pivot-based structures bind the
ordered, typed source Pivot bar identities. These values distinguish separate
real structures without depending on a fetch or cache identity.

## 2. Excluded material

The following must not affect candidate identity:

```text
window-relative session ordinals
runtime window length
source envelope / dataset hash
current cache metadata
floating indicator values
geometry or evidence output values
evaluation session after candidate availability
```

Dense session ordinals remain authoritative for causality, geometry and
lifecycle calculation inside one normalized execution. They are not stable
cross-window identity.

## 3. Runtime normalization and indicator warm-up

The promoted runtime normalizes every sufficient current-data envelope before
`PatternInputMapper`:

```text
arbitrary sufficient closed-session envelope
        ↓ latest 300 closed sessions
80-bar discovery / indicator warm-up
+ 220-bar current discovery horizon
        ↓
Pattern Core + TA-Lib
```

The 80-bar prefix is derived from the maximum `minimum_history_bars` among the
nine promoted scopes. It also exceeds the longest canonical indicator period,
EMA50. A 300-, 600-, or 1,950-bar source envelope ending on the same closed
session therefore supplies exactly the same bars, source hash and TA-Lib seed
to runtime Pattern Core.

Dataset v2 evaluation replay is not rewritten or truncated by this product
runtime normalization contract.

## 4. Version and compatibility

Candidate IDs generated under v2 are intentionally different from v1 IDs.
Existing persisted Decision/message snapshots remain immutable and readable:
downstream contracts treat `candidate_id` as an opaque stable string and do not
recompute it during history restore. No database migration or snapshot rewrite
is required.

The v2 contract applies only to newly generated Pattern results. Cross-version
ID equality is neither promised nor inferred.

## 5. Invariants

- Same instrument, sessions, `as_of`, calibration and structure anchors produce
  the same candidate ID across equivalent source windows.
- Different stable structure anchors produce different candidate IDs.
- All six launch Patterns use the same candidate identity schema.
- Identity has no Decision, ranking, execution, Broker or Portfolio authority.
- Changing this identity material requires another explicit schema version.
