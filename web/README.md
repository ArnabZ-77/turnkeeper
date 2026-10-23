# Turnkeeper web (Vite + React + TypeScript + Bryntum Scheduler trial)

```
npm ci             # Node 20.19+ (Node 24 is fine)
npm run dev        # http://localhost:5173, proxies /api, /webhooks, /mcp to http://localhost:8000
npm run build      # tsc --noEmit && vite build -> ../app/static (served by the FastAPI app)
npm test           # vitest: pure modules (board mapping, SSE parsing/reconnect, reducer, ticker)
```

- `src/api.ts` typed fetchers for the routes in `docs/CONTRACTS.md`; `src/events.ts` EventSource with reconnect.
- `src/state.ts` pure reducer that folds SSE events (`chip`, `trace`, `payout`, `ticker`, `toast`, `queue`,
  `board`, `schedule_changed`) into UI state; a `board` event bumps `boardVersion` and the App refetches.
- `src/board/mapping.ts` turns the BoardModel into scheduler rows/events/time ranges (unit-tested).
- `src/components/RotationBoard.tsx` drives the core `@bryntum/scheduler` (trial, aliased) directly; if the
  scheduler cannot start it falls back to a CSS grid with the same props.
- Theme: `src/theme/tokens.css` (light/dark tokens), `src/theme/bryntum.css` (structural CSS + FontAwesome +
  status classes); the Stockholm light/dark theme variables are bundled and swapped at runtime
  (`src/theme/useTheme.ts`). No CDN fonts or scripts.
- Bryntum trial packages run `postinstall` scripts; they are approved in `package.json` (`allowScripts`) for
  npm 11's install-scripts gate. Older npm ignores that field.
