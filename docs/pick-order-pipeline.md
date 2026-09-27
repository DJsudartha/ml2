# Pick-order collection: current architecture and acceptance

## Ownership

- `liquipedia/match_finder.py`: normalize API responses, preserve game/series VODs
  and Liquipedia match/game IDs. Unknown winner values remain unknown.
- `data/raw_games.py`: compatible game rows; role slots are not chronological picks.
- `data/liquipedia_vods.py`: URL parsing, timestamp preservation, source provenance,
  optional uploader/availability metadata checks. Never persist signed stream URLs.
- `data/vod_sources.py`: LP-first manifest resolution shared by CLI and weekly runs;
  existing official-channel discovery is an explicit fallback.
- `data/collection.py`: deterministic distinct-match selection and acceptance counts.
- `data/vod_pipeline.py`: bounded media, layout detection, evidence, suggestions,
  resumable jobs. No dataset rebuild or model training.
- `data/complete_draft.py`: in-memory first-settled pre-swap detection, calibrated
  player-card separators, and completion-only same-VOD portrait-pool matching.
- `data/complete_draft_vod.py`: game-level VOD verification, resumable input
  identities, and bounded decoder command construction.
- `data/complete_draft_identity.py`: layout resolution, completion-tracker setup,
  and time-separated in-memory identity observations.
- `data/broadcast_hero_names.py`: in-memory OCR for supported vertical MPL hero
  labels, restricted to Liquipedia's five final heroes per team.
- `data/hero_portrait_matcher.py`: normalized temporal portrait scoring and a
  conservative one-to-one team assignment for M7 artwork.
- `data/vod_pick_order_suggestions.py`: direct per-game identity evidence and
  guarded one-missing-name elimination; the fixed role-remap path is legacy.
- `data/pick_order_results.py`: canonical result records and constructors shared
  by capture, suggestion, and weekly orchestration paths.
- `data/pick_order_exclusions.py`: validation and immutable hashes for the
  match-level development exclusion registry.
- `data/pick_order_review_artifacts.py`: crop/contact-sheet persistence and
  capture report serialization; full decoded frames remain in memory.
- `data/pick_order_consistency.py`: fixed holdout selection, exact-order metrics,
  blind-label audit, official-VOD audit, and fail-closed acceptance thresholds.
- `data/pick_order_gallery_release.py`: immutable hashes for the authorized private
  gallery; holdout crops cannot be added before scoring.
- `data/pick_order_media_rights.py`: local execution guard for authorized sources;
  it records evidence but does not make a legal determination.
- `scripts/capture_complete_drafts.py`: thin CLI and per-game orchestration for
  crop-only review bundles; no full frame is retained unless explicitly requested.
- `scripts/prepare_pick_order_holdout.py`: metadata-only, deterministic 30-game
  selection for each layout, excluding development matches.
- `scripts/verify_pick_order_holdout_vods.py`: approved-uploader and game-title
  metadata verification without downloading media.
- `scripts/prepare_blind_pick_order_review.py`: timestamped unlabeled contact sheets.
- `scripts/evaluate_pick_order_holdout.py`: separate accuracy and rollout gates.
- `scripts/collect_pick_order_data.py`: headless orchestration and isolated snapshots.
- `scripts/inspect_draft_ocr.py`: experimental diagnostic; no annotation promotion.

Review uses headless JSON and generated contact sheets. Browser review tooling is not part
of the collection pipeline.

## Current consistency-gate state — 27 September 2026

- The previous 60-game selection with ID
  `cca1124bb1c797759b19db1b7267e34dba730fb89846a2fbff16e3e6fa6723f5`
  is invalid because it includes both smoke-test matches. It must not be scored.
- The committed exclusion registry contains the ten pilot games and two smoke games,
  representing six development games per layout. A replacement holdout will be fixed
  only after the 15-game-per-layout development sets and gallery are frozen.
- Replacement holdout selection, labels, and suggestions: **not created**.
- Accuracy gate: **failed/pending**; weekly ready: **false**.
- New live VOD processing during this implementation: **four development-game v15
  smoke runs**, authorized by the private local rights record.
- The existing 27-image local gallery remains private and frozen. No smoke crop was
  added to it, and neither the rights record nor gallery archive is committed.

