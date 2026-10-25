# ML² MVP integration report

Integration branch: `codex/mvp-integration` in `C:\Users\djsud\.codex\worktrees\mvp-integration\mlbb-draft-website`.
Shared donor base: `fa1fcd81e78452105fa772320e59c8c829ba537b`. Backend and frontend donor changes were uncommitted; the allowlisted byte-copy receipt is `private/mvp-integration-copy-receipt.json`. Main and donor checkouts were not modified. Integration copied the backend and frontend MVP implementations; the only integration-specific code edit increased one stale-response test timeout to 15 seconds.

## Result

- **Local MVP: PASS.** A live FastAPI server and Vite frontend completed all 20 draft actions across six initial bans, B1, R1/R2, B2/B3, R3, four later bans, R4, B4/B5, and R5. Browser recommendations came from the live API. The UI reached Complete, and reset/rapid follow-up actions worked without an unavailable-recommendations message.
- **Deployment: NOT YET VERIFIED.** No deployment was performed. A deployment needs an explicit `VITE_API_BASE_URL`, corresponding backend CORS origin, production smoke test, and product acceptance of the small 17-game statistical baseline and unknown historical competitive eligibility. Scores are ranking weights, not validated confidence or accuracy.

## Validation

- Focused backend pytest: 52 passed.
- Scoped backend Ruff: passed.
- Frontend Vitest: 26 passed; lint, TypeScript typecheck, and Vite production build passed.
- `git diff --check`: passed.
- Live API: 20 sequential strict-turn actions, 10 bans and 10 picks, completed in 0.77 seconds after warmup; no returned candidate was already picked or banned. Partial double-pick states retained shared batch semantics. R4 with only six bans returned HTTP 422. Both advice endpoints returned HTTP 200.
- Live browser: all 20 actions reached Complete with no recommendation error. Reset cleared the draft; three rapid subsequent actions retained the correct phase and live suggestions.
- Live sparse-pool API on a separate local port: five deterministic zero-score fallback picks; configured disabled hero absent from pick and ban top-five results.
- CORS preflight: configured Vite origins allowed; an unconfigured port rejected. Build retained the `/ml2/` base path. Backend snapshot hash remained `59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e`; backend and frontend each had 133 matching hero names including Hirara.

The optional local dependency install reported 9 existing npm audit advisories (4 moderate, 5 high); no dependency or lockfile was changed.

## Run locally

From the integration worktree root:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173/ml2/`. If port 5173 is already occupied by another Vite process, stop that process first or run the integrated Vite server with `--host ::1 --port 5173 --strictPort` and open `http://localhost:5173/ml2/`. Set `VITE_API_BASE_URL` when using a backend other than `http://127.0.0.1:8000`.
