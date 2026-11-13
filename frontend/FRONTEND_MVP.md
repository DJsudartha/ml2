# Frontend MVP integration - 2026-10-25

All implementation lives in this separate frontend MVP worktree. Backend files,
training code, experimental datasets, model artifacts and API route contracts are
unchanged. The backend MVP from the parallel session is not assumed merged.

## Findings and changes

- The simulator used `/draft/advise-*` and expected the advisor wrapper instead
  of consuming the existing direct recommendation endpoints. It now uses
  `/draft/recommend-picks` and `/draft/recommend-bans` via the shared API client.
- Previously visible recommendations could describe an earlier draft, and late
  successes could replace current results. Results now belong to an exact draft
  state; obsolete requests are aborted and late success/error responses ignored.
- Malformed payloads previously looked like successful empty responses. The
  client checks the direct envelope, requested team, unique positive integer
  ranks, finite scores, string reasons and unavailable/duplicate heroes. Backend
  rank determines display order. Valid empty results, loading and errors have
  separate displays; errors identify the configured API target.
- Existing correct draft order is preserved: six first-phase bans, B1, R1/R2,
  B2/B3, R3, four second-phase bans, R4, B4/B5, R5. Headers identify phase/batch
  and remaining choices. Double batches allow either selection order and refresh
  after each actual choice. No pair model or historical ordering is implemented.
- Timeout selection now resets the timer after each choice, including the first
  member of a double batch. Reset clears results, cancels requests and restores
  empty state; starting again requests the initial blue ban. There was no undo
  control, and no new undo feature was added.

Every request carries only completed `blue_picks`, `red_picks`, `blue_bans`,
`red_bans`, the currently acting `team`, `top_k: 3`, `strict_turn: true` and
`rerank_pool_size: null`. Phase and action select the proper existing endpoint;
no future picks/bans, guessed candidate masks or extra batch contract are sent.
A partial double pick contains its actually selected first member. This is an
interactive refresh of existing inference, not sequential historical training.

## Files

MVP implementation and tests:

- `src/features/draft-simulator/api.ts` and `api.test.ts`
- `src/features/draft-simulator/types/draft.ts`
- `src/features/draft-simulator/data/draftOrder.ts`
- `src/features/draft-simulator/components/DraftInterface.tsx` and its test
- `src/features/draft-simulator/components/DraftHeader.tsx`
- `src/features/draft-simulator/components/RecommendationBox.tsx`
- This report

Existing uncommitted catalog work was carried from the original checkout, not
newly implemented for this MVP: `data/heroes.ts`, `data/heroes.test.ts`,
`public/HeroIcon/Hirara.png`, `public/HeroIcon/Hirara.provenance.md`, and the
pre-existing Hirara portion of `DraftInterface.test.tsx`. Keep this distinction
when integrating the worktree. The original checkout remains untouched.

## Run and integrate

From this worktree's `frontend/`:

```powershell
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173/ml2/ . Start the separately implemented FastAPI MVP
from its own checkout on `http://127.0.0.1:8000`, or explicitly set
`VITE_API_BASE_URL` before starting Vite. The `/ml2/` asset base is unchanged.
Current backend CORS allows localhost/127.0.0.1 port 5173 and the deployed GitHub
Pages origin. A different frontend origin requires backend-owner coordination.

No mismatch was found against the checked-in `backend/api/draft.py` and its
current recommendation return shapes: direct `{ team, recommendations }`, with
`{ hero, rank, score, reasons }` rows and optional metadata. Advisor helpers stay
compatible for other callers but are not required by this UI. Hero names must
match the frontend's canonical inventory. No new request/response schema or
frontend inference fallback was introduced, and no dependency was added.

Live backend-MVP rankings remain an integration dependency: the backend service
was unavailable during the browser smoke check. The rendered UI correctly
reported the failed request, and reset restored the initial controls. Browser
success with real recommendations is not claimed. Component/API tests use local
contract mocks only; the finished workflow always calls the configured API.

## Validation

```powershell
npm run lint
npm run typecheck
npm run test
npm run build
```

The complete-draft component test checks all 20 actual actions against an
independent expected trajectory, all seven batch labels, both ban phases,
selecting teams and exact causal request arrays. Other coverage checks reset,
abort-ignoring delayed successes/errors, loading versus empty/error results,
malformed/unavailable recommendations, ranking, and preserved Hirara behavior.
Independent read-only review corrected the last batch label and checked API,
state, asynchronous-response and scope boundaries.

Final result: **26/26 tests passed across all three frontend test files**.
Lint, typecheck, production build and whitespace checks passed. The double-pick
timeout regression also verifies that only the first known hero enters the next
request and the second slot is not consumed immediately.
