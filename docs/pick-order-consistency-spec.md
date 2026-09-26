# Pick-order consistency specification

Status: accepted on 25 September 2026.

The pipeline favors correct chronological orders over maximum suggestion coverage. It
must fail closed when layout geometry, lock chronology, or hero identity is uncertain.
Confirmed annotation v1 and frontend/backend HTTP interfaces remain unchanged.

## PR #63 correctness scope

1. Use one committed metadata-only exclusion registry covering development,
   calibration, gallery-source, and smoke-test games. Exclude the entire Liquipedia
   match and record the registry hash in each holdout selection.
2. Reject a holdout whose recorded exclusion hash no longer matches the registry.
3. Archive the contaminated selection metadata outside active evaluation inputs and
   remove disposable derived suggestions or crops.
4. Select a complete draft only after all ten cards have settled; an enlarged or
   otherwise invalid preceding frame cannot become the retained candidate.
5. Permit Liquipedia-set elimination only when exactly one identity remains unresolved
   across the entire game and its slot and final artwork are independently verified.
6. Keep the capture CLI and serialized review-result shapes compatible.

The public test seams are the headless holdout selector/evaluator, capture review
result, and weekly rollout gate. Acceptance requires focused regression coverage, the
full backend suite and Ruff, plus two development-game smoke runs per layout. Smoke
abstention is allowed; every selected frame must be manually confirmed as settled and
pre-swap, and any complete suggestion must exactly match its manual order.

## Development and calibration

- Maintain 15 development games per layout: the six existing games plus nine fresh
  verified per-game uploads from different matches.
- Validate all ten crop regions across at least three development games per layout.
- Pair a human-approved calibration manifest with runtime anchor, separator, and crop
  checks. A geometry change creates a new layout version; letterboxing alone does not
  when normalized anchors still pass.
- Add only reviewer-confirmed development crops to the private gallery, then freeze and
  hash the gallery before selecting a holdout.

## Holdout evaluation

- Select 30 M7 and 30 MPL Indonesia Season 18 games only after development inputs are
  frozen. Holdout matches cannot overlap the exclusion registry.
- Label draft order blind, then recheck every inferred, ambiguous, incomplete, or
  disagreeing game after at least 48 hours, along with a deterministic 20 percent of
  clear cases.
- Each layout must produce at least 18 complete suggestions and zero incorrect complete
  orders. One independently verified inferred identity may count toward coverage, but
  direct and inferred accuracy are reported separately and both must have zero errors.
- Tuning after viewing holdout results retires that holdout. A failed retired holdout may
  become development material; a passing holdout remains a permanent benchmark and is
  never added to training or the gallery.

## Weekly rollout and retention

Passing the gate does not activate collection automatically. A human must enable the
weekly review-only workflow, and every suggestion remains unconfirmed until reviewed.
Only separate confirmed non-holdout games may rebuild datasets or train models.

Retain private slot crops, contact sheets, selections, exclusion hashes, component
versions, suggestions, labels, and metrics. Delete bounded media segments, decoded
frame sequences, and full frames after processing.
