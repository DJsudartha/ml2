# ML² Docker and CI/CD runbook

This branch prepares a container and release gates. It does **not** change the
running [GitHub Pages site](https://djsudartha.github.io/ml2/) or the running
[Render backend](https://ml2-backend-sfy3.onrender.com). Do not switch the Render
runtime or merge this branch until the image and service cutover are approved.

## What the workflows do

- `.github/workflows/ci.yml` runs on pull requests and `main` pushes. It checks
  backend pytest and Ruff, frontend lint, typecheck, tests and the public Pages
  build, then builds the Docker image and exercises real API requests in a
  container limited to 512 MiB. The Docker job requires both test jobs to pass.
- `.github/workflows/deploy-to-pages.yml` retains the existing Pages release on
  `main`, but now waits for a successful `CI` push run for the current `main`
  commit. It still requires a valid public HTTPS `VITE_API_BASE_URL` via
  `npm run build:deploy`. Manual dispatch from `main` remains an explicit
  operator action.
- `.github/workflows/deploy-backend-render.yml` is manual and inert unless the
  repository variable `RENDER_DOCKER_DEPLOY_ENABLED` is exactly `true`. It
  checks that the current `main` commit passed CI, then uses the secret
  `RENDER_DEPLOY_HOOK_URL` to request that exact commit. A successful hook call
  means a deploy was queued, not that the service is healthy.

The current Pages Actions variable is
`VITE_API_BASE_URL=https://ml2-backend-sfy3.onrender.com`. Keep the value as an
origin, without `/draft`, `/ml2/` or a trailing path. The FastAPI CORS list
allows `https://djsudartha.github.io`; `/ml2/` is a URL path, not an origin.

## Local Docker check

Run from the repository root with Docker Desktop's Linux engine running:

```powershell
docker build -t ml2-backend:local .
docker run --detach --name ml2-local --memory=512m -p 127.0.0.1:10000:10000 -e PORT=10000 ml2-backend:local
python .github/scripts/smoke_backend.py http://127.0.0.1:10000
docker image inspect ml2-backend:local --format '{{.Size}}'
docker stats ml2-local --no-stream --format '{{.MemUsage}}'
docker logs ml2-local
docker rm --force ml2-local
```

The image uses Python 3.12, a non-root user and a single Uvicorn process. It
contains the FastAPI import closure and
`backend/services/modeling/mvp_artifacts/contextual-v1.json`. Its build-time
preflight loads the 17-game serving snapshot and runs pick and ban advice.
`.dockerignore` allows only serving code, the requirements file and that
artifact; no raw datasets, reviews, holdouts, frontend bundle or local secrets
enter the build context. `PORT` defaults to 10000 and can be overridden.
`LOCAL_DRAFT_ADVISOR_BACKEND=tfidf` avoids optional embedding downloads.

The first [PR CI Docker run](https://github.com/DJsudartha/ml2/actions/runs/40563202186)
built an image of **487,197,034 bytes** (about 465 MiB). After health, pick,
ban, advice, chronology and CORS smoke requests, Docker reported **113.8 MiB**
of resident container memory under a **512 MiB** limit. The smoke passed. Image
bytes occupy disk and are not the service RAM requirement. This short test
demonstrates basic compatibility with the Free memory limit, but does not
establish peak memory under production traffic; check Render memory metrics
after cutover as well.

## Render cutover, only after approval

1. Record the currently live Render deploy commit, runtime, settings and
   rollback option. Keep the existing Python service live during CI review.
2. Require `frontend`, `backend` and `docker` CI checks on the branch. Review
   image size, memory and API smoke output. Merge only after release approval.
3. In the existing Render service's **Settings → Build → Source**, change the
   Git runtime to Docker using the repository-root `Dockerfile`. Leave its
   linked repository and `main` branch intact. Set health check path `/health`,
   root directory to the repository root, and keep `PORT=10000` unless Render
   supplies a different port. Do not set a separate build or start command;
   the Dockerfile supplies both. Set auto-deploy to **Off** for a controlled
   manual cutover. Confirm the free 512 MiB plan remains selected.
4. Deploy the approved `main` commit from Render's dashboard. Check build
   logs, successful health check and that the expected commit is live. The
   manual GitHub workflow may be used later, after the Docker service and a
   protected `render-production` environment are explicitly configured:

   ```powershell
   gh secret set RENDER_DEPLOY_HOOK_URL --repo DJsudartha/ml2
   gh variable set RENDER_DOCKER_DEPLOY_ENABLED --repo DJsudartha/ml2 --body true
   gh workflow run deploy-backend-render.yml --repo DJsudartha/ml2 --ref main
   ```

   Enter the hook URL only through the secret prompt; never print or commit it.
   Keep the enable variable unset or false until the cutover is approved.
   Protect the `render-production` GitHub environment with required reviewers.
5. Smoke the live backend and the Pages UI:

   ```powershell
   $api = 'https://ml2-backend-sfy3.onrender.com'
   Invoke-RestMethod "$api/health"
   python .github/scripts/smoke_backend.py $api
   gh run list --repo DJsudartha/ml2 --workflow deploy-to-pages.yml --limit 5
   ```

   Open `https://djsudartha.github.io/ml2/` and confirm recommendations come
   from the Render origin. Test a full draft, reset and a cold start. A Free
   Render service may sleep after idle and take about a minute to wake; the UI
   must surface a temporary network delay rather than imply model confidence.

The hook workflow does not itself wait for Render's deployment result. Treat
its green status only as a successfully submitted request; check Render's
deploy status and run the live smoke separately.

## Rollback

- If the Docker deploy fails, use the Render dashboard's deploy history to
  roll back to the recorded last working Python deploy, or change the service
  Source runtime back to Python with its recorded build/start settings.
  Recheck `/health`, all recommendation endpoints and CORS. Keep auto-deploy Off
  until the failure is understood.
- If Pages fails, redeploy the last working Pages commit using GitHub Actions
  after confirming its API URL. Do not repoint `VITE_API_BASE_URL` to localhost.
  If a bad `main` commit caused the issue, revert it through the normal PR and
  passing CI path.
- Render Free retains only a limited number of prior deploys for dashboard
  rollback, so record the working commit before cutover.

## Remaining release gate

No Docker switch or automatic backend release is enabled by this branch.
Approval requires a passing Docker CI job with measured image/memory values,
then a controlled Render cutover and live smoke. The contextual scorer is an
experimental frequency baseline; these checks establish serving reliability,
not predictive accuracy.
