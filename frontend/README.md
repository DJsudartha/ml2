# Frontend

React + TypeScript + Vite frontend for the MLBB draft simulator.

## Commands

```bash
npm ci
npm run dev
npm run lint
npm run typecheck
npm run test
npm run build
```

## Notes

- The production Pages build uses the Vite base path `/ml2/`
- The default API base is the local backend at `http://127.0.0.1:8000`
- Set `VITE_API_BASE_URL` only when an explicit alternative backend is available
- Repository-level collaboration rules and PR workflow live in the root `README.md` and `CONTRIBUTING.md`
