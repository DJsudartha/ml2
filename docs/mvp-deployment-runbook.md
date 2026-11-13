# ML² MVP deployment runbook

For the current live Pages and Render service and the proposed Docker path, see
[`docker-cicd-runbook.md`](docker-cicd-runbook.md). This earlier runbook records
pre-deployment preparation and its host-status statements are historical.

Status: configuration preparation only. No deployment or merge is authorized by this runbook.

## Established configuration and remaining setting

The checked-in `.github/workflows/deploy-to-pages.yml` publishes `frontend/dist` to GitHub Pages on a push to `main` or a manual dispatch. Vite uses `/ml2/`; the frontend has one page and no client-side URL routes requiring a Pages 404 rewrite. The intended frontend origin is `https://djsudartha.github.io` (application URL `https://djsudartha.github.io/ml2/`), already explicitly allowed by `backend/main.py` CORS. An origin never includes `/ml2/`.

No backend deployment definition or deployed backend URL exists in this checkout. The user confirmed that the historical Render backend was abandoned; there is no active backend host to reuse. Before approval, select a host and confirm its HTTPS service URL, connected repository, deployment branch and auto-deploy setting. Hosting selection and the confirmed backend URL remain blockers. Render is an option, not a selected provider.

Set the GitHub **repository Actions variable** `VITE_API_BASE_URL` to that confirmed HTTPS origin, without `/draft`. It is public build configuration, not a secret. Pages now runs `npm run build:deploy`, which rejects missing, HTTP, loopback and malformed origins before Vite can embed its local default. `npm run build` remains usable for local integration/CI; it is not the public deployment command. A changed API URL requires rebuilding Pages. Do not put credentials in any `VITE_*` setting.

## Backend host settings

Use the repository root as the service Root Directory, Python 3.12, and these commands on a Python web-service host. The host must provide `PORT`, or its equivalent must replace `$PORT` in the startup command. If Render is selected, leave Root Directory empty and use its Python service runtime. Use the approved release revision containing both MVP and deployment changes; do not deploy an older main that lacks the serving artifact.

Build command:

```sh
python -m pip install -r backend/requirements.txt && python -c "from backend.services.modeling.contextual_mvp import load_snapshot, hero_pool; load_snapshot(); assert hero_pool(); print('MVP serving artifact and pool verified')"
```

Start command:

```sh
python -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
```

Health check path: `/health`. Configure the host's Python runtime to a supported Python 3.12 release. Environment: `LOCAL_DRAFT_ADVISOR_BACKEND=tfidf` (also the application default). No Liquipedia, YouTube, LLM or training credentials are required for serving. Leave `ML2_SIMULATOR_HERO_POOL` unset to use the packaged 133-hero catalog; an override must be an existing runtime JSON file. Optionally set `ML2_DISABLED_HEROES` to comma-separated exact hero names. Restart after configuration changes. Ensure no old advisor override selects sentence-transformer embeddings.

`backend/requirements.txt`, rather than the stale root `requirements.txt`, is the dependency source. It includes FastAPI, Uvicorn, Pydantic, HTTPX and the scientific dependencies currently imported by advisor/legacy compatibility modules. Do not remove them based solely on the contextual scorer's standard-library implementation. The requirements also include optional embedding packages; this preparation does not change that dependency graph.

The source checkout must include `backend/scripts/export_backend_mvp.py`, `backend/services/modeling/contextual_mvp.py` and the committed `backend/services/modeling/mvp_artifacts/contextual-v1.json` (28,189 bytes). Its SHA-256 must remain `59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e`. Runtime validates and caches it once per process. No source datasets, protected labels or model refitting are needed. `/health` is a liveness check; the build preflight and real recommendation request below check serving readiness.

## Local release checks

From the integration checkout on Windows, after obtaining the confirmed origin:

```powershell
$env:VITE_API_BASE_URL = '<confirmed backend HTTPS origin>'
Push-Location frontend
npm ci
npm run test:deploy
npm run lint
npm run typecheck
npm run test
npm run build:deploy
Pop-Location
python -m pytest -q tests/test_contextual_mvp.py tests/test_draft_api.py --basetemp private/deployment-pytest
python -c "from backend.services.modeling.contextual_mvp import load_snapshot; print(load_snapshot()['version'])"
```

