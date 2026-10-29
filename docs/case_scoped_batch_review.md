# Case-scoped candidate review

The original candidate-only UI exports every packaged game, including unreviewed
ones. The strict development selector requires exactly the externally finalized
candidate allowlist and rejects extra candidate cases. Selecting a game in the
original navigation does not restrict its export.

`backend/scripts/build_case_scoped_batch_review.py` creates a **new** UI package
with an explicit fixed review scope. It accepts the preserved package as an
external input; no private generated UI or experimental importer is required in
the repository. It supports the existing candidate-only UI structure and fails
if required code markers change. It does not regenerate proposals or use VOD/OCR,
credentials, model artifacts or network access.

From the repository root, using Python 3.12+:

```powershell
$reviewPython = 'C:/Users/djsud/AppData/Local/Programs/Python/Python312/python.exe'
& $reviewPython -m backend.scripts.build_case_scoped_batch_review `
  --package 'C:/Users/djsud/OneDrive/Documents/GitHub/mlbb-draft-website/private/candidate_expansion_20261022/review_package' `
  --output 'private/case_scoped_review_20261030' `
  --case 'MPL_Indonesia_Season_18_Regular_Season_games.json::series8::game1::1' 'MPLIDS18W1_0005' 1 `
  --case 'MPL_Indonesia_Season_18_Regular_Season_games.json::series9::game1::2' 'MPLIDS18W1_0006' 2
```

The command requires at least one exact `(game_id, match_id, game_no)` triple.
Missing, invalid, repeated or ambiguous identities fail before output creation.
Every source identity must be unique by ID and by match/game pair. Nonempty
holdouts are rejected without decoding their payloads. A lexical JSON validator
checks syntax and duplicate keys, decodes only object keys and identity/hash
scalars, and compares embedded/frozen manifests without materializing game or
batch payloads. All requested identities must match before a copy is created.

Existing output paths and nested source/output paths are refused. Supported
files are copied explicitly: frozen candidate/empty-holdout manifests, evidence
assets and the modified index, plus a new explanatory README. Source files and
existing exports are never edited. `human_reviews`, `verified_labels`, arbitrary
files and existing package README claims are not copied. Linked evidence files
are rejected. The frozen manifests, full embedded candidate data, hashes,
proposals and evidence paths remain unchanged; this scopes the UI and export,
not the raw package contents. The full package therefore retains all original
candidate metadata for integrity checking.

Open the new `index.html`. Its visible navigation count and exported cases match
the requested scope. The export keeps schema version 1, original proposal arrays,
all answer fields and provenance hashes, and `independently_verified:false`.
Imports must contain exactly that scope, with matching case identities. Missing,
extra or duplicate cases fail before saved answers are replaced. Export also
checks the exact scope. Browser storage has a separate scope-specific key.

The new UI starts with fresh progress. An existing **exact-scope** draft export
can be imported manually, retaining its reviewer and answer fields. An old
eight-case export cannot be imported into a two-case UI. This tool does not
silently rewrite submissions, merge drafts or migrate old browser storage;
preserve those originals and perform the selected review in the scoped UI.

Selection is not approval. This remains proposal-assisted development review.
An export alone never establishes independently verified labels. Unreviewed or
uncertain rows remain unresolved and cannot become complete-game training
labels. External human finalization remains separate: exact export hash,
finalized case IDs, actual reviewer/finalizer identity and time, source inspection
and exposure attestation. Existing strict validation and downstream ingestion
gates still apply. The builder writes no human review, finalization or label file.

## Synthetic verification

Python and Node.js are required. Normal tests use eight invented games and the
original UI code with its embedded real package removed; they are independent of
private packages and untracked experimental modules. The Node VM exercises actual
generated navigation, export-button and import handlers with a minimal DOM;
this does not establish full browser rendering behavior.

```powershell
& $reviewPython -m pytest -q tests/test_case_scoped_batch_review.py
& $reviewPython -m ruff check backend/scripts/build_case_scoped_batch_review.py tests/test_case_scoped_batch_review.py
```

One optional integration test uses the existing strict importer as an external
read-only oracle. Its root must contain the owner's
`backend/scripts/experimental_development_review_source.py` and
`experimental_batch_review.py`. It imports no training adapter or other pipeline
module and opens only freshly invented synthetic review data. The test creates
its synthetic finalization in memory; no real finalization or review is created.

```powershell
$env:ML2_REVIEW_ORACLE_ROOT = 'C:/Users/djsud/OneDrive/Documents/GitHub/mlbb-draft-website'
& $reviewPython -m pytest -q tests/test_case_scoped_batch_review.py
Remove-Item Env:ML2_REVIEW_ORACLE_ROOT
```

The original eight-case behavior is reproduced on synthetic data. The scoped
export passes the real `read_development_review` contract for exactly two
synthetic cases; the original export fails with extra unfinalized candidates.
Tests also check one-case scopes, zero/missing/duplicate/invalid/ambiguous
selection, unexpected imports, unchanged sources, provenance, evidence bytes,
answer fields, unsupported UI structures and output protection. These tests do
not measure reconstruction accuracy or approve any real candidate.
