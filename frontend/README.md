# Frontend

React + TypeScript + Vite frontend for the MLBB draft simulator.

## Commands

```bash
npm ci
npm run dev
npm run lint
npm run build
```

## Notes

- The production Pages build uses the Vite base path `/ml2/`
- The current API base points to the hosted backend on Render
- Set `VITE_API_BASE_URL=http://127.0.0.1:8000` in `frontend/.env.local` to target a local backend during development
- Repository-level collaboration rules and PR workflow live in the root `README.md` and `CONTRIBUTING.md`
