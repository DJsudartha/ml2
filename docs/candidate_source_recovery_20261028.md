# Candidate metadata source recovery - 2026-10-28

All three requested candidate games now exact-join a newly retained Liquipedia API response. The response supplies match/game identities, calendar strings, patch claims, team identities and sides, complete roster sets and explicit bans 1-5 per team. This is metadata recovery for unreviewed proposals. No review, finalization, training eligibility, model fit or production change occurred.

Branch `feat/candidate-source-recovery` starts at fetched `origin/main` commit `0861dc40473248d1d22ef0e975c411b6527298d9`, in an isolated worktree. Manager integrity checks found the original workspace Git status unchanged and six monitored files byte-identical: `AGENTS.md`, `.codex/config.toml`, the old readiness manifest, its candidate-only source, its builder and the old importer. This is a scoped integrity check, not a hash of every repository file. Its existing report records 17 finalized development games and eight provisional candidates. No development label counts change here.

## Per-game evidence

All pointers below refer to the original API response. Each exact allowed game is also retained as an original-byte JSON file under [the scoped supplement](../experimental/candidate_source_recovery/). Opponent identity comes from `match2opponents[id=n].name`; side comes from game `extradata.team{n}side`. Champion slots are compared as complete sets, never interpreted as pick chronology. Bans come directly from `team{n}ban1` through `team{n}ban5`, without loader fallback enumeration.

### MPLIDS18W5_0004 G2

- Stable internal identity: `MPL_Indonesia_Season_18_Regular_Season_games.json::series59::game1::2`. Publisher game number: `2`; API `match2gameid` and `subgroup` agree. Pointer: `/result/0/match2games/1`.
- Recovered source calendar string: `2026-09-12 13:00:00`. Source patch claim: `2.1.95`. Game and parent match values agree.
- Blue: **EVOS**; red: **Natus Vincere**. Exact side/identity and complete roster sets agree with the pinned candidate baseline.
- Blue bans 1-3 before B1: Belerick, Atlas, Marcel. Bans 4-5 after R3: Uranus, Minotaur.
- Red bans 1-3 before B1: Freya, Melissa, Gloo. Bans 4-5 after R3: Barats, Miya.
- Original game-byte SHA-256: `8e5f39cce15d7fcec0067bdcac2946a8364c272041612f382caa055c0ca3087b`. Scoped file: `MPLIDS18W5_0004_G2.json`.
- Still unknown: actual start instant, interpretation of source timezone, independently certified tournament-server patch deployment, and historical hero eligibility. No missing slot or inferred ban ordering is filled.

### MPLIDS18W6_0003 G1

- Stable internal identity: `MPL_Indonesia_Season_18_Regular_Season_games.json::series68::game1::1`. Publisher game number: `1`; API `match2gameid` and `subgroup` agree. Pointer: `/result/1/match2games/0`.
- Recovered source calendar string: `2026-09-18 13:15:00`. Source patch claim: `2.1.95`. Game and parent match values agree.
- Blue: **Dewa United Esports**; red: **RRQ Hoshi**. Exact side/identity and complete roster sets agree with the pinned candidate baseline.
- Blue bans 1-3 before B1: Fanny, Atlas, Minotaur. Bans 4-5 after R3: Esmeralda, Gatotkaca.
- Red bans 1-3 before B1: Freya, Arlott, Yi Sun-shin. Bans 4-5 after R3: Rafaela, Marcel.
- Original game-byte SHA-256: `76c91c1d790889f7bfba9efb98c76c85e448e00590a5b1dce24f026096e0da80`. Scoped file: `MPLIDS18W6_0003_G1.json`.
- Still unknown: actual start instant, interpretation of source timezone, independently certified tournament-server patch deployment, and historical hero eligibility. No missing slot or inferred ban ordering is filled.

### MPLIDS18W7_0003 G1

- Stable internal identity: `MPL_Indonesia_Season_18_Regular_Season_games.json::series82::game1::1`. Publisher game number: `1`; API `match2gameid` and `subgroup` agree. Pointer: `/result/2/match2games/0`.
- Recovered source calendar string: `2026-10-03 07:00:00`. Source patch claim: `2.1.95`. Game and parent match values agree.
- Blue: **Alter Ego**; red: **Natus Vincere**. Exact side/identity and complete roster sets agree with the pinned candidate baseline.
- Blue bans 1-3 before B1: Paquito, Aulus, Masha. Bans 4-5 after R3: Marcel, Gloo.
- Red bans 1-3 before B1: Carmilla, Freya, Uranus. Bans 4-5 after R3: Badang, Alice.
- Original game-byte SHA-256: `114fd18a7c15994e7dd17ea588fd9dbec1664a3d0b4dbc55b42076e73a8c71b9`. Scoped file: `MPLIDS18W7_0003_G1.json`.
- Still unknown: actual start instant, interpretation of source timezone, independently certified tournament-server patch deployment, and historical hero eligibility. No missing slot or inferred ban ordering is filled.

## Ordered source ban positions

| Game | Side | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- | --- |
| MPLIDS18W5_0004 G2 | blue | Belerick | Atlas | Marcel | Uranus | Minotaur |
| MPLIDS18W5_0004 G2 | red | Freya | Melissa | Gloo | Barats | Miya |
| MPLIDS18W6_0003 G1 | blue | Fanny | Atlas | Minotaur | Esmeralda | Gatotkaca |
| MPLIDS18W6_0003 G1 | red | Freya | Arlott | Yi Sun-shin | Rafaela | Marcel |
| MPLIDS18W7_0003 G1 | blue | Paquito | Aulus | Masha | Marcel | Gloo |
| MPLIDS18W7_0003 G1 | red | Carmilla | Freya | Uranus | Badang | Alice |

