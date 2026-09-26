# Keep pick-order evaluation match-disjoint and immutable

The pick-order pipeline uses a versioned exclusion registry to keep every development,
calibration, gallery-source, and smoke-test match out of blind holdouts. A holdout is
created only after its layout calibration and gallery are frozen; changing evidence or
tuning after observing holdout results retires that holdout and requires a fresh
match-disjoint selection. This sacrifices reusable evaluation games and some short-term
training data in exchange for trustworthy exact-order accuracy and coverage estimates.

## Consequences

- Holdout selections record the exclusion-registry hash and frozen component versions.
- A failed holdout may become development material only after it is explicitly retired.
- A passing holdout remains a permanent regression benchmark and is not used for model
  training or gallery enrichment.
