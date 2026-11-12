# Draft-quality policy replay

The public command validates and replays a frozen recommendation-quality policy against
expert judgments supplied in private artifacts. No real expert benchmark or approved
numerical policy is included in this change. The public tests use synthetic records,
names, dates, approvals and values; their success is not empirical accuracy or expert
approval.

From the repository root:

```powershell
python -m backend.scripts.replay_draft_quality_policy --manifest private/draft-quality/manifest.json --manifest-sha256 <trusted-manifest-sha256> --output report-001.json
```

The output is relative to the private manifest directory. Use an existing private
directory and a new filename. Traversal outside that directory and overwriting an
existing file are refused. Keep every real artifact and report private and untracked.

Exit 0 means the command produced a valid report, including an explicitly disabled gate.
Inspect `verification.release_gate`; command success alone is not policy readiness.
Exit 1 means an input was rejected or a report could not be safely created. Stdout
contains a safe status, run ID and verification digest, without paths, case identities,
judgments or exception traces. The command never fits, activates or replaces a model.
Its gate covers recommendation quality only; it cannot approve an entire release.

## Frozen artifacts and trust

A trusted SHA-256 supplied outside the manifest pins its exact UTF-8 bytes. Each referenced
artifact has a relative `path` and exact-byte `sha256`. The command checks a hash before
decoding its payload, bounds reads, rejects duplicate JSON keys and nonfinite numbers,
and resolves paths beneath the manifest directory. Hashes detect modification; they do
not authenticate a reviewer or establish that an expert judgment is correct.

The manifest has schema version 1, kind `draft_quality_manifest`, an opaque version,
an explicit `synthetic` boolean, an identity-only `selection` of unique case IDs and
match IDs, and `artifacts` references. Validate and freeze that selection before opening
the corresponding benchmark payloads. Do not inspect unrelated protected labels.

| Artifact | Kind | Required content |
| --- | --- | --- |
| Policy | `recommendation_quality_policy` | Versioned definitions and explicit numerical fields below; no numerical defaults |
| Benchmark | `expert_draft_quality_benchmark` | Exact selected cases, judgments and provenance; matching synthetic declaration |
| Approval | `recommendation_quality_policy_approval` | Actual approval status/reference/date bound to the exact policy and benchmark hashes |
| Replay | `recommendation_quality_replay` | Hash-bound, paired candidate/incumbent outputs over the frozen selection |

The manifest `freeze` record explicitly states `policy_before_results`,
`judgments_before_results`, `benchmark_before_results` and
`excluded_from_development`. Its `earlier_result_exposure` is `none`, `disclosed`
or `unknown`; disclosed or unknown exposure disables independent readiness.
These are hash-pinned operator attestations, not chronology inferred from file mtimes.

For real inputs, missing approval, independent expert evidence, case evidence, freeze
attestations or replay leaves the gate disabled **before the replay artifact is opened**.
A report distinguishes checked artifact bytes from unchecked source evidence.
`source_evidence_bytes_checked=false` means private expert/eligibility record hashes are
references only: the approving evaluator must verify those records. The command does not
claim an unavailable historical source or reviewer record was checked.

Review and approval dates use supplied `YYYY-MM-DD` values. The original private evidence
must retain its actual timestamp precision. Same-day order relies on the explicit freeze
attestation; the command does not invent times. A judgment or approval dated after first
result exposure is rejected. Prior exposure must be disclosed and cannot be relabeled
independent by changing a date.

## Expert cases

Each benchmark has schema version 1, kind, version, synthetic flag and `cases`.
Each case records the following private fields:

| Field | Meaning |
| --- | --- |
| `case_id`, `match_id` | Exact membership of the frozen identity selection |
| `stage`, `side` | B1/blue, R1,R2/red, B2,B3/blue, R3/red, R4/red, B4,B5/blue, R5/red |
| `patch_evidence` | `verified`, explicitly `patch_neutral`, or unresolved `unknown` |
| `coverage` | Frozen context-evidence category: `supported`, `limited` or `fallback` |
| `eligible_heroes`, `unavailable_heroes` | Evidence-backed historical eligible universe and actual pre-batch exclusions |
| `eligibility_evidence_sha256` | Reference to the private verification record; never infer eligibility from today's simulator |
| `review_provenance` | `independent`, `proposal_assisted` or `unreviewed` |
| `review_record_sha256`, `reviewed_on` | Actual private expert evidence reference and supplied review day |
| `options` | Unordered hero option, judgment and composition assessment |

Each option has `heroes`, `judgment` and `composition`. Judgment is exactly
`acceptable`, `unacceptable` or `unjudged`. An option absent from the expert list
is unjudged. Observed professional choices and co-occurrence never supply a judgment.
Composition is `pass`, `fail` or `unjudged`; an acceptable judgment cannot contradict
a known illegal option or failed composition.

Singleton stages use one hero per option. R1,R2, B2,B3 and B4,B5 use two distinct heroes
as one unordered option. Reversing the pair has identical meaning; no within-pair order
or partial per-hero credit is invented. Legality checks use the frozen historical
universe, pre-batch exclusions, batch size and uniqueness. Missing eligibility evidence
or unknown patch evidence disables real readiness; patch-neutral cases remain explicit.