Positions 1-3 are the first phase; positions 4-5 are the second phase.

## Provenance and identity checks

One bounded authenticated GET to `https://api.liquipedia.net/api/v3/match` used `wiki=mobilelegends`, `limit=3` and conditions restricted to the three exact match IDs. The response was retrieved at `2026-10-28T13:32:45.460388+00:00`, with HTTP 200, 27,849 bytes and exactly three match records. Explicit game allowlisting preceded decoding of game payloads; other games in those series stayed opaque. No API key is stored here.

The original response remains outside the repository; supply its local path only at replay time with `--original`. Its SHA-256 is `18aece3f90bf6b17344dea6f4f424cbc1921d02618b02e236b789743e850eb66` and its byte length is 27,849. The scoped receipt records exact UTF-8 byte offsets, JSON pointers, game-span hashes and allowed match-metadata spans. Optional replay checks the whole original hash first, then reads only those spans; it never JSON-decodes the whole response.

The prior readiness SHA-256 remains `62d018ca9a5f21739cfccd34299d49d18dd5d2ae1ce5438ad687f5d6ab013b08`. The read-only audit rechecked all 10 referenced-file hashes, candidate identity/team/roster consistency and all 148 packaged asset hashes. Existing/protected match disjointness was checked using lexical identity-only access. No protected roster, label, image, prediction or result payload was decoded. The path-level checks of private source files remain in a local-only receipt backup. The public receipt retains only the source spans, file hashes, and aggregate consistency counts. Hashes identify bytes and do not authenticate the underlying factual claims.

The old 53-series normalized raw snapshot has none of the three exact games. Older retained API snapshots include stale/conflicting later match IDs: W5_0004 names Alter Ego/RRQ, W6_0003 names Geek Fam/Natus Vincere; W7 lacks the required hero evidence. Those records cannot be joined by ID alone. The fresh response agrees on exact requested match/game IDs, both teams and sides, all ten roster heroes, the calendar/patch claims and all ten copied ban values for each target. These independent constraints support the recovered joins despite historical schedule drift. Original old snapshots and copied claims were not rewritten.

## Staged files and limits

`experimental/candidate_source_recovery/supplement.json` contains exactly three provisional metadata records. `scoped-source.json` retains only permitted match metadata; the three separate game JSON files preserve exact original allowed game bytes. `receipt.json` binds every data file, response/span hashes, pointers, source request and field mapping. Its private-source path/hash audit details are retained only in ignored `private/candidate_source_recovery/receipt-original-with-private-checks.json`. `verify.py` checks replay and consistency; `test_verify.py` exercises rejection paths.

The external receipt pin is `bbaa2e318b32b9e25c594f0bca7c72cedb1bece824321bc3a6441dc1939309a7`. Use that pin when validating copied artifacts; a self-reported receipt hash alone is not an independent trust anchor.

This supplement has production/training approval disabled and remains unreviewed. It is deliberately not a replacement importer manifest. The old readiness manifest still reports five of eight source-backed games; this separate supplement recovers the other three, supporting future eight-of-eight metadata coverage only after an explicitly versioned readiness integration. No candidate becomes automatically eligible for training. The current importer and its prior untracked dependencies are absent from latest main; none were copied or changed here.

Canonical chronology is preserved: B1 -> {R1,R2} -> {B2,B3} -> R3; second ban phase; R4 -> {B4,B5} -> R5. Each team's bans 1-3 occur before B1 and bans 4-5 after R3. Double-pick members are unordered. The supplement emits no pick-batch targets or training examples.

## Validation

From the isolated worktree with Python 3.12:

```powershell
python experimental/candidate_source_recovery/verify.py --receipt-sha256 bbaa2e318b32b9e25c594f0bca7c72cedb1bece824321bc3a6441dc1939309a7
$originalResponse = Read-Host 'Path to the local original Liquipedia API response JSON'
python experimental/candidate_source_recovery/verify.py --receipt-sha256 bbaa2e318b32b9e25c594f0bca7c72cedb1bece824321bc3a6441dc1939309a7 --original $originalResponse
python -m pytest -q experimental/candidate_source_recovery/test_verify.py -p no:cacheprovider
python -m ruff check experimental/candidate_source_recovery
```

Result: **22 focused tests passed (0.16s)**; scoped Ruff passed. Original-byte replay passed for all three games and all selected match metadata spans, verifying 30 explicit source bans. Cases cover receipt/data tampering, identity/subgroup conflicts, duplicated sides, missing/duplicate bans, date/patch mismatch, wrong tournament, candidate roster/side drift, disabled approval boundaries, roster permutation invariance, preservation of ban positions, and rejection of machine-specific home paths in public receipts. These checks validate this supplement, not the old importer or the full application. No full application suite was run because application code is unchanged.

## Economic stopping point

The requested source fields are recovered for all three games. Further VOD, OCR or human-review research is unnecessary for this metadata task. The remaining uncertainties need different evidence: timezone/actual-start documentation, organizer-certified patch deployment and effective hero restrictions. Another broad source search would not justify silently filling them. Future human finalization and versioned readiness integration remain separate authorized work; no merge, push or deployment occurred.

## Source attribution

The scoped match metadata and game records are attributed to [Liquipedia](https://liquipedia.net/). Liquipedia states that its content is licensed under [CC BY-SA 3.0](https://liquipedia.net/api-terms-of-use). The original authenticated API response remains local-only; no API credential is included in this supplement.
