# PROJECT_STATUS.md — Technical Setup Handoff (as of 17 July 2026, mid Step 8)

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
- OPEN: Anthropic Console workspace + two named API keys + spend limit. Decision made: Claude API for dev AND pilot (Haiku-class for dev, Sonnet-class pinned for bench); free trial credits first, paid top-up ~week 8. BLOCKS: the real provider call in LLMClient.analyze() (currently a stub — see §7, B2). Owner: B. Deliberately not being worked right now.
- OPEN: `is_synthetic` per-document flag. `run_analysis()` (analysis_pipeline.py, on branch b/analysis-pipeline, not yet a PR) needs an `is_synthetic` bool per document to pass to `LLMClient.analyze()`, but nothing tracks this today — not on `documents`, not on `analysis_jobs`, not in CONTRACTS.md. Adding it means a schema column (A's lane) + a CONTRACTS.md version bump (needs both people's approval, same as any CONTRACTS change). Raised with A on 2026-07-17; blocks finishing the `_execute_analysis` wiring (worker.py) until resolved. Stopgap of hardcoding `True` (SYNTHETIC_ONLY already restricts dev/free-tier traffic to synthetic docs regardless) was considered but not adopted — waiting for A's decision instead.
- OPEN: PR #13 (b/pii-gateway) and PR #15 (verifier cardinality fix) are both CI-green and awaiting A's review — B can't self-approve (GitHub rejects it, and it'd violate the review rule in §4 anyway).
- Constitution reminders that constrain upcoming work: SYNTHETIC_ONLY=true enforced in the LLM client while on trial/free tiers; PII gateway must be live before the first LLM call on any non-synthetic document; only doc_id/user_id transit the queue.

## 6. Step 7 — CLOSED: interface contracts locked
docs/CONTRACTS.md v1 merged (PR #5): analysis_jobs columns/state machine/retry semantics, Findings JSON schema, analysis_results storage shape, and the polling/unverified-findings boundary behaviors are all DECIDED and locked. Changing CONTRACTS.md now requires both people's approval.

## 7. CURRENT STEP — Step 8: lanes split, parallel work in progress
- A1: RLS spike — DONE (PR #6, backend/spikes/rls_spike.py, PASS). Finding: FORCE ROW LEVEL SECURITY plus a non-superuser/non-BYPASSRLS role are both required; `lexireview` cannot enforce RLS on its own.
- A2: schema migrations (Alembic) — DONE (PR #8 schema, PR #9 migration/RLS tests). Migration 001 creates users/documents/analysis_jobs/analysis_results/audit_log, applies FORCE RLS, introduces the app_user non-superuser role (APP_DATABASE_URL).
- A4: document storage — DONE (PR #14, backend/app/storage.py: put_document/fetch_document, CONTRACTS.md §4 v1.2). Ships with the app_user_session context manager in db.py, shared by the worker. Landed with a CI fix (1ba93fd): ci.yml wasn't setting APP_DATABASE_URL, so RLS-dependent tests were silently running under the owner/BYPASSRLS role — db.py's fallback now fails hard instead of warning when CI=true. (Done out of order, ahead of A3.)
- A3 (next, not started): auth, upload endpoint wired to preflight.py.
- B1: Celery job plumbing (pass-by-ID, states per the contract, retries/backoff, DLQ) — DONE (backend/app/models.py, jobs.py, worker.py; PR #4).
- B2: extraction (pypdf/python-docx, per-job random temp dirs) + [BLOCK_n] anchors — DONE (PR #7). LLM client — DONE as a STUB (PR #10): enforces every CLAUDE.md rule 3 guardrail (pseudonymisation required, SYNTHETIC_ONLY on free tiers, API-key presence) but `LLMClient.analyze()` still raises `NotImplementedError` after guardrails pass — no real provider call is wired up yet. Blocked on the Anthropic Console open item above.
- B3: quote verifier — DONE (PR #12). A retro-review on 2026-07-17 found the inconsistency-span split had no cardinality check; closed via PR #15 (raises `ValueError` for anything but exactly 2 spans — not a plain `assert`, since bandit flagged that asserts are stripped under `-O`).
- B4: PII gateway — PR #13 OPEN, CI green, rebased onto main, awaiting A's review. Pattern-based redaction only (email/phone/PAN/Aadhaar/SSN); names/addresses deliberately deferred (see DECISION_LOG.md).
- B5: analysis_pipeline.py — composes extract → pseudonymise → anchor → LLMClient.analyze → verify into `run_analysis()`. Written and tested on local branch `b/analysis-pipeline`, not yet opened as a PR (blocked on #13 merging first, since it imports `app.pii_gateway`).
- B6 (next, blocked): wire `_execute_analysis` in worker.py (currently a no-op placeholder) to call `run_analysis()`. Blocked on the `is_synthetic` open item above — everything else it needs (storage, extraction, anchors, PII gateway, verifier) exists.

## 8. How to keep this file useful
Update PROJECT_STATUS.md in the same PR whenever a step closes or an open item resolves. It is the orientation file for any fresh Claude session; stale status is worse than no status.
