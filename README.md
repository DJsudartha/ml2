# ML²

An MLBB draft simulator with data-driven ban and pick recommendations.

The repository currently contains:

- a Vite + React frontend for the draft UI
- a FastAPI backend for recommendation endpoints
- modeling and data-processing scripts for training and refreshing draft data
- a shared test suite focused on backend recommendation logic
- a v1 pick-order annotation pipeline for reviewed VOD-assisted draft labels

## Project Structure

- `frontend/` - Vite + React application deployed to GitHub Pages
- `backend/` - FastAPI app, modeling services, and data-processing scripts
- `tests/` - repository-level Python tests for recommendation and advisor behavior
- `.github/` - CI, Pages deployment, issue forms, and pull request automation

## Local Setup

### Prerequisites

- Node.js 20+
- Python 3.12+

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

The Pages build uses the Vite base path `/ml2/`.
The frontend defaults to the local API at `http://127.0.0.1:8000`. Set
`VITE_API_BASE_URL` before starting or building the frontend only when an explicit
alternative backend is available.

### Backend

From the repository root:

```bash
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The backend exposes draft recommendation routes under `/draft`, including:

- `POST /draft/recommend-bans`
- `POST /draft/advise-bans`
- `POST /draft/recommend-picks`
- `POST /draft/advise-picks`

### Environment Variables

`backend/.env.example` documents the Liquipedia API variables used by scraping and refresh scripts:

- `LIQUIPEDIA_API_KEY`
- `LIQUIPEDIA_API_KEY2`
- `YOUTUBE_API_KEY` for read-only official-channel VOD discovery

Keep real secrets in `backend/.env`. Do not commit `.env` files, local virtual environments, or generated caches.

## Pick-Order Data Collection

### Consistency gate and media authorization

The weekly pick-order workflow is currently **disabled**. The ten-game pilot was a
development set with reference-assisted coverage, not an unseen-game accuracy result.
The replacement gate fixes 30 M7 Knockout and 30 MPL Indonesia Season 18 games from
different Liquipedia matches. It accepts a layout only when at least 18 of 30 games
have complete suggestions and no complete order is wrong.

New YouTube metadata checks, frame processing, review-crop retention, and gallery
archiving require a private `--media-rights-file` that records an actual authorization
basis and covered channel IDs. Claims such as private, noncommercial, or delete-after-use
are deliberately rejected. The repository does not include this file. Without it, use
only the metadata selector, existing-data evaluator, and synthetic tests.

Fix the match-disjoint selection using local Liquipedia snapshots:

```powershell
& $vodPython backend/scripts/prepare_pick_order_holdout.py `
  --raw-dir backend/data/raw/pick_order_suggestions/complete_draft_pilot/raw `
  --development-report backend/data/raw/pick_order_suggestions/complete_draft_pilot/manifest.json `
  --output backend/data/raw/pick_order_suggestions/consistency_holdout/selection.json
```

Once usage rights are confirmed, freeze the authorized private gallery before any
holdout run. Its content hash prevents holdout crops from leaking into references:

```powershell
& $vodPython backend/scripts/release_pick_order_gallery.py `
  --gallery-dir backend/data/raw/hero_reference_gallery `
  --archive private/pick_order_gallery.zip `
  --manifest-output private/pick_order_gallery.json `
  --media-rights-file private/media_rights.json
```

Verify selected uploads, run capture without `--start-sec` or `--references`, and
prepare unlabeled review artifacts:

```powershell
& $vodPython backend/scripts/verify_pick_order_holdout_vods.py `
  --holdout backend/data/raw/pick_order_suggestions/consistency_holdout/selection.json `
  --raw-dir backend/data/raw/pick_order_suggestions/complete_draft_pilot/raw `
  --output backend/data/raw/pick_order_suggestions/consistency_holdout/vods.json

& $vodPython backend/scripts/capture_complete_drafts.py `
  --manifest backend/data/raw/pick_order_suggestions/consistency_holdout/selection.json `
  --profiles backend/data/complete_draft_profiles.json `
  --raw-dir backend/data/raw/pick_order_suggestions/complete_draft_pilot/raw `
  --output-dir backend/data/raw/pick_order_suggestions/consistency_holdout/capture `
  --gallery-manifest private/pick_order_gallery.json `
  --media-rights-file private/media_rights.json

