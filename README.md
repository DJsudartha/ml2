# MLBB Draft Website

An MLBB draft simulator with data-driven ban and pick recommendations.

The repository currently contains:

- a Vite + React frontend for the draft UI
- a FastAPI backend for recommendation endpoints
- modeling and data-processing scripts for training and refreshing draft data
- a shared test suite focused on backend recommendation logic

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

Keep real secrets in `backend/.env`. Do not commit `.env` files, local virtual environments, or generated caches.

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
npm run build
```

```bash
python -m pytest -q
ruff check backend tests
```

## Deployment

- Frontend deploys to GitHub Pages from `main` via `.github/workflows/deploy-to-pages.yml`
- Backend is currently hosted separately from the Pages deployment workflow

## Notes for Collaborators

- The frontend currently posts recommendation requests to the hosted backend at `https://ml-2-8lkf.onrender.com`
- If you want local frontend-to-local-backend development, make the API base configurable before changing the default workflow
