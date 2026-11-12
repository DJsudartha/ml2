# Pick-order qualification calibration and replay

This command evaluates pick-order reconstruction only. It does not accept games,
change annotations, refresh sources, inspect media, train a model, or change serving.
Recommendation quality and win probability are outside this policy kind.

The real benchmark and numerical-policy acceptance criteria for #78 remain unmet
until the private inputs below are supplied. No real calibration threshold, sample
requirement or approval is supplied by this change. The public tests use explicitly
synthetic games and illustrative numbers; those numbers are not recommended defaults.

## Freeze before opening evaluation results

1. An authorized data steward selects identity-only calibration and evaluation
   manifests. Use one game from each distinct, source-qualified match and one
   layout, recognition version and commitment-rule version per cycle. The two
   partitions must share neither game nor match. Existing development, calibration,
   gallery, smoke-test and retired-holdout exclusions stay outside evaluation.
2. Freeze the calibration evidence and proposed numerical requirements. A separate
   evaluation custodian freezes the untouched evaluation evidence without showing
   its results to the person selecting thresholds. Its identity manifest commits
   the calibration-manifest, requirements and exclusion-registry byte hashes.
3. Run `calibrate`. It checks identities and hashes before decoding calibration
   evidence. It never opens the evaluation payload. Recognition candidates are only
   observed calibration scores; select the candidate admitting the most games while
   meeting the proposed calibration precision, coverage and minimum qualified count.
   Break a tie with the higher threshold. No qualifying candidate produces a null
   threshold and an explicit disabled decision.
4. The policy owner reviews the concrete proposed values and calibration disclosure,
   then supplies an explicit approval record bound to the exact policy bytes.
   The immutable policy retains `approval_status=pending`; the separate approval
   record supplies sign-off. Editing a policy invalidates its approval.
5. Run `evaluate` once the frozen policy and approval exist. It validates the frozen
   contract and approval chronology before opening evaluation evidence. Missing
   approval leaves that payload unopened. Changes to the calibration material,
   proposed numerical requirements, components or exclusions require a new policy
   cycle. An evaluation that informed development or tuning must be retired under
   the existing exclusion policy; do not claim it is untouched again.

All hashes cover exact file bytes, with no line-ending normalization. Hashes prove
identity, not that a reviewer was blind or that an approval was authorized. Private
review and approval records must establish those facts; the runner validates their
supplied attestations and does not authenticate people or reopen their media.
The evaluator implementation is the one exception: its source hash uses UTF-8 LF
line endings for cross-platform checkout compatibility. The policy pins this hash
and `exact-unordered-seven-batch-order-v1`; changed evaluation code or metric
definitions require a new frozen policy cycle.

## Public commands

Create an access-restricted private directory first. Both output files must be new
files below the explicitly designated `--private-root`, with existing parents.
The caller is responsible for that directory's operating-system permissions. The
command resolves paths, refuses escapes and existing files, and never overwrites.

```powershell
python -m backend.scripts.replay_order_qualification calibrate `
  --calibration-manifest private/qualification/calibration-manifest.json `
  --evaluation-manifest private/qualification/evaluation-manifest.json `
  --exclusion-registry backend/data/pick_order_exclusions.json `
  --calibration-results private/qualification/calibration-evidence.json `
  --requirements private/qualification/proposed-requirements.json `
  --policy-output private/qualification/policy-v1.json `
  --output private/qualification/calibration-report-v1.json `
  --private-root private/qualification

python -m backend.scripts.replay_order_qualification evaluate `
  --calibration-manifest private/qualification/calibration-manifest.json `
  --evaluation-manifest private/qualification/evaluation-manifest.json `
  --exclusion-registry backend/data/pick_order_exclusions.json `
  --policy private/qualification/policy-v1.json `
  --approval private/qualification/policy-v1-approval.json `
  --evaluation-results private/qualification/evaluation-evidence.json `
  --output private/qualification/evaluation-report-v1.json `
  --private-root private/qualification
```