& $vodPython backend/scripts/prepare_blind_pick_order_review.py `
  --holdout backend/data/raw/pick_order_suggestions/consistency_holdout/selection.json `
  --suggestions backend/data/raw/pick_order_suggestions/consistency_holdout/capture/report.json `
  --output-dir backend/data/raw/pick_order_suggestions/consistency_holdout/blind_review `
  --media-rights-file private/media_rights.json
```

Blind labels stay in confirmed annotation v1. The separate audit must show that all
inferred, ambiguous, incomplete, or disagreeing games and a deterministic 20% of clear
games were rechecked at least 48 hours later. Evaluate without promoting anything:

```powershell
& $vodPython backend/scripts/evaluate_pick_order_holdout.py `
  --holdout backend/data/raw/pick_order_suggestions/consistency_holdout/selection.json `
  --gold private/holdout_gold.json `
  --suggestions backend/data/raw/pick_order_suggestions/consistency_holdout/capture/report.json `
  --audit private/holdout_audit.json `
  --vod-verification backend/data/raw/pick_order_suggestions/consistency_holdout/vods.json `
  --gallery-manifest private/pick_order_gallery.json `
  --media-rights-file private/media_rights.json `
  --output backend/data/raw/pick_order_suggestions/consistency_holdout/gate.json
