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
existing file are refused. Path-resolution failures, including link loops, produce safe
structured rejections without echoing filesystem paths. Keep real artifacts and reports
private and untracked.

Exit 0 means the command produced a valid report, including an explicitly disabled gate.
Inspect `verification.release_gate`; command success alone is not policy readiness.
Exit 1 means an input was rejected or a report could not be safely created. Stdout
contains a safe status, run ID and verification digest, without paths, case identities,
judgments or exception traces. The command never fits, activates or replaces a model.
Its gate covers recommendation quality only; it cannot approve an entire release.

## Expert charter/scenario preflight

Use the same public CLI with `--preflight` for the separate prospective metadata path:

```powershell
python -m backend.scripts.replay_draft_quality_policy --preflight --manifest private/draft-quality/scenario-manifest.json --manifest-sha256 <trusted-manifest-sha256> --output preflight-001.json
```

This path validates declarations and bindings only. It does not create scenarios,
judge options, inspect source/sign-off records, run recommendations, calculate metrics,
approve a policy, or establish empirical admissibility. Real readiness remains disabled
for every preflight report. Without `--preflight`, historical policy replay retains its
existing contract below.

All three documents use schema version 1 and an opaque `version`. The manifest kind is
`expert_scenario_preflight_manifest`; its only other fields are `synthetic` and
`artifacts`. The artifacts map contains exactly `charter` and `scenarios`, each using
the existing relative-path and SHA-256 binding. Hashes are checked before JSON decoding.
No policy, benchmark judgments or candidate output is accepted in this manifest.

The charter kind is `organized_draft_expert_charter`:

| Field | Declaration |
| --- | --- |
| `patch` | One named patch, as a nonempty string; no inferred deployment or patch-neutral substitution |
| `ruleset` | The declared, versioned organized-draft ruleset identifier |
| `eligibility_source` | Source-record binding for the eligible hero universe |
| `effective_disables_source` | Source-record binding for disables and their effective scope |
| `expert_signoff_record` | Explicit expert sign-off record binding; its presence is not approval verification |

Patch, ruleset and source/sign-off record IDs must contain a non-whitespace character;
presence checks do not normalize or rewrite the supplied declarations.
Each source/sign-off binding contains exactly an opaque `record_id` and a SHA-256.
These are references only and are never opened, fetched, or treated as authenticated
signatures. The charter bytes are checked; the referenced evidence bytes are not.

The scenario-set kind is `authored_draft_scenario_set`. It contains `charter_sha256`,
`scenario_type`, and a nonempty `scenarios` list. Its charter hash must match the checked
charter bytes. `scenario_type` is `expert_authored_nonhistorical` for declared real
metadata or `synthetic_software_fixture` when the manifest has `synthetic: true`.
Historical matches belong to the existing replay path and cannot be retyped here.
Public positive fixtures are explicitly synthetic software metadata, not real cases.

Each scenario has exactly `scenario_id`, `author_id`, `base_family_id`, `split`, and
`judgment_author_id`. There is no `match_id`, label, recommendation, context payload,
or option judgment in this metadata schema. IDs are explicit opaque declarations.
`split` is `development` or `locked_evaluation`; every scenario and derivative declared
in one base family must stay in the same split. Duplicate scenario IDs and family
leakage are rejected. Declaring a false family cannot be detected from these bytes.
`judgment_author_id` may be null while judgment ownership is unresolved. The scenario
author may also be the declared judgment author; the report discloses that dependence
instead of asserting independence.

Reports include aggregate scenario/family/author counts, same-author judgment counts,
unknown judgment-author counts, checked artifact hashes and versions, and explicit
`source_evidence_bytes_checked: false` and `evaluation_performed: false`. They contain
no author/scenario identities or source record IDs. The report makes no statistical
claim: scenario-family evaluation units, author effects, uncertainty methods and
numerical policy remain unresolved. Historical match floors and match bootstrap
semantics are not applied to authored scenario families.

Malformed, unsupported, payload-bearing, unbound or leaking metadata receives a safe
structured rejection. The same bounded parser, private output confinement, exclusive
creation, deterministic verification digest and run-metadata separation apply.
Input bytes are preserved. A valid report exits 0 while its release gate stays disabled;
CLI success cannot authenticate an expert, establish honest access history, prove
unrecorded access absent, verify eligibility/disables, or satisfy issue #79's real
benchmark and policy-approval acceptance criteria.

## Frozen artifacts and trust

### Declared nonhistorical charter form

The same `--preflight` seam also accepts a charter of kind
`declared_nonhistorical_expert_charter` (schema version 1). This additive form keeps
the original charter and historical replay contracts unchanged. Its exact fields are
`schema_version`, `kind`, `version`, `patch`, `ruleset`, `declaration`, and
`expert_signoff`. The last two fields use local relative `path`/`sha256` artifact
bindings; `expert_signoff` may be null. Hashes are checked before decoding, and the
same size, parser, private-path and exclusive-output controls apply.

