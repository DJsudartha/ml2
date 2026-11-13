# Contributing

## Workflow

- Branch from `main`
- Use a short-lived branch named `feat/*`, `fix/*`, `chore/*`, `docs/*`, or `exp/*`
- Open a pull request for every change
- Merge with squash merge after checks pass

Do not push directly to `main` as part of the normal workflow.

## Before You Start

- Use a GitHub Issue for planned work when the change is larger than a small fix
- Keep pull requests focused on one logical change
- If a change affects both frontend and backend, call that out clearly in the PR summary

## Local Setup

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

### Backend

```bash
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

## Required Checks Before Opening a PR

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

## Commit Prefixes

Use lightweight commit prefixes that match the kind of change:

- `feat:` for new user-facing or developer-facing functionality
- `fix:` for bug fixes
- `docs:` for documentation updates
- `chore:` for maintenance or tooling
- `refactor:` for structural code changes without behavior changes
- `test:` for test-only changes

## Pull Requests

Each pull request should:

- explain what changed and why
- mention any backend API, model, or workflow impact
- include screenshots for UI changes when useful
- note the checks you ran locally
- link related issues when applicable

## Local-Only Files

Do not commit:

- `.env` files
- virtual environments such as `backend/venv` or `.venv`
- `frontend/node_modules`
- build output like `frontend/dist`
- caches, editor folders, and generated local artifacts
