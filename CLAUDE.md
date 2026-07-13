# CLAUDE.md — LexiReview Project Constitution

LexiReview is an AI-assisted legal document review tool (single-document analysis: risky clauses,
missing clauses, internal inconsistencies) built per `docs/PRD_LexiReview_v2_4_1_FINAL.docx`.
Two developers: Person A owns ingestion (auth, upload, preflight, storage, schema/RLS);
Person B owns analysis (worker, extraction, anchors, LLM client, verification).

## Stack
- FastAPI (Python 3.12) · PostgreSQL 16 + RLS + pgvector · Celery + Redis · MinIO (S3 API)
- React frontend (later) · Nginx ingress (later) · docker-compose for everything
- LLM via provider-agnostic client: Gemini free tier in dev (synthetic docs only), paid no-training endpoint for pilot

## Non-negotiable rules (violating any of these fails code review)
1. EVERY database query is scoped by user_id in the data-access layer. Foreign IDs return 404.
2. NEVER log document text, quotes, findings, or filenames containing party names. Audit log is
   metadata only (event_type, user_id, doc_id, timestamp, ip). IPs are purged after 90 days.
3. The LLM client: temperature 0, JSON-schema-constrained output, 180 s timeout, pinned model
   string from env. It MUST refuse payloads not flagged pseudonymised, and MUST refuse
   non-synthetic documents when LLM_PROVIDER is a free tier (SYNTHETIC_ONLY=true).
4. Only doc_id/user_id transit the Celery queue — never raw document text.
5. Extraction runs in a per-job random temp dir, always purged in a finally block.
6. Findings without a machine-verified quote (exact substring, then fuzzy >= threshold) are never
   shown as verified. Unverified findings are flagged, never silently dropped.
7. The UI never says a document is "safe" — only "no issues detected by automated review".
8. Uploads pass the preflight gate (backend/app/preflight.py) BEFORE any job is enqueued:
   size cap, magic bytes, page cap (~100), encryption/corruption check, SHA-256.
9. Documents are versioned and immutable: re-upload = new version row, never an overwrite.
10. Secrets live only in .env (gitignored). Never print, commit, or paste them.

## Conventions
- Branches: a/<task> or b/<task>. PRs < 400 lines, squash-merged, reviewed by the non-author.
- Migrations: Alembic, one migration per PR that touches schema.
- Tests: pytest; every new module gets tests; CI must be green before merge.
- Style: ruff for lint/format; type hints on public functions.
- Any non-obvious decision gets one line in docs/DECISION_LOG.md in the same PR.

## Commands
- Run stack: `docker compose up --build`
- Tests: `docker compose exec api pytest` (or `cd backend && pytest` with a venv)
- Lint: `ruff check backend`
- DB shell: `docker compose exec postgres psql -U lexireview`

## Interface contract (owned jointly — changes need both people's sign-off)
- analysis_jobs table shape and state machine: docs/CONTRACTS.md
- Findings JSON schema: docs/CONTRACTS.md