Inspect `frontend/dist/index.html` for `/ml2/assets/` paths. Inspect the built JavaScript to confirm the exact backend origin is embedded. Test builds using a placeholder domain establish packaging only; they must not be deployed. Pages already uploads all of `frontend/dist`, including `HeroIcon/Hirara.png`.

## Approved deployment sequence and smoke checks

1. Record the existing Pages deployment revision and chosen backend settings. Obtain explicit hosting/release/merge approval. Keep backend auto-deploy disabled while preparing if it would otherwise publish an unapproved push. A push to `main` automatically publishes Pages, so leave `main` unchanged until backend readiness is established.
2. After approval, push/stage the exact approved integration revision on its integration branch and deploy that revision to the chosen backend service. Confirm the build preflight, `/health`, real recommendation requests and production-origin CORS below before merging the frontend release. If the host cannot deploy an integration revision, explicitly gate/disable the Pages workflow before any main push and restore it only after backend smoke checks pass.
3. Set the confirmed `VITE_API_BASE_URL` repository variable. Then merge the reviewed integration change by the repository's normal process; the approved main push runs Pages. A manual retry after backend readiness is `gh workflow run deploy-to-pages.yml --repo DJsudartha/ml2 --ref main`. Check `gh run list --repo DJsudartha/ml2 --workflow deploy-to-pages.yml` and open the successful run.
4. Verify the deployed backend with the commands below, then open `https://djsudartha.github.io/ml2/`. In browser Network tools confirm recommendation POSTs reach the confirmed HTTPS backend, responses are 200 JSON, and no requests target localhost. Complete all 20 actions, including partial double picks, both ban phases, reset, and a rapid selection. Confirm no picked, banned or configured disabled hero is recommended. R3 has six completed bans; R4 onward has ten.

```powershell
$api = '<confirmed backend HTTPS origin>'
Invoke-RestMethod "$api/health"
$initialBan = @{ team='blue'; top_k=5; strict_turn=$true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/draft/recommend-bans" -ContentType application/json -Body $initialBan
$pick = @{ team='blue'; blue_picks=@(); red_picks=@(); blue_bans=@('Fanny','Zhuxin','Hayabusa'); red_bans=@('Joy','Suyou','Lukas'); top_k=5; strict_turn=$true } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/draft/recommend-picks" -ContentType application/json -Body $pick
Invoke-WebRequest -Method Options -Uri "$api/draft/recommend-picks" -Headers @{ Origin='https://djsudartha.github.io'; 'Access-Control-Request-Method'='POST'; 'Access-Control-Request-Headers'='content-type' }
```

The preflight must return `Access-Control-Allow-Origin: https://djsudartha.github.io`. Advice endpoints retain the same request body and return `{recommendation, advisor}`; the UI uses recommendation endpoints directly. The artifact/source hashes and `contextual-backend-mvp-v1` identify the intended serving revision. Scores are ranking weights from the 17-game development fit, without validated accuracy claims.

Check the chosen host's plan and cold-start behavior before release. A sleeping/starting backend or network failure can keep the UI loading or show its existing request error; stale requests are aborted and unavailable recommendations do not prevent manual drafting. The 30-second draft timer continues during loading, so warm the backend via `/health` and a real recommendation before the public smoke test. There is no automatic retry or bounded request timeout in this MVP. Do not claim uninterrupted availability on a sleeping service; choose a service plan for the intended availability.

## Rollback

Record last-known-good deployment revisions before each release. For later backend releases, redeploy the prior successful revision using the chosen host's rollback mechanism and restore recorded settings. On the first backend deployment there is no working backend revision to roll back to: disable the failed service and restore the previous frontend deployment if necessary. In GitHub Actions/Pages, redeploy the prior approved frontend revision using the Pages workflow's manual dispatch ref if Pages policy permits it, or revert the release commit through a normal approved revert PR and let `main` publish again. Restore the prior API repository variable before rebuilding if it changed. Verify `/health`, recommendation JSON, CORS and the Pages app after rollback. Do not rewrite source/snapshot hashes or reset main history.