`calibrate` exits 0 only when it produces a calibrated proposal. That is not approval
or readiness. `evaluate` exits 0 only for a passing, approved real-evidence decision;
synthetic evidence always leaves `qualification_gate_enabled=false` and exits 1,
even if `evaluation_passed=true` demonstrates successful software mechanics.
No action in either mode performs automatic acceptance or modifies another file.

## Private input contracts

All documents are schema version 1 objects. Keep genuine manifests, evidence,
requirements, policies, approvals and reports private and out of commits.

| Document | Required fields |
| --- | --- |
| Identity manifest | `kind=order_qualification_manifest`; distinct `version`; `partition=calibration` or `qualification_evaluation`; `data_origin=real` or `synthetic`; `frozen_at`; `recognition_version`; `commitment_rule_version`; exact `evidence_sha256`; `games` containing only identity metadata |
| Game identity | Nonempty `game_id`, source-qualified `match_id`, and `layout_id`. Repeated matches within either partition and any cross-partition overlap are rejected. |
| Evaluation manifest additions | `untouched=true`; `results_exposed_before_policy=false`; exact `calibration_manifest_sha256`, `requirements_sha256`, `exclusion_registry_sha256` precommitted before calibration. These are honest exposure declarations, not an override for retired data. |
| Evidence | `kind=order_qualification_evidence`; `games` with exactly the selected IDs, no extras or duplicates. Each record has capture status, usable-evidence status, recognition status/score, commitment status/version, proposed batches and reference provenance. |
| Proposed requirements | `kind=order_qualification_requirements`; `version`; `policy_version`; every numerical field below. There are no omitted-field defaults. |
| Approval | `kind=order_qualification_approval`; `policy_sha256` of the exact generated file; matching `policy_version`; `approved=true`; genuine private `approval_record_id`; matching `data_origin`; honest `approved_at`. The record ID references accountable sign-off without publishing a person's identity. |

The existing version 1 exclusion-registry format is reused unchanged. Its original
file bytes and canonical registry ID are both frozen in the policy lineage.
Unknown versions, changed hashes, mismatched components, excluded evaluation matches,
unreadable input and malformed schemas yield safe disabled reports.

Dates may retain day precision (`YYYY-MM-DD`). Offset-aware ISO timestamps are also
accepted. A day describes the full day, not an invented midnight review time. If
that precision cannot establish manifest freeze before policy calibration, or
approval after policy freeze and before replay, the decision is disabled. Generated
run timestamps describe the command's execution, never a human review.

### Numerical requirements and commitment

| Numerical field | Meaning |
| --- | --- |
| `minimum_order_precision` | Required exact-order proportion among all policy-qualified games, in `(0,1]`. |
| `minimum_coverage` | Required policy-qualified fraction of every selected game, in `(0,1]`; applied during calibration and evaluation. |
| `minimum_calibration_qualified_games` | Positive integer qualified-game count required when selecting an observed recognition threshold. |
| `minimum_evaluation_games` | Positive integer count of selected evaluation games from distinct matches. |
| `minimum_evaluation_qualified_games` | Positive integer count required for the evaluation precision denominator. |

These fields and the selected recognition threshold are concrete numerical-policy
deliverables requiring explicit owner approval before independent evaluation.
The runner supplies no real values. The approval pins the complete final policy,
including those fields and its recognition/commitment versions.

Every evidence row supplies `capture_status=complete|interrupted|failed`, boolean
`usable_evidence` and `identities_complete`, and a finite `recognition_min_score`
in `[0,1]`. `commitment_status=verified|missing|uncertain` is separate and must use
the frozen `commitment_rule_version`. A declared verified commitment requires a
`commitment_evidence_sha256`. This is a reference to supporting private evidence;
the runner does not inspect those underlying bytes. Capture failures remain visible
in the capture-status counts even if saved material is usable.

Eligibility always requires all of: a valid complete order, usable evidence,
complete recognition meeting the frozen threshold, and verified commitment under
the frozen rule. High recognition or a complete proposal alone cannot qualify.
There is no switch to lower commitment criteria. An alternative rule requires a new
version, calibration, independent evaluation and concrete approval.

