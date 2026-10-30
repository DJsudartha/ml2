---
name: mlbb-backend-draft-api
description: Backend workflow for the MLBB Draft Website FastAPI draft recommendation API. Use when editing backend/main.py, backend/api/draft.py, request schemas, route behavior, CORS settings, or the wiring from route handlers into recommender services.
---

# MLBB Backend Draft API

Read the repo-root `AGENTS.md` first.

## Workflow

1. Start with `backend/main.py`, `backend/api/draft.py`, and `backend/services/modeling/advisor_pipeline.py`.
2. Keep route handlers thin. Put recommendation logic in `backend/services/modeling/` or `backend/services/llm/` instead of expanding route code.
3. If request or response fields change, update the frontend consumer and frontend types in the same task.
4. Follow the active documented entrypoint `backend.main:app`.

## Guardrails

- Treat `backend/models/draft_model.py` as legacy or stale unless the task explicitly targets it.
- `backend/api/draft.py` currently owns the active request model for draft routes.
- Preserve the `/draft` route prefix and existing endpoint names unless the task explicitly includes an API break.
- Call out contract changes clearly because the frontend posts directly to these routes.

## Done

Run these when backend Python code changes:

- `python -m pytest -q`
- `ruff check backend tests`

If the change affects frontend payloads, also run the frontend checks listed in `AGENTS.md`.