The active extractor is `complete_draft_v15`. It requires a verified official per-game
upload, validated layout, ten stable lock events, time-separated identity observations,
and a first settled pre-swap frame. The calibrated M7 and MPL Season 18 layouts declare
their card slots as `pre_swap_pick_order`; lock timestamps prove transitions but animation
ties do not override those validated slot semantics. Uncalibrated timestamp ties,
enlarged-card geometry, hovers, swaps, unknown layouts, wrong uploaders, or weak game
matches produce incomplete review results. The evaluator rejects manual windows,
same-game references, and legacy extractors.

## Consistency correction — 23 September 2026

The capture output now reports identity and chronology as separate gates:
`identity_complete`, `slot_order_validated`, and `order_complete`. Accepted complete
orders remain in `picks` for compatibility; partial or provisional identities are exposed
only through `proposed_picks`. Unresolved slots include their top two candidates, local
margin, and team-assignment margin. M7 identity matching is now a five-by-five bijection
over Liquipedia's final team set, using lighting/alignment-normalized observations sampled
at separated source times. Liquipedia-set elimination is permitted only when exactly one
identity is unresolved across the entire game and its lock is independently verified.

This change has regression coverage but has not promoted labels, refreshed raw data,
retrained models, or run the 60-game holdout. Weekly rollout remains disabled until each
30-game layout set has at least 18 complete suggestions and zero incorrect complete
orders, with the private rights, gallery, VOD, and label-audit controls present.

### Automatic smoke — 27 September 2026

Four official per-game VODs were processed through the automatic-window, crop-only v15
route, with at most two concurrent remote streams and the frozen private gallery. M7's
layout freezes its first stable placeholder-to-hero lock: both M7 games detected later
slot movement and failed closed with `slot_movement_after_lock`, no selected frame, no
crop evidence, and no retry. MPL Season 18 keeps its calibrated settled-card behavior so
enlarged-card animation artwork cannot become a false first lock.

Both MPL games produced normal-size settled contact sheets whose screen slots matched the
previously reviewed pick order. `CGCbVSueZ7c` produced a complete 10-pick suggestion that
exactly matched the prior reviewer-accepted order. `SuqcHmHNTos` abstained because OCR and
lock evidence were incomplete. The two MPL review bundles stayed below 0.2 MB; the two
M7 terminal reports were about 1.2 KB each. No run retained a VOD or full frame, changed a
gallery reference, promoted an annotation, rebuilt a dataset, or trained a model.

These are development smokes, not gate evidence or confirmed labels. All four games remain
excluded from a fresh blind 30+30 selection. The result demonstrates safe abstention and
one exact complete order; it does not satisfy the eventual coverage gate.

### Private control files

The rights record is intentionally not generated by the repository. It is a private JSON
document with `rights_confirmed: true`, a specific `basis`, the immutable
`allowed_channel_ids`, and explicit booleans for `extract_review_crops` and
`freeze_private_gallery`. The guard rejects generic private-use, deletion, or
noncommercial claims. The record documents an external authorization; it cannot create
one.

The label audit is version 1 with one row per holdout `game_id`. Every row records
`blind`, `first_pass_at`, and `first_order`. Required delayed checks also contain
`second_pass_at` and `second_order`; disagreements need `resolved_order`. Timestamps must
include an offset and be at least 48 hours apart. The seeded clear-game sample is derived
from the immutable selection ID, so it cannot be cherry-picked after seeing scores.

The live VOD verification file is also version 1 with one row per game. Fixture results
are useful for tests but cannot satisfy the gate. Manual verification may be used only
when it records the official `channel_id`, `video_id`, reviewer, and concrete verification
evidence. These control files and the gallery archive are private runtime artifacts, not
curated repository data.

## Historical extraction — 13 September 2026

The complete-draft pilot used five official per-game M7 VODs and five official per-game
MPL Indonesia Season 18 VODs. Each bounded stream produced the first stable frame with all
ten final portraits and a later stable player/role-positioned arrangement. The extractor
matched those observations globally within each team, attached Liquipedia hero identities,
and retained only ten review crops per game.

- Bounded VODs processed: **10 / 10**, across **2 tournaments**.
- Complete crop-evidenced pick-order suggestions: **10 / 10**.
- Suggestions matching both final Liquipedia hero sets exactly: **10 / 10**.
- Full VODs or frame sequences retained: **0**.
- Human-confirmed pick orders: **0 / 10**.

The untracked pilot report is at
`backend/data/raw/pick_order_suggestions/complete_draft_pilot/role_remap_v7/report.json`;
its sibling `evidence/` directory contains exactly 100 labeled crops. Every row is still
`needs_review`. This is a successful extraction run, not a human-confirmed accuracy
benchmark, and none of its proposals were promoted into curated annotations.