Independent expert judgments must be established without first showing that reviewer
candidate proposals. Proposal-assisted material remains disclosed in
`declared_review_provenance` and cannot satisfy the independent benchmark prerequisite.

## Policy and numerical approval

The metric version is `match_certified_option_precision_v1`; paired scoring is
`unordered_exact_option_v1`. The policy must explicitly supply all fields below.
No real value is approved or calibrated by this implementation.

| Field | Definition / approval needed |
| --- | --- |
| `top_k` | Frozen expected number of unique options per responding case |
| `precision_target` | Minimum lower confidence bound on match-macro certified acceptable rate |
| `min_matches` | Independent-match floor overall |
| `min_matches_per_stratum` | Independent-match floor for every gated category |
| `min_response_coverage` | Required fraction of frozen cases receiving a response |
| `min_judgment_coverage` | Required judged fraction of all emitted options |
| `non_regression_margin` | Maximum tolerated paired candidate-minus-incumbent loss |
| `confidence` | One-sided lower-bound confidence and central-interval confidence |
| `bootstrap_replicates`, `bootstrap_seed` | Frozen deterministic match-resampling procedure |
| `max_interval_width` | Maximum central-interval width for candidate precision and paired difference |
| `required_strata` | Frozen category lists for stage, side, patch evidence and context coverage |

Every observed category is reported and gated even if omitted from `required_strata`.
A declared category with no cases disables readiness. Approve relevant strata before
result inspection; do not choose subgroups after seeing performance.

An approval record has schema version 1, approval kind, `status` (`proposed`,
`approved` or `rejected`), exact `policy_sha256` and `benchmark_sha256`,
actual `approved_on` day, private `reference`, and
`independent_evidence_verified`. Only an actual approving decision may be recorded as
approved. A synthetic test approval is never a real approval. Changing numbers,
definitions, membership or judgments changes the hashes and requires fresh approval.
The current real numerical policy remains unresolved; no approved policy is committed.

## Metrics and uncertainty

A responding case is expected to emit the smaller of `top_k` and the number of
feasible unordered options in its frozen legal universe. Missing cases and empty output
are abstentions; partial nonempty output fails the completeness check. The denominator
does not shrink when an output is omitted.

- **Judged precision** is acceptable / (acceptable + unacceptable) emitted options.
  It is descriptive and null when there are no judged options.
- **Judgment coverage** is judged / all emitted options. Unknown options stay unjudged.
- **Response coverage** is responding / all frozen benchmark cases; abstention counts
  are explicit.
- **Certified acceptable rate** is acceptable / frozen expected option slots.
  Unknown and missing outcomes receive no certified-success credit; they are not
  relabeled unacceptable. This is a conservative evidence bound over the fixed output
  opportunity set, preventing selective judgment or abstention from manufacturing
  perfect precision.
- Options, positions and seven pick batches within one match are correlated. First
  aggregate certified rates within each match, then weight independent matches equally.

The bootstrap resamples whole matches and preserves candidate/incumbent pairing and all
within-match positions. The one-sided lower bound is percentile `1-confidence`; the
central interval uses percentiles `(1-confidence)/2` and `(1+confidence)/2`.
The non-regression check uses candidate-minus-incumbent match scores on the same frozen
cohort. Every overall and stratum decision needs the sample floor, bounded interval
width, candidate precision target, paired non-regression bound, response/judgment
coverage, and valid legality/composition evidence. Failed or unassessed composition and
illegal emitted options disable the gate.

These percentile intervals approximate observed between-match sampling variation under
independent, representative match sampling. They do not repair selection bias,
contamination, poor expert judgments, missing strata or historical uncertainty.
A degenerate bootstrap cannot rule out unseen rare failures. Minimum counts are floors,
not proof of adequate power; wide intervals remain insufficient evidence. No metric is
an optimal-pick or win-probability estimate.

The report separates deterministic `verification` from per-run ID, entry point and UTC
start time. Its digest is SHA-256 of sorted, compact UTF-8 verification JSON.
Synthetic replay can pass metric mechanics while `release_gate.enabled` remains false.

## Human inputs still required

Keep the completed tables and records private. Select identities before attaching
evidence, and do not open protected payloads outside the selected scope.

| Human task | Bounded deliverable |
| --- | --- |
| Evaluation owner | Freeze position/match identities, exclusions, historical universe evidence and required strata before results |
| Expert reviewer | Independently judge the selected unordered options; distinguish unacceptable and unjudged, and assess composition |
| Policy owner | Resolve every numerical field, justify sample/uncertainty requirements, and explicitly approve exact policy/benchmark hashes |
| Independent evaluator | Verify expert/eligibility records and honest exposure history; preserve actual review-date precision |

Compact position review template (repeat only for selected identities):

| Position / match | Batch / side | Pre-batch evidence ref | Unordered option | Acceptable / unacceptable / unjudged | Composition | Eligibility ref | Review day / prior exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| selected private ID | canonical batch | private pinned ref | one hero or unordered pair | expert supplies | pass / fail / unjudged | private pinned ref | actual supplied precision |

Until these actual records, approved numbers and sufficient independent samples exist,
the technical replay is available but the real benchmark and numerical-approval
acceptance criteria of issue #79 remain unmet.
