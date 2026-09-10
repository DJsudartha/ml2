---
name: mlbb-frontend-draft-ui
description: Frontend workflow for the MLBB Draft Website React, TypeScript, and Vite draft simulator. Use when editing files under frontend/, changing the draft simulator UI, hero grid or slot components, recommendation panels, frontend payload shapes, or the hosted backend wiring.
---

# MLBB Frontend Draft UI

Read the repo-root `AGENTS.md` first.

## Workflow

1. Inspect `frontend/src/features/draft-simulator/components/DraftInterface.tsx`, `frontend/src/features/draft-simulator/api.ts`, `frontend/src/features/draft-simulator/types/draft.ts`, and `frontend/src/features/draft-simulator/constants/draft.ts` before editing frontend data flow.
2. Classify the task as presentational, API-related, or both. Keep UI-only changes inside the component tree. Keep fetch and payload changes centralized.
3. Prefer consolidating duplicated request and response types instead of updating one copy and leaving another stale.
4. Prefer using the shared fetch helper in `api.ts` instead of adding new direct `fetch` calls inside components.

## Guardrails

- Preserve the GitHub Pages base path `/ml2/` unless the task explicitly changes deployment.
- The default frontend API target is the hosted Render backend. Do not silently switch the default local-development workflow.
- Preserve hero names exactly. Asset filenames under `frontend/public/HeroIcon/` depend on spacing and punctuation.
- There is no established frontend test suite here yet, so lint, typecheck, and build are the required checks.

## Done

Run these when frontend behavior changes:

- `cd frontend && npm run lint`
- `cd frontend && npx tsc -p tsconfig.app.json --noEmit`
- `cd frontend && npm run build`

If the change also affects backend contracts, update the backend and frontend in the same task and mention the contract change explicitly.