Manual review on 15 September found many wrong labels and orders, especially on
red-side enlarged cards. The mechanical 10/10 final-set check did not measure
chronological accuracy. Do not promote the `role_remap_v7` rows.

## Corrective pilot — 15 September 2026

The active capture command now waits for normal card separators before selecting
the first complete pre-swap frame. It then recognizes MPL hero-name strips with
local OCR or M7 portraits against canonical/confirmed broadcast artwork; no
Liquipedia role-slot mapping is used. A verified complete frame plus four unique
high-confidence team identities may infer the one remaining hero from the LP set.
Every inferred pick records `liquipedia_set_elimination` as its identity source.

- Official bounded per-game VODs processed: **10 / 10**, across **2 tournaments**.
- Complete review suggestions: **8 / 10** — MPL Season 18 **5 / 5**, M7 **3 / 5**.
- Unresolved M7 suggestions: **2 / 5** with partial per-slot evidence, not guessed orders.
- Crop evidence retained: **100** small portraits; full frames and VODs retained: **0**.
- Human-confirmed new orders and annotation promotions: **0**.

The isolated untracked output is
`backend/data/raw/pick_order_suggestions/complete_draft_pilot/settled_direct_v9/report.json`
with its sibling `evidence/` folder. All ten rows remain `needs_review`.
The local `backend/data/raw/hero_reference_gallery/` contains 19 crops drawn only
from slots the reviewer explicitly marked correct; no unreviewed label was added.
The two M7 gaps are `-YSkw01WzNI` and `72gsCs5Toho`. This is still a
reference-assisted pilot on known games, not an unseen-game accuracy benchmark.

## Reviewer-labeled M7 rerun — 16 September 2026

The reviewer identified all eight previously unresolved portraits in those two
games. Only those confirmed crops were added with `add_hero_skin_reference.py`,
bringing the local ignored gallery to 27 images. A read-only Uranus comparison
reproduced the miss: the reviewer-confirmed crop scored 0.7029 against existing
references, below the 0.72 acceptance threshold despite Uranus being the clear
best candidate. After adding that confirmed crop, the same check scored 1.0.

Each bounded game window was recaptured into a separate review-only folder:
`complete_draft_pilot/reviewer_labels_v9_ys/` and
`complete_draft_pilot/reviewer_labels_v9_72/`. Both now have a complete order
suggestion (`0.9797` and `0.9447` confidence); the original 8/10 pilot report
was not overwritten. Together, the known-game pilot now has **10/10 complete
review suggestions**, still **0 newly promoted annotations**. The reruns kept
20 small crops and no full frames or VODs. Same-game reference seeding is not
an unseen-game accuracy test.

## Remaining correctness risks

1. **Reference-assisted coverage:** all ten runs use visually selected portrait-pool
   references and prelocated 60-second windows. New tournaments still need layout and
   empty-state calibration before unattended discovery-to-order operation.
2. **M7 identity coverage:** the reviewer-confirmed gallery fills the two known
   pilot gaps, but alternate skins and portrait shifts on unseen games remain
   unmeasured. Never turn low-score guesses into reference images.
3. **Similarity and OCR calibration:** ten complete suggestions are not ten
   confirmed correct orders. Hero-name OCR is layout-specific, the completion-only
   global pool threshold is deliberately looser for an enlarged MPL reference,
   and every order still requires human review.
4. **Stable identifiers:** LP IDs are now retained, but legacy annotation `game_id`
   still includes file/series indices. Migration needs explicit compatibility handling;
   do not merge refreshed raw data blindly into old confirmed annotations.
5. **Large retrievals:** the pilot fails closed on a 1,000-row response rather than
   silently treating a possibly truncated response as complete. Pagination remains
   necessary for large tournament/date queries.
6. **Media reliability:** existing yt-dlp/FFmpeg section downloads can stall; their
   process-level timeout and disk-quota enforcement need further hardening. Metadata
   lookup success is not a substitute for bounded media tests.

## Next acceptance gate

Human-review all ten complete suggestions against the original pre-swap draft
sequences, including the two reviewer-labeled reruns. Only promote explicitly
confirmed orders into annotation v1. Expand with negative layouts,
hover-heavy drafts, alternate overlays, and unseen VODs before treating these
thresholds as production calibration. Keep model training and confirmed-label
promotion separate.