### Reference labels and unordered pairs

Each `reference` has `provenance`, boolean `reviewed`, boolean
`machine_answers_shown`, and its seven `batches` when known:

| Provenance | Required disclosure and use |
| --- | --- |
| `independent_blind` | The reviewer established the reference without first seeing machine answers; `machine_answers_shown=false`. Required for every evaluation reference. |
| `proposal_assisted` | The reviewer saw machine answers before finalizing the reference; `machine_answers_shown=true`. Disclosed calibration material only; it is never independent gold. |
| `missing` | No reviewed reference; `reviewed=false`. It cannot silently disappear from the evaluation denominator. |

Contradictory exposure fields are rejected. A declaration of independence cannot
replace the private review record establishing it.

Both proposed and reference orders use B1, {R1,R2}, {B2,B3}, R3, R4, {B4,B5}, R5,
with batch sizes 1,2,2,1,1,2,1 and ten unique heroes. Equality ignores order within
each pair and preserves the order between batches. Partial or duplicate orders are
incomplete; seven batches are never counted as seven independent observations.

## Observable decisions

The aggregate report has a `run_id`, explicit CLI entry point and actual UTC start
time. `verification` and its SHA-256 are stable for repeated evaluation of the same
frozen inputs; run metadata is separate. The digest covers the UTF-8, sorted-key,
two-space-indented JSON plus final newline used by the command.

Completeness, usable evidence, recognition, commitment and capture status have
separate counts. Completeness and qualification coverage use all selected games.
Exact-order precision uses all qualified games and is null unless every one has a
complete independent reference; assisted or missing labels are not dropped to
manufacture precision. Every selected evaluation game also needs an independent
reviewed reference for the gate to pass. Reports disclose provenance categories,
the frozen numerical values, sample unit and actionable failure codes.

The unit is one game per distinct match. All proportions are descriptive; this
implementation makes no confidence-interval or population-accuracy claim. It does
not treat correlated crops or batches as independent games. Sample sufficiency is
the explicitly approved minimum, not a statistical guarantee inferred by code.

Reports verify the supplied files' byte hashes and lineage. They explicitly say
`underlying_evidence_bytes_checked=false`; media, external review records, reviewer
independence and approval authority still need the private evidence audit. Reports
and stdout omit game identities, labels, reviewer details, local paths, raw errors
and exception traces. Keep even aggregate operational reports private.

## Exact outstanding human/evidence inputs

No protected labels or media were opened for this implementation. Software success
on synthetic inputs does not complete the benchmark, calibration or approval work.
The next review request is bounded to one layout and one recognition/commitment
version, with exactly the identity membership frozen below; it requests no new media
collection or processing.

| Needed input | Human task before the next dependent action |
| --- | --- |
| Concrete proposed numerical requirements | Provide precision and coverage targets plus calibration/evaluation sample minima in the fields above. They remain proposed until the complete calibrated policy is explicitly approved. |
| Authorized identity-only sampling plan | Select exactly the proposed calibration and evaluation counts from distinct source-qualified matches, restricted to one layout/version. Freeze the exact private IDs before opening payloads. Exclude existing protected holdouts and all development/tuning matches from evaluation. If the authorized inventory cannot meet the counts, record that shortfall and keep the gate disabled. |
| Calibration reference records | Supply reviewed orders and honest independent-versus-assisted exposure for those selected calibration identities, with machine outputs and separate recognition/commitment evidence. No unreviewed proposal becomes a reference. |
| Untouched evaluation reference records | An independent reviewer supplies complete seven-batch references for the selected evaluation identities without seeing proposed answers first. Preserve supplied timestamp precision and any earlier exposure; retire contaminated selections. |
| Frozen manifests and concrete approval | Custodian freezes evidence hashes and the precommitted calibration contract. Policy owner then approves the resulting exact policy byte hash after reviewing its concrete values and provenance. Do not tune those values after evaluation results become visible. |

Until those real inputs and approval exist, automatic qualification remains disabled.