The declaration is a `nonhistorical_benchmark_declaration` with exactly:

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind`, `version` | Versioned metadata header |
| `patch`, `ruleset` | Must match the charter |
| `source_authority` | `declared_notes_not_publisher_verified`; no publisher authentication claim |
| `rules` | Nonempty list of explicit rule statements (each bounded as ordinary metadata text) |
| `hero_universe` | Local artifact binding; kind `proposed_expert_scenario_hero_universe`, schema version 1, and a nonempty unique `proposed_allowed_heroes` list |
| `benchmark_exclusions` | Unique explicit subset of that declared universe; expert design choices only |
| `official_eligibility_status`, `official_disables_status` | Both `unknown`; the declaration cannot satisfy historical official-evidence requirements |
| `acceptance_record` | Local hash-bound JSON object; preserves exact review provenance bytes without authenticating its author or interpreting it as final sign-off |
| `source_records` | Nonempty list of opaque `record_id`/`sha256` source references, not opened or authenticated |
| `historical_restriction_note` | Bounded text retained as context only; never a filter on this declared universe |

The hero-universe artifact may retain its original proposal/provenance fields. Its
list is a declared design scope, even after a reviewer accepts it; it does not become
verified official eligibility. The report counts the full declared universe and the
set remaining after explicit benchmark exclusions. A conditional professional ban
note cannot remove heroes. Applying any historical competition restriction still
requires evidence of the applicable rule, relevant hero addition/rework effective
date, match date, and exact boundary semantics through the historical process.

A null sign-off returns `status: pending` and `expert_signoff_missing` after checking
the declaration, universe and acceptance bytes. Scenario bytes are not opened. No
placeholder source or signature is needed. For a non-null sign-off, the local record
has exactly `schema_version`, `kind: benchmark_declaration_signoff`, `version`,
`status: signed`, `declaration_sha256`, `signer_id`, and the supplied `signed_on` day.
Its declaration hash must match the exact checked declaration. That declaration
transitively binds the rules, universe, exclusions, source references, and acceptance
record. The sign-off targets declaration bytes rather than its containing charter,
avoiding a circular self-hash. Any changed dependency requires a changed declaration
hash and fresh sign-off. The scenario set still binds the complete charter bytes.

This checks a sign-off declaration's structure and lineage, not cryptographic
authenticity, reviewer identity, source truth or honest custody. Checked declaration
artifact bytes are reported separately from unchecked source-evidence bytes. A signed
synthetic example can validate metadata and family separation, but every declared
preflight report retains a disabled release gate and performs no evaluation. Numerical
policy, readiness and actual final expert sign-off remain separate prerequisites.

#### User-relayed verbal approval

A declared charter may instead bind a `benchmark_declaration_relayed_verbal_signoff`
record (schema version 1). Its exact fields are `schema_version`, `kind`, `version`,
`scope: declared_nonhistorical_benchmark_only`, `unsigned_charter`,
`approval_attestation`, and `approved_statement_sha256`. The two artifact references
use local `path`/`sha256` bindings. The unsigned charter must equal the supplied
charter with only `expert_signoff` replaced by null, preserving its version,
declaration and ruleset. This separate form does not relax the dated sign-off form:
`benchmark_declaration_signoff` still requires its actual `signed_on` day.

The attestation must be a JSON object with integer `schema_version: 1`,
`kind: user_relayed_final_verbal_benchmark_approval`, and
`approval_status: final_verbal_approval_user_relayed`. It must contain:

| Field | Structural requirement |
| --- | --- |
| `expert_id` | Nonblank declared identity; not authenticated |
| `relay_source` | `user_instruction_in_current_chat`; not direct expert contact |
| `direct_expert_contact`, `independent_identity_authentication`, `cryptographic_signature` | Each explicitly false |
| `verbal_signoff_at`, `verbal_signoff_day` | Each explicitly null; the event time and date are unknown |
| `recorded_at`, `recorded_timezone` | An actual ISO recording timestamp with seconds and UTC offset, plus a nonblank supplied timezone label; neither supplies an event date |
| `approved_statement_verbatim` | Exact supplied statement, at most 10,000 characters, containing the bound declaration hash and the scope phrase `nonhistorical organized five-player benchmark` |
| `approved_statement_sha256` | Exact UTF-8 statement hash; must equal the independently bound sign-off record's hash |
| `unsigned_charter_sha256`, `approved_declaration_sha256`, `unchanged_hero_universe_sha256` | Must match the artifact bytes checked by this run |
| `source_package_receipt_sha256` | Valid SHA-256 provenance reference; receipt/source truth is not authenticated by this check |
| `numerical_policy_approved`, `activation_authorized` | Each explicitly false |

Additional attestation audit fields are retained without interpreting them as
approval. The enclosing artifact hash pins all of those bytes. The statement hash,
textual scope markers and structured scope declarations are consistency checks;
they cannot decide whether a person actually uttered the statement or verify its
natural-language meaning. Recording a relay never claims an authenticated identity,
cryptographic signature, official source verification, or release permission.

#### Charter-only metadata command

When no scenario manifest or set exists, use the same public runner's charter-only
mode instead of constructing placeholder scenarios:

```powershell
python -m backend.scripts.replay_draft_quality_policy --charter private/draft-quality/charter.json --charter-sha256 <trusted-charter-sha256> --output charter-report-001.json
```

`--charter` is mutually exclusive with `--manifest` and `--preflight`; supply only its
matching hash argument. Output remains a new private path relative to the charter
directory, with the same confinement, parser and redaction controls. The report kind
is `declared_charter_preflight_report`. An unsigned charter is `pending`; a checked
approval can be `validated`. Relayed approval reports
`approval_method: user_relayed_final_verbal`, `approval_structurally_validated: true`,
`approval_identity_authenticated: false`, and `approval_event_time_known: false`.
No identity, statement, recording timestamp or relay text appears in the report.

Charter-only success reports `scenario_preflight_performed: false`,
`scenario_bytes_checked: false`, `evaluation_performed: false`, and a disabled release
gate. It does not create missing scenarios or satisfy full scenario preflight. The
existing scenario command still requires its genuine manifest and scenario set.

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
  The primary `certified_precision` field, interval mean and gate all use this same
  equal-weight match mean; option-slot counts remain descriptive aggregates.

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
