# Turnkeeper web (Vite + React + TypeScript + Bryntum Scheduler trial)

```
npm ci             # Node 20.19+ (Node 24 is fine)
npm run dev        # http://localhost:5173, proxies /api, /webhooks, /mcp to http://localhost:8000
npm run build      # tsc --noEmit && vite build -> ../app/static (served by the FastAPI app)
npm test           # vitest: pure modules (board mapping, SSE parsing/reconnect, reducer, ticker/receipt helpers)
```

- `src/main.tsx` / `src/App.tsx`: page shell. Loads `GET /api/session` -> `GET /api/circles/{id}` -> `EventSource
  /api/sessions/{sid}/events`; a `board` event refetches the BoardModel. Header: CharterCard + Counters + Ticker;
  main: RotationBoard; right rail: AgentTrace + PayoutReceipt + QueueBadge; bottom: JudgeBar; Toasts on top.
- `src/api.ts` typed fetchers for the routes in `docs/CONTRACTS.md`; `src/events.ts` EventSource with reconnect.
- `src/state.ts` pure reducer that folds SSE events (`chip`, `trace`, `payout`, `ticker`, `toast`, `queue`,
  `board`, `schedule_changed`) into UI state; a `board` event bumps `boardVersion` and the App refetches.
- `src/board/mapping.ts` turns the BoardModel into scheduler resources (members in rotation order), events (one
  contribution per member per round, coloured by status, plus a "pot" milestone on the recipient's row at each due
  date), time ranges (one band per round and a "now" line) and the time axis (`monthAndYear`, or `weekAndMonth`
  for weekly circles). Unit-tested; the grid fallback renders from the same data.
- `src/board/RotationBoard.tsx` dynamically imports `src/board/SchedulerBoard.tsx` (the `@bryntum/scheduler-react`
  wrapper around the trial package). If the import or the first render fails, `src/board/GridBoard.tsx` (CSS grid,
  same props) takes over, so the page never goes blank. Hover shows order/capture/payout ids; click pins them.
- Bryntum packages: `@bryntum/scheduler@npm:@bryntum/scheduler-trial@7.3.7` plus `@bryntum/scheduler-react@7.3.7`
  (the wrapper has no `-trial` variant). `.npmrc` pins `@bryntum` to the public npm registry. The trial shows a
  "Bryntum Trial Version" watermark; that is expected. Both packages run `postinstall` scripts; they are approved in
  `package.json` (`allowScripts`) for npm 11's install-scripts gate. Older npm ignores that field.
- Theme: `src/theme/tokens.css` (light/dark tokens), `src/theme/bryntum.css` (structural CSS + FontAwesome +
  status classes); the Stockholm light/dark theme variables are bundled and swapped at runtime
  (`src/theme/useTheme.ts`). No CDN fonts or scripts; everything ships in `../app/static/assets`.
