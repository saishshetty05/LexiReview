# LexiReview Decision Log

One line per non-obvious decision, added in the same PR as the change.
Historical decisions (rounds 1–5 of PRD cross-validation) live in the PRD v2.4.1, Section 15.

| Date | Decision | Rationale | Who |
|------|----------|-----------|-----|
| YYYY-MM-DD | RLS + connection pooler: <result of week-1 spike> | PRD SEC-2 requires validation | A |
| 2026-07-13 | LLM provider: Claude API for both dev and pilot | Model-family continuity between dev and bench, plus no-training API terms; LLM client stays provider-agnostic | A |
| 2026-07-13 | Step 2 Console setup deferred | OPEN ITEM — must complete before any LLM client work | A |
| 2026-07-13 | Model strings: dev = <Haiku-class choice>, pilot = <Sonnet-class choice> | — | A |
