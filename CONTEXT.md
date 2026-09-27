# MLBB Draft Data

This context describes the professional-match evidence used to derive and evaluate
chronological MLBB draft orders.

## Language

**Development game**:
A game whose media or labels may influence layout calibration, reference galleries,
thresholds, or extractor behavior.
_Avoid_: Training holdout, test game

**Holdout game**:
A match-disjoint game reserved for blind evaluation and excluded from development,
gallery construction, and model training.
_Avoid_: Development sample, gallery source

**Exclusion registry**:
The versioned record of games and matches that cannot enter a holdout because they
have already influenced development.
_Avoid_: Ignore list, temporary exclusions

**Retired holdout**:
A holdout that is no longer valid for evaluation after its evidence influenced a
pipeline change. Its games may become development games, but never re-enter a holdout.
_Avoid_: Failed holdout

**Review suggestion**:
An automatically proposed chronological pick order that has not been confirmed by a
human reviewer.
_Avoid_: Annotation, ground truth

**Confirmed annotation**:
A human-verified chronological pick order eligible for curated datasets unless the
game is reserved as a permanent holdout.
_Avoid_: Suggestion, inferred order

**Frozen gallery**:
An immutable, hash-identified set of reviewer-confirmed hero references used for one
evaluation cycle.
_Avoid_: Live gallery, mutable references
