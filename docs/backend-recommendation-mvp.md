# Backend recommendation MVP

This isolated worktree exposes the contextual Draft Lab scoring formula through the existing FastAPI `/draft/recommend-picks`, `/draft/recommend-bans`, `/draft/advise-picks`, and `/draft/advise-bans` routes. It does not load or train an XGBoost model for these routes. The original direct legacy recommender modules remain available for their existing callers.

## Launch on Windows

From this worktree root:

```powershell
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The existing simulator can target `http://127.0.0.1:8000`; no frontend contract change is needed. Stop any other server using port 8000 first. The semantic advisors now default to the existing local TF-IDF implementation, avoiding embedding-model downloads. An explicit `LOCAL_DRAFT_ADVISOR_BACKEND` override retains the prior optional embedding behavior.

Example first pick:

```powershell
$draft = @{
  team = 'blue'
  blue_picks = @(); red_picks = @()
  blue_bans = @('Fanny', 'Zhuxin', 'Hayabusa')
  red_bans = @('Joy', 'Suyou', 'Lukas')
  top_k = 5; strict_turn = $true
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/draft/recommend-picks -ContentType application/json -Body $draft
```

The same payload works with `/draft/advise-picks`; its response keeps the existing `recommendation` plus `advisor` shape. An initial ban request is `{ "team": "blue", "top_k": 5 }`.

## Behavior and source

`backend/services/modeling/mvp_artifacts/contextual-v1.json` is a 28,189-byte compact serving artifact. It contains global, batch, side, ally, and enemy counts from exactly 17 finalized development games across 16 matches: 119 batches, 170 positive memberships, and 51 historically observed heroes. The exact game and match inventories are in the artifact. It also contains team-specific first- and second-phase ban counts from those games and a 133-hero simulator pool copied from the current simulator catalog. It contains no raw game examples, review exports, query targets, holdout payloads, or unverified candidates.

The artifact derives from `contextual-full-development-ordered-bans-v2.json`, source file SHA-256 `2db7d7456e1c66794e6c9fc2ab1c27bdbdb46d1ce0df3b89409f9ec0834b9657`, payload hash `06d622ed38f909c5d8ee914c82f474b5bd1a968bde29cadb0159225d33f23fd3`, and corrected dataset hash `4dcfafa3b3cbeb5a845f2a55f7fff142eb49f92190979f6cf4472a602cd52431`. The exported artifact hash is `59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e`. The conversion utility rejects source bytes unless the complete snapshot hash and static development record allowlist pass before JSON payload decoding. Runtime reads only the compact artifact, checks its exact hash before decoding, and caches it once per process.

The contextual scorer preserves the frozen weights: global 0.20, batch 0.35, side 0.15, ally 0.15, enemy 0.15, conditional smoothing strength 10, and global pseudocount 1. Seven synthetic golden cases compare all components and top-five rankings with the original scorer. Bans only filter pick candidates; they do not change contextual score statistics. Ban recommendation scores are observed team/phase frequencies, without counter-pick or predicted-win claims.

Pick batches are `B1 -> {R1,R2} -> {B2,B3} -> R3 -> R4 -> {B4,B5} -> R5`. A partial double pick retains the context before that batch and only filters its first interactive member. R3 accepts only three known bans per team; R4 and subsequent batches require all five per team under `strict_turn=true`. Invalid prefixes, duplicates, pick/ban overlaps, unknown heroes, and wrong strict turns return HTTP 422. `strict_turn=false` retains requested-side preview overrides and allows missing ban information with `ban_knowledge=unknown_or_incomplete`; supplied future-phase bans are still rejected at early pick states.

Top-k is capped at 20 by the existing API. The current pool excludes selected heroes, known bans, and configured disabled heroes. Observed candidates keep their contextual scores; unsupported candidates follow with score zero and an explicit `catalog_fallback` evidence marker, ordered by hero name. No probabilities or legal historical universe are inferred from fallback. `rerank_pool_size` is retained for compatibility; the contextual scorer has no second-stage reranker and reports that policy explicitly.

Optional configuration, set before starting the server:

- `ML2_SIMULATOR_HERO_POOL`: path to a JSON array of unique exact hero names, to configure a different current simulator pool.
- `ML2_DISABLED_HEROES`: comma-separated names to exclude from that pool.

Configuration is cached; restart the server after changes. Corrupt artifacts and invalid pool configuration fail rather than silently changing the learned source. The response includes artifact/source hashes, dataset counts, fit status, and unknown historical eligibility warnings.

## Validation and limits

52 focused tests passed, including the four actual unmocked FastAPI routes, the existing API forwarding/schema tests, legacy direct recommender/advisor regressions, all 20 draft actions, all seven golden score cases, partial-double invariance, first/second ban boundaries, disabled/known exclusions, catalog-only cold start, malformed source/artifact guards, and empty-statistic fallback. Scoped Ruff passed. Tests use `--basetemp private/backend-mvp-pytest` after creating the `private` parent.

```powershell
python -m pytest -q tests/test_contextual_mvp.py tests/test_draft_api.py tests/test_pick_advisor.py tests/test_ban_advisor.py tests/test_pick_recommender.py tests/test_ban_recommender.py --basetemp private/backend-mvp-pytest
```

No model-quality validation was performed. This is a full-development MVP fit; its predictions do not measure out-of-sample accuracy. The source is small and tournament-specific, the scorer ranks double-pick members independently, historical competitive eligibility is unknown, and some source patch metadata is missing. Current legality depends on maintaining the simulator pool and explicit restrictions. Ban frequencies are descriptive source statistics, not independently validated optimal bans. Zero-score fallback guarantees usable candidates only while eligible configured heroes remain. Production deployment and frontend changes are outside this task.

For explicit artifact reproduction, run `python -m backend.scripts.export_backend_mvp --snapshot <exact-v2-snapshot> --hero-source <current-heroes.ts> --output <new-file>`. It exclusively creates output; never overwrite the frozen snapshots or historical source. A changed source or artifact requires a deliberately reviewed new version and pinned hash.
