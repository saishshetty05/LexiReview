# PROJECT_STATUS.md — Technical Setup Handoff (as of 15 July 2026, end of Step 7)

Audience: Claude Code sessions working on this repo (and the two humans). You already know WHAT we are building (see CLAUDE.md and docs/PRD_LexiReview_v2_4_1_FINAL.docx). This file tells you WHERE THE PROJECT STANDS technically, what has been done, what is pending, and what happens next.

## 1. Team and roles
- Person A (repo owner, GitHub: saishshetty05) — INGESTION side: auth, upload endpoint, preflight gate, object storage, Postgres schema + Row-Level Security.
- Person B — ANALYSIS side: Celery worker, text extraction, [BLOCK_n] anchors, provider-agnostic LLM client, quote verification.
- Both use Claude Code (terminal) for in-repo work, each under their own Claude Pro account. Rule: AI writes, the OTHER human reviews. No code reaches main without a PR approved by the non-author.

## 2. Repository state
- Repo: https://github.com/saishshetty05/LexiReview (private, name capitalization is "LexiReview"; local folders are C:\projects\lexireview on both machines).
- Branch protection via ruleset "protect-main", ACTIVE, no bypass list: (a) PR required, 1 approval; (b) required status check: "ci"; (c) branches must be up to date before merging (expect the "update branch" step when main moves); (d) force pushes and deletions blocked.
- CI: .github/workflows/ci.yml — ruff (lint), bandit (security), pytest (currently the 5 preflight tests). Green on main.
- Merged history (squash merges):
  - Initial skeleton (compose stack, CLAUDE.md, CI, backend/app/preflight.py).
  - docs: PRD v2.4.1 + decision log.
  - fix(worker): broker_connection_retry_on_startup=True (B's PR — Celery 6 deprecation warning is GONE from worker startup logs; verified on rebuild).
  - chore: .gitattributes (text=auto; LF for .yml/.py; *.docx binary) + decision-log row for the fileMode workaround (A's PR).
  - #4 feat(worker): job state machine per CONTRACTS v1 (B's PR — backend/app/models.py AnalysisJob, jobs.py state-transition helpers, worker.py retry/DLQ wiring; 12 tests; analysis_jobs existed only as a SQLAlchemy model tested against SQLite, no Alembic migration yet).
  - #5 docs: lock interface contracts v1 (B's PR — docs/CONTRACTS.md now LOCKED: analysis_jobs columns/state machine/retry semantics, Findings JSON schema + analysis_results storage shape, polling/unverified-findings boundary behaviors. Changes now require both people's approval).
  - #6 spike(rls): validate RLS + FORCE + pooled connections (A's PR — backend/spikes/rls_spike.py, PASS: FORCE ROW LEVEL SECURITY plus a non-superuser/non-BYPASSRLS app role are both required; the dev `lexireview` role is superuser+BYPASSRLS and cannot enforce RLS. Closes the RLS decision-log placeholder).
- Local machine quirks already handled (do not re-debug):
  - core.fileMode=false set on BOTH machines (Windows phantom "modified everywhere").
  - .gitattributes prevents CRLF churn. If a whole-repo phantom diff appears again, check `git diff --stat` for 0 insertions/deletions before assuming real changes.
  - The .docx PRD is marked binary; it changes only via deliberate PRs.

## 3. Environment state
- Both machines run the full stack with `docker compose up --build`: postgres (pgvector/pgvector:pg16), redis:7, minio (S3 API :9000, console :9001), api (FastAPI, uvicorn --reload, :8000), worker (Celery, only task: ping).
- Verified on BOTH machines: `GET /health` -> `{"status":"ok"}`; Swagger at :8000/docs; MinIO console login works. "Identical environments" claim is demonstrated.
- .env exists locally on both machines (copied from .env.example), NOT in Git, never to be pasted into any Claude session. LLM keys are still BLANK (see §5).

## 4. Development workflow (established and drilled)
- Branch naming: a/<task> or b/<task>. Small PRs (<400 lines), squash-merge, delete branch after. Decision-log rows travel IN THE SAME PR as the change they describe.
- Review = read `gh pr diff`, confirm scope matches the PR's claim, confirm ci pass, approve with a comment, squash-merge. If main moved since branching: `gh pr update-branch <branch>` then re-check ci, then merge. NEVER use --admin.
- Both humans have run this loop end to end (one PR authored each, one reviewed each), including an update-branch queue event and a CI --watch race (harmless; re-watch).

## 5. Open items (tracked in docs/DECISION_LOG.md)
- OPEN: Anthropic Console workspace + two named API keys + spend limit. Decision made: Claude API for dev AND pilot (Haiku-class for dev, Sonnet-class pinned for bench); free trial credits first, paid top-up ~week 8. BLOCKS: any LLM client work beyond a stub. Owner: B. This is the only open item — everything else in this section as of Step 6 has closed (RLS decision closed by PR #6; CONTRACTS.md locked by PR #5).
- Constitution reminders that constrain upcoming work: SYNTHETIC_ONLY=true enforced in the LLM client while on trial/free tiers; PII gateway must be live before the first LLM call on any non-synthetic document; only doc_id/user_id transit the queue.

## 6. Step 7 — CLOSED: interface contracts locked
docs/CONTRACTS.md v1 merged (PR #5): analysis_jobs columns/state machine/retry semantics, Findings JSON schema, analysis_results storage shape, and the polling/unverified-findings boundary behaviors are all DECIDED and locked. Changing CONTRACTS.md now requires both people's approval.

## 7. CURRENT STEP — Step 8: lanes split, parallel work in progress
- A1: RLS spike — DONE (PR #6, backend/spikes/rls_spike.py, PASS). Finding: FORCE ROW LEVEL SECURITY plus a non-superuser/non-BYPASSRLS role are both required; `lexireview` cannot enforce RLS on its own.
- A2: schema migrations (Alembic) — IN PROGRESS (branch a/schema-migrations). Migration 001 creates users/documents/analysis_jobs/analysis_results/audit_log, applies FORCE RLS per the spike finding, and introduces the app_user non-superuser role the app connects as (APP_DATABASE_URL). Split into two PRs: schema (this one) and migration/RLS tests (a/schema-migration-tests, merges only after its tests pass locally).
- A3 (next, not started): auth, upload endpoint wired to preflight.py.
- B1: Celery job plumbing (pass-by-ID, states per the contract, retries/backoff, DLQ) — DONE (backend/app/models.py, jobs.py, worker.py; PR #4). analysis_jobs now has a real migration as of A2 above.
- B2 (next, not started): extraction (pypdf/python-docx) in per-job random temp dirs purged in finally, then [BLOCK_n] anchors, then the LLM client STUB (provider-agnostic, refuses un-pseudonymised or non-synthetic payloads — no real key needed yet).

## 8. How to keep this file useful
Update PROJECT_STATUS.md in the same PR whenever a step closes or an open item resolves. It is the orientation file for any fresh Claude session; stale status is worse than no status.
