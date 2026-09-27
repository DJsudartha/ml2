# AGENTS.md

## Project Overview

- This repository is an MLBB draft simulator with a Vite + React + TypeScript frontend, a FastAPI backend, and Python-based modeling and data-refresh scripts.
- The active frontend lives in `frontend/`.
- The active backend entrypoint is `backend.main:app`.
- Repository-level tests live in `tests/` and currently focus on backend recommendation and advisor behavior.

## Repo Map

- `frontend/`: draft simulator UI, static hero assets, Vite build config, GitHub Pages output.
- `backend/`: FastAPI routes, recommendation services, advisor generation, scripts, and committed data/model artifacts.
- `tests/`: pytest coverage for recommender and advisor logic.
- `.codex/skills/`: project-local Codex skill definitions that can be versioned with the repo.
- `.github/workflows/ci.yml`: the source of truth for CI checks.

## Commands

- Frontend install: `cd frontend && npm ci`
- Frontend dev: `cd frontend && npm run dev`
- Frontend lint: `cd frontend && npm run lint`
- Frontend typecheck: `cd frontend && npx tsc -p tsconfig.app.json --noEmit`
- Frontend tests: `cd frontend && npm run test`
- Frontend build: `cd frontend && npm run build`
- Backend install: `python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt`
- Backend dev server: `python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000`
- Backend tests: `python -m pytest -q`
- Python lint: `ruff check backend tests`

## Active Paths And Legacy Paths

- Treat `backend/main.py` as the active backend app entrypoint.
- Treat `backend/api/draft.py` and `backend/services/modeling/advisor_pipeline.py` as the active API flow.
- `backend/app/main.py` and `backend/models/draft_model.py` exist, but they do not match the current documented app flow. Do not treat them as the primary source of truth unless the task is explicitly about cleaning up legacy code.

## Working Rules

- Read the root `README.md` and the relevant feature files before editing.
- Treat the skills in `.codex/skills/` as project-local, versioned workflow guides.
- Keep frontend request and response shapes aligned with backend route models.
- Preserve the GitHub Pages base path `/ml2/` unless the task explicitly changes deployment.
- The frontend defaults to the local backend at `http://127.0.0.1:8000`; use `VITE_API_BASE_URL` for an explicit override.
- Preserve hero names exactly across frontend assets, backend data, and tests. Punctuation and spacing matter.
- Prefer consolidating duplicated request or type definitions rather than introducing new duplicates.

## Frontend Guidance

- Start with `frontend/src/features/draft-simulator/`.
- Check `api.ts`, `types/draft.ts`, `constants/draft.ts`, and `components/DraftInterface.tsx` before changing frontend data flow.
- There are duplicate request and response shapes in the frontend. If the task touches API wiring, decide whether to consolidate them instead of editing only one copy.
- Frontend tests use Vitest and Testing Library. Run them with lint, typecheck, and build validation.

## Backend Guidance

- Keep FastAPI routes thin and push logic into services.
- If request fields or response payloads change, update both backend route types and frontend consumers in the same change.
- Favor changes in `backend/services/modeling/` and `backend/services/llm/` over adding logic to route handlers.
- When debugging behavior, start from the relevant pytest files first.

## Modeling And Advisor Guidance

- High-risk regressions include turn resolution, unavailable-hero filtering, order-profile selection, recommendation payload shape, and advisor text format.
- `tests/test_pick_recommender.py`, `tests/test_ban_recommender.py`, `tests/test_pick_advisor.py`, and `tests/test_ban_advisor.py` are the first places to inspect before changing recommendation logic.
- The advisor layer currently expects a local semantic flow. Do not assume a hosted LLM is involved.
- Do not retrain models or regenerate committed artifacts unless the task explicitly calls for it.

## Data And Script Guidance

- Start with `backend/scripts/` for scraping, refresh, and training workflows.
- External-data tasks may require `backend/.env` and Liquipedia API keys.
- Separate raw-data refresh, processed-data rebuild, and model retraining as different operations.
- Avoid accidental churn under `backend/data/raw/`, `backend/data/processed/`, and `backend/data/modeling/models/`. If those files change, explain why.

## Review Priorities

- Prioritize bugs, schema drift, stale-path edits, incorrect draft-turn behavior, and accidental artifact churn over style-only feedback.
- For UI changes, call out any coupling to API-target behavior or the GitHub Pages base path.
- For backend changes, mention whether the change affects the frontend contract or committed data/model outputs.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for DJsudartha/ml2. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single context: root `CONTEXT.md` and `docs/adr/`, created when needed. See `docs/agents/domain.md`.
