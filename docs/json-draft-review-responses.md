# JSON draft review responses

`backend/scripts/build_json_draft_review_batch.py --validate-response` validates a
human response against an exact, externally supplied SHA-256 of an existing
proposal file. Validation does not finalize labels, promote data, or authorize
training. Keep the original proposal bytes unchanged; edit a separate response.

Run from the repository root:

```powershell
python -m backend.scripts.build_json_draft_review_batch `
  --validate-response private/my-human-response.json `
  --review-batch private/json_next_review_batch_20261031/next-draft-review.json `
  --review-batch-sha256 <exact-original-file-sha256>
```

Copy the existing `review_response_template` into a separate response file.
Keep the exposure fields, case identity, case `proposal_sha256`, and each retained
batch `proposal_sha256`. Set the actual reviewer identity, source-inspection
attestation, and actual timezone-aware review start/completion times. Every
submitted decision needs its actual `reviewed_at`, `source_inspected: true`, and a
meaningful source note explaining the evidence checked and any uncertainty.
Do not copy example timestamps or claim checks that were not performed.
The validator checks nonblank notes and declared attestations; it cannot verify
that a human inspected the evidence or that the supplied timestamps are truthful.

The existing per-batch form remains supported: use `Confirm`, `Correct`, or
`Uncertain`, with `corrected_heroes: []` unless correcting. Two-hero batches are
unordered. Corrections must change the proposed set, preserve batch size and
team roster, and avoid conflicting assignments among reviewed batches.

Submit review decisions only for batches actually reviewed. Untouched template
cases may remain included: keep their decisions `null`, `corrected_heroes: []`,
`source_inspected: false`, `source_note: null`, and `reviewed_at: null`.
Alternatively, omit untouched cases or batches from the response entirely.
Neither inclusion nor omission confirms them; both remain unreviewed. An accepted
response must contain at least one case, and every included case must retain a
nonempty batch-entry list (or the attested compact form below). Cases and batches
may appear in any order, identified by their exact IDs. Unknown IDs,
duplicate IDs, duplicate JSON keys at any depth, and extra declarations are
rejected. Omitted decisions remain unreviewed, including when another game has
all seven confirmed. For six proposed games and one fully confirmed game, the
result is 7 confirmed and 35 unreviewed. `Uncertain` stays uncertain.

Every accepted reviewed batch must satisfy
`review_started_at <= reviewed_at <= review_completed_at`. All three timestamps
must include a timezone. Comparisons use instants, so different offsets denoting
the same instant are accepted at either interval boundary.

## Optional full-game confirmation

After a human has checked all seven batches against the source, a case can replace
its `batches` array with `confirm_all_batches`. This is only a shorthand for seven
explicit confirmations. It requires the all-seven attestation and all seven
original batch hashes; it cannot encode uncertainty or corrections. Use the
per-batch form for those decisions or for partial review. Mixing the two forms,
including an empty `batches` alongside the compact form, is rejected.

The following is a **synthetic shape example**, not review evidence. Replace every
identity, hash placeholder, note, and timestamp with the exact proposal values
and the human's actual review record. Retain the ordinary response envelope's
format/exposure fields, reviewer identity, source-inspection attestation, and
start/completion timestamps; this object belongs in its `cases` array:

```json
{
  "game_id": "synthetic-game",
  "match_id": "synthetic-match",
  "game_no": 1,
  "proposal_sha256": "COPY_EXACT_CASE_PROPOSAL_SHA256",
  "confirm_all_batches": {
    "decision": "Confirm",
    "all_seven_batches_checked": true,
    "batch_proposal_sha256": {
      "B1": "COPY_EXACT_B1_SHA256",
      "R1,R2": "COPY_EXACT_R1_R2_SHA256",
      "B2,B3": "COPY_EXACT_B2_B3_SHA256",
      "R3": "COPY_EXACT_R3_SHA256",
      "R4": "COPY_EXACT_R4_SHA256",
      "B4,B5": "COPY_EXACT_B4_B5_SHA256",
      "R5": "COPY_EXACT_R5_SHA256"
    },
    "source_inspected": true,
    "source_note": "Synthetic example: checked all seven phase displays against the linked source evidence.",
    "reviewed_at": "2026-10-31T01:30:00+08:00"
  }
}
```

The compact `reviewed_at` is a human-supplied game-level confirmation event reused
for its seven declared decisions. It does not invent separate batch inspection
times. Per-batch responses preserve each human-supplied timestamp as submitted.
The validator leaves the submitted response unchanged and returns counts only
after every check passes. CLI output includes `confirm`, `correct`, `uncertain`,
`unreviewed`, and `finalized: false`. Any later finalization or promotion remains
a separate hash-bound operation requiring its own authorization and checks.
