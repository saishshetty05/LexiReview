# LexiReview frontend

Vite + React + TypeScript + Tailwind. Walking skeleton (UI slice 1) — see
`docs/DECISION_LOG.md`. Static fixture data only; no backend calls yet.

```
npm install
npm run dev      # http://localhost:5173, redirects to /review/:jobId
npm run build    # tsc -b && vite build
```

Route: `/review/:jobId` — split-screen review page. Left pane is a document
viewer placeholder; right pane renders findings from
`src/fixtures/findings.json`, matching `docs/CONTRACTS.md` §2's shape.
