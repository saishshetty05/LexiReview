# LexiReview Decision Log

One line per non-obvious decision, added in the same PR as the change.
Historical decisions (rounds 1–5 of PRD cross-validation) live in the PRD v2.4.1, Section 15.

| Date | Decision | Rationale | Who |
|------|----------|-----------|-----|
| YYYY-MM-DD | RLS + connection pooler: <result of week-1 spike> | PRD SEC-2 requires validation | A |
| 2026-07-13 | LLM provider: Claude API for both dev and pilot | Model-family continuity between dev and bench, plus no-training API terms; LLM client stays provider-agnostic | A |
| 2026-07-13 | Step 2 Console setup deferred | OPEN ITEM — must complete before any LLM client work | A |
| 2026-07-13 | Model strings: dev = <Haiku-class choice>, pilot = <Sonnet-class choice> | — | A |
| 2026-07-13 | Windows phantom-modification issue: core.fileMode=false set locally on A's machine; .gitattributes added for line-ending normalization; B to apply same local config if phantoms appear | — | A |
| 2026-07-14 | Contracts v1 locked: retry via requeue (max 3), API-writes-queued-only, 14-category finding enum incl. missing_clause, immutable findings rows, unverified shown-never-dropped | — | B |
| 2026-07-15 | Manual user retry creates a NEW job row — terminal rows are immutable | — | B |
| 2026-07-15 | OPEN: storage location for the document summary (not a finding; not covered by CONTRACTS v1) | — | B |
| 2026-07-15 | Eager-mode Celery does not auto-recurse on self.retry() — it raises Retry through .get() instead of re-running the task; tests must drive the retry loop across separate apply() calls to match real-worker behavior | Discovered writing the DLQ-exhaustion test; avoids a false sense of coverage from a test that never actually exercised retry semantics | B |