```

`gate_passed` is the accuracy/coverage result. `weekly_ready` additionally requires the
rights record, frozen gallery, official VOD verification, and label audit. Neither field
confirms annotations or triggers model training.

### Infer pick order from the complete draft

`capture_complete_drafts.py` reads a bounded official VOD stream sequentially at its
source frame rate. It selects the first verified ten-hero frame after enlarged pick
cards retract and before any slot swap. MPL Indonesia Season 18 uses calibrated
player-card separators to detect normal card geometry. A same-VOD team-wide
comparison verifies the ten-portrait pool even if the early reference contains
an enlarged card; that comparison does not assign hero names.

The active identity path uses Liquipedia's final five heroes per team as a
candidate set, not its nonchronological `slot` field. MPL Season 18 reads vertical
broadcast hero names using local OCR. M7 uses canonical icons and reviewer-confirmed
broadcast artwork in a local gallery. M7 scores time-separated observations after
alignment and lighting normalization, then solves one one-to-one assignment across
all five team slots. Absolute, slot-margin, and team-assignment gates reject weak or
ambiguous identities. A complete proposal also requires a stable placeholder-to-final
lock event for every slot and agreement with the first settled pre-swap frame. For the
calibrated M7 and MPL Season 18 profiles, those slot positions define pick order;
animation-overlapped lock timestamps are diagnostic evidence rather than a second order
source. One unread hero can be inferred by elimination only when that slot and lock
event are independently verified.
The old profile `role_slot_map` remains for legacy comparisons but is not used by
the active capture command.

Frames are processed in memory. The default `--evidence-mode crops` writes small,
timestamped lock/settled portrait crops, a labeled ten-crop contact sheet, and
`report.json`; it does not retain a complete frame, frame sequence, or VOD. Review JSON
separates `identity_complete`, `slot_order_validated`, and `order_complete`. Partial
identities appear in `proposed_picks` with top-two candidate diagnostics, while `picks`
remains populated only for a fully accepted order. Use `--evidence-mode frame` only for
a deliberate full-frame review artifact, or `--evidence-mode none` for metadata-only
diagnostics. Identical completed jobs are reused.

```powershell
& $vodPython backend/scripts/capture_complete_drafts.py --manifest path/to/manifest.json --profiles backend/data/complete_draft_profiles.json --raw-dir path/to/raw --vod-verification path/to/vods.json --gallery-manifest private/pick_order_gallery.json --media-rights-file private/media_rights.json --evidence-mode crops --output-dir path/to/review-output
```

`--vod-verification` is optional when the raw Liquipedia row is available; when supplied,
it must be a version 1 audit with a valid live or documented-manual per-game record for
every selected game. A fixed manifest may supply the two final five-hero sets if its raw
tournament snapshot is no longer present. When running from an isolated worktree, pass
`--profile-assets-root path/to/the/main/checkout` to resolve ignored calibration images
without copying them into Git.

Install `backend/requirements-ocr.txt` for MPL's broadcast-name mode. Without
local OCR, these games remain unresolved instead of using an unsafe role map.
The pilot profiles cover M7 and MPL Indonesia Season 18. Each references a local
all-placeholder calibration image. On a fresh checkout, obtain calibration images
with `--probe-sec 30` (omit `--profiles`), inspect them, and update the profile's
`placeholder_frame` path. Calibration probes are not completion results. Season 18
replaces its center logo with draft statistics, so the profile uses the persistent
headset and sponsor elements instead.

The old pilot is **reference-assisted**, not a benchmark on unseen games. `--references`
accepts a JSON object keyed by video ID, with `frame` (a visually verified image containing
all ten hero portraits) and `scan_start_sec` (an earlier incomplete draft). A reference may
be post-swap because it is used only as an unordered team portrait pool. Once completion is
found, settled observations come directly from the stream and hero identities are
checked separately. Reference-assisted or manually windowed output is rejected by the
holdout evaluator. Reference and calibration images are local pilot inputs.

Use `--source-id mlbb_esports` or `--source-id mpl_indonesia` to process broadcasters
independently with different output directories. `--start-sec` and `--duration-sec`
can refine an already located draft window; the window must include an incomplete
draft before completion. Unknown layouts, an already-complete opening, missing settled
pre-swap frames, and ambiguous hero identities fail closed. Network reads time out and
retry twice from the start of the bounded window. Near-complete interrupted scans
also receive two bounded retries. FFmpeg source timestamps preserve
timing across variable frame rates. Every result remains `needs_review`; human confirmation
is required before copying it into confirmed annotation v1 data.

### Headless Liquipedia-first collection (recommended)

Game-level Liquipedia VOD links are now preserved during ingestion and take precedence
over YouTube title discovery. Existing raw files must be refreshed to acquire fields
that older ingestion discarded. Use an isolated output directory; the following command
does not overwrite curated tournament files, rebuild datasets, or train models:

```powershell
& $vodPython backend/scripts/collect_pick_order_data.py --tournament M7_World_Championship/Knockout_Stage --tournament MPL/Indonesia/Season_17/Regular_Season --matches-per-tournament 5 --verify-media
```

The command saves API snapshots, normalized game data, all-source and selected manifests,
and `report.json` under `backend/data/raw/pick_order_suggestions/lp_collection/`.
It selects one eligible game from each distinct Liquipedia match, not five games from
one series. Snapshots are reused on repeated runs; `--refresh` explicitly refreshes them.
`--verify-media` checks metadata and approved uploader IDs using yt-dlp, without video
downloads or YouTube Data API calls. This does not prove every video frame decodes.
YouTube API discovery is now opt-in using `--youtube-fallback` in the collection,
discovery, and weekly commands. Series-only links and duplicate timestamps require review.

**Retrieval is not pick-order completion.** The report counts retrieved matches,
verified VODs, complete evidenced suggestions, and human-confirmed orders separately.
`--process` invokes the existing bounded-video pipeline; `--require-orders` returns a
failure exit code until every selected game has a complete evidenced suggestion. MPL's
production layout is still gated on calibration. The weekly command processes local raw
data; it does not itself refresh Liquipedia snapshots. No review page is required.

The complete-draft command now uses OCR directly for supported MPL hero-name strips.
`backend/scripts/inspect_draft_ocr.py --help` remains a separate diagnostic for
existing frame caches; its JSON does not prove a lock or a pre-swap order. See
[pipeline notes](docs/pick-order-pipeline.md) for the current acceptance results and limits.

Liquipedia match data currently provides each team's final five heroes by role slot, not the actual pick order. The model can use those role slots for order-agnostic pick-fit training, but true draft-order training requires a separate reviewed label layer.

V1 stores curated pick-order labels in:

```bash
backend/data/raw/pick_order_annotations/pick_order_annotations.json
```

Recommended workflow from the repository root:

```bash
python backend/scripts/export_pick_order_annotation_template.py --output backend/data/raw/pick_order_annotations/review.template.json
```

Fill or review the generated template, then copy confirmed entries into the curated annotation file. Validate before using the labels:

```bash
python backend/scripts/validate_pick_order_annotations.py
```

VOD-assisted suggestions use versioned, normalized layout crops from
`backend/data/layouts.json` and a manifest shaped like
`backend/data/raw/vod_manifest.example.json`. Official per-game uploads are preferred,
followed by per-series uploads and full-day broadcasts. Approved immutable channel IDs
live in `backend/data/vod_sources.json`.

Install optional VOD dependencies only when needed:

```bash
python -m pip install -r backend/requirements-vod.txt
```

Discover recent official videos and build an auditable manifest:

Discovery follows uploads pagination to the requested date cutoff and also checks
configured tournament playlists, including the official M7 per-game collection.
Playlist videos must still belong to the approved channel. Unsupported tournaments
are omitted; `missing_vod` means no eligible match, not that a video does not exist.
Short clips (under ten minutes) and titles marked as highlights are excluded.
Team aliases, game number, stage, and a 36-hour publication/start-time window guard
automatic matching; delayed uploads may require manual matching.

```bash
python backend/scripts/discover_pick_order_vods.py --since-days 8 --youtube-fallback --output backend/data/raw/pick_order_suggestions/latest_manifest.json
```

For the initial M7 backfill, use `--since-days 365` instead of `8`.

Process matched entries with at most two remote streams and four recognition workers:

```bash
python backend/scripts/run_pick_order_vod_pipeline.py --manifest backend/data/raw/pick_order_suggestions/latest_manifest.json
```

The combined weekly command remains fail-closed until the full gate reports
`weekly_ready: true`. After that, a local scheduler may run it every Monday at 03:00
Asia/Makassar with the same private rights and frozen-gallery inputs:

```bash
python backend/scripts/run_weekly_pick_order_collection.py --gate-report path/to/gate.json --media-rights-file private/media_rights.json --gallery-manifest private/pick_order_gallery.json
```

The weekly command uses isolated per-game capture jobs and a deterministic merge. It
only produces review suggestions; it never confirms annotations, rebuilds datasets,
retrains models, or changes the active recommendation service.

Generate a single review suggestion from pre-extracted frames when debugging:

```bash
python backend/scripts/suggest_pick_order_from_vod.py --manifest backend/data/raw/vod_manifest.example.json --game-id "M7_World_Championship_Knockout_Stage_games.json::series1::game1::1" --frames-dir backend/data/raw/frames/example
```

If `--frames-dir` is omitted, the script downloads only the bounded draft section to a
temporary directory, extracts frames, and deletes the media section. It never retains a
complete VOD. VOD suggestions are always `needs_review`; only human-reviewed annotations
should be marked `confirmed`.

`m7_world_v1` is calibrated. `mpl_id_v1` is registered but intentionally remains
`needs_calibration` until a real MPL Indonesia draft frame is reviewed. Activate it by
calibrating all pick/ban slots and at least two static anchors:

```bash
python backend/scripts/calibrate_layout.py --frame path/to/mpl-id-draft.jpg --layout-id mpl_id_v1 --family mpl_id --anchor top_brand --anchor center_timer
```

Alternate hero skins used by the M7 recognizer belong in the local gallery
under `backend/data/raw/hero_reference_gallery/<Hero Name>/`; they do not require another
layout. Seed the gallery only from a reviewer-confirmed crop; unreviewed
suggestions must not become reference images automatically.

After a reviewer confirms an unresolved crop, add it without renaming the hero:

```bash
python backend/scripts/add_hero_skin_reference.py --hero "Yi Sun-shin" --image path/to/confirmed-crop.jpg
```

The command also accepts a newly introduced hero with no frontend icon when its exact name
appears in the supplied `--raw-dir` Liquipedia data.

Runtime ledgers, temporary media, extracted frames, evidence, and suggestions remain
untracked. Only deliberately reviewed annotations should be committed.

The supported review flow is headless: `capture_complete_drafts.py` writes compact crop
evidence, `prepare_blind_pick_order_review.py` creates contact sheets, and
`evaluate_pick_order_holdout.py` checks reviewed labels. There is no browser review page.
Commit curated annotations and manifest examples only; do not commit downloaded VODs,
extracted frames, local suggestion outputs, contact sheets, or generated review templates.

## Development Workflow

This repository uses a trunk-based workflow:

- branch from `main`
- use short-lived branches such as `feat/*`, `fix/*`, `chore/*`, `docs/*`, or `exp/*`
- open a pull request for every change
- merge back into `main` with squash merge

Recommended commit prefixes:

- `feat:`
- `fix:`
- `docs:`
- `chore:`
- `refactor:`
- `test:`

More detail lives in `CONTRIBUTING.md`.

## Quality Checks

Run these before opening a pull request:

```bash
cd frontend
npm run lint
npx tsc -p tsconfig.app.json --noEmit
npm run test
npm run build
```

```bash
python -m pytest -q
ruff check backend tests
```

Run the same backend checks after changing data scripts, annotation validation, or modeling dataset builders.

## Deployment

- Frontend deploys to GitHub Pages from `main` via `.github/workflows/deploy-to-pages.yml`
- The backend currently runs locally and is not part of the Pages deployment workflow

## Notes for Collaborators

- Start `backend.main:app` locally before using draft recommendations.
- Override `VITE_API_BASE_URL` only when a replacement backend is intentionally available.
