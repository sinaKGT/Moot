# Moot app (frontend)

React 19 + TypeScript + Vite. The look is "The Chamber" (see `docs/v1-features.md`, 3.11).

The built app lives in `src/moot/server/static/` and is committed, so running Moot needs no
Node.js: `moot serve` serves it. You only need Node.js 22+ to change the app.

```
npm install        # once
npm run dev        # live-reloading app at http://localhost:5173 (start `moot serve` too)
npm test           # unit tests (events, commands, team-builder draft, map layout, geometry)
npm run build      # type-check, then build into src/moot/server/static
```

Layout:

- `src/types.ts`: the server's data and the live events, as the app uses them
- `src/api.ts`: calls to the server
- `src/session/reducer.ts`: turns the event stream into what the screen shows (pure, tested)
- `src/commands.ts`: what you type: messages, /commands, @ and / autocomplete (tested)
- `src/builder/draft.ts`: the team builder's draft: editing, checks, and the session
  configuration it becomes (pure, tested)
- `src/components/builder/`: the team builder (library, table, agent card, model picker,
  moderator, rules, round-plan editor)
- `src/components/`: the discussion screen (rail, round table, poll card, transcript, input
  bar, "your move" prompts)
- `src/insights/`: the "Map & alliances" view: the argument map (its layout in
  `mapLayout.ts`, pure and tested) and the alliance monitor
