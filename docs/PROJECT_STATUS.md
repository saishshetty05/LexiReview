# PROJECT_STATUS.md — Technical Setup Handoff (as of 19 July 2026, Step 8 — B-side code-complete, A's auth PR in review)

Audience: Claude Code sessions working on this repo (and the two humans). You already know WHAT we are building (see CLAUDE.md and docs/PRD_LexiReview_v2_4_1_FINAL.docx). This file tells you WHERE THE PROJECT STANDS technically, what has been done, what is pending, and what happens next.

## 1. Team and roles
- Person A (repo owner, GitHub: saishshetty05) — INGESTION side: auth, upload endpoint, preflight gate, object storage, Postgres schema + Row-Level Security.
- Person B — ANALYSIS side: Celery worker, text extraction, [BLOCK_n] anchors, provider-agnostic LLM client, quote verification.
- Both use Claude Code (terminal) for in-repo work, each under their own Claude Pro account. Rule: AI writes, the OTHER human reviews. No code reaches main without a PR approved by the non-author.

## 2. Repository state
- Repo: https://github.com/saishshetty05/LexiReview (private, name capitalization is "LexiReview"; local folders are C:\projects\lexireview on both machines).
- Branch protection via ruleset "protect-main", ACTIVE, no bypass list: (a) PR required, 1 approval; (b) required status check: "ci"; (c) branches must be up to date before merging (expect the "update branch" step when main moves); (d) force pushes and deletions blocked.
- CI: .github/workflows/ci.yml — ruff (lint), bandit (security), pytest. Green on main.
- Canonical local test verification: plain `docker compose exec api pytest -q` — no env override needed (fixed 2026-07-18 by #19, see the vantage-point row in DECISION_LOG.md). Current count on main: **95 passed** (verified 2026-07-19, after #24).
- Merged-PR list (source of truth: `gh pr list --state merged`, chronological by merge time):
  - #2 fix(worker): set broker_connection_retry_on_startup to silence Celery… (NikhilKatti29)
  - #1 chore: add .gitattributes; log fileMode workaround (saishshetty05)
  - #3 docs: add PROJECT_STATUS.md technical handoff (saishshetty05)
  - #5 docs: lock interface contracts v1 (NikhilKatti29)
  - #4 feat(worker): job state machine per CONTRACTS v1 (NikhilKatti29)
  - #6 spike(rls): validate RLS + FORCE + pooled connections (SEC-2) (saishshetty05)
  - #9 test(db): migration-apply + RLS smoke tests; CI Postgres service (saishshetty05)
  - #8 feat(db): schema v1 with FORCE RLS and non-superuser app role (saishshetty05)
  - #7 feat(analysis): extraction + block anchors with per-job temp dirs (NikhilKatti29)
  - #11 docs: CONTRACTS v1.1 — document_summaries table (NikhilKatti29)
  - #10 feat(analysis): provider-agnostic LLM client stub with guardrails (NikhilKatti29)
  - #12 feat(analysis): deterministic quote verifier (NikhilKatti29)
  - #14 feat(storage): put/fetch document with RLS-scoped anti-enumeration errors; CONTRACTS v1.2 (saishshetty05)
  - #13 feat(analysis): pattern-based PII redaction gate (NikhilKatti29)
  - #17 feat(analysis): implement the anthropic provider call in LLMClient (NikhilKatti29)
  - #16 docs: refresh PROJECT_STATUS.md for mid-Step-8 state (NikhilKatti29)
  - #18 feat(db): migration 002 — is_synthetic + document_summaries (CONTRACTS v1.3) (saishshetty05)
  - #19 test(db): vantage-agnostic connection resolution; canonical verification simplified (saishshetty05)
  - #15 fix(analysis): assert exactly 2 spans on inconsistency evidence_quote (NikhilKatti29) — landed as "degrades to unverified," not the assert the title still names; see DECISION_LOG.md 2026-07-18 rows for the two-round revision
  - #20 feat(analysis): compose extraction/redaction/anchors/LLM/verification pipeline (NikhilKatti29)
  - #23 feat(worker): wire _execute_analysis to the real analysis pipeline (NikhilKatti29) — supersedes #21 (same task, closed unmerged 2026-07-19 and redone as #23 after main moved underneath it)
  - #22 docs: refresh PROJECT_STATUS.md — #13/#15/#17/#18/#19 merged, #20/#21 open (NikhilKatti29)
  - #24 feat(api): add GET /jobs/{id} and GET /jobs/{id}/findings (NikhilKatti29)
  - Still OPEN (not in this list): #25 feat(auth): register/login/logout + delete-account cascade (FR-15) (saishshetty05) — requesting Nikhil's review; a review comment is already posted flagging that its `get_current_user` dependency (cookie-JWT, yields `tuple[User, Session]`) isn't a drop-in for the jobs router's `get_current_user_id` placeholder (header-UUID) — see DECISION_LOG.md 2026-07-19.
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
- OPEN (the only thing actually blocking a real end-to-end run now): Anthropic Console workspace + two named API keys + spend limit. Decision made: Claude API for dev AND pilot (Haiku-class for dev, Sonnet-class pinned for bench); free trial credits first, paid top-up ~week 8. Owner: B. The provider-call code itself is no longer blocked on this (#17 implemented the real anthropic branch of LLMClient.analyze() ahead of the keys existing) — it just raises ProviderNotConfiguredError until a key is set.
- CLOSED (2026-07-18, #18): `is_synthetic` per-document flag. A decided: `documents.is_synthetic boolean NOT NULL DEFAULT FALSE`, fail-closed (an untagged document is treated as real). Migration 002 + CONTRACTS v1.3 §1a. `worker.py`'s `_execute_analysis` (#23) reads it off the same documents row already queried for file_type.
- CLOSED (2026-07-19): PR #20 (analysis_pipeline.py composition) and PR #23 (wires it into worker.py's `_execute_analysis`; supersedes the earlier #21) both merged. B-side pipeline is code-complete end to end pending the Console key (below).
- OPEN: PR #25 (A's auth: register/login/logout/delete-account, FR-15) is up for Nikhil's review. Flagged in review: its `get_current_user` dependency isn't a drop-in for the jobs router's `get_current_user_id` placeholder (app/deps.py) — different return shape (`tuple[User, Session]` vs. bare `uuid.UUID`) and different session-ownership pattern (dependency already holds `app_user_session` open vs. each jobs route opening its own). The swap will touch both `get_job` and `get_job_findings` bodies, not just an import change. Scoped as a fast-follow, not a blocker on #25 itself.
- Constitution reminders that constrain upcoming work: SYNTHETIC_ONLY=true enforced in the LLM client while on trial/free tiers; PII gateway must be live before the first LLM call on any non-synthetic document (live since #13); only doc_id/user_id transit the queue.
- Known recurring friction (not a bug, just a habit to keep): every PR that touches the last row of docs/DECISION_LOG.md collides with whatever merged most recently touching the same file. Expect it, not surprised by it — same one-line reorder fix each time.

## 6. Step 7 — CLOSED: interface contracts locked
docs/CONTRACTS.md v1 merged (PR #5): analysis_jobs columns/state machine/retry semantics, Findings JSON schema, analysis_results storage shape, and the polling/unverified-findings boundary behaviors are all DECIDED and locked. Changing CONTRACTS.md now requires both people's approval.

## 7. CURRENT STEP — Step 8: lanes split, parallel work in progress
- A1: RLS spike — DONE (PR #6, backend/spikes/rls_spike.py, PASS). Finding: FORCE ROW LEVEL SECURITY plus a non-superuser/non-BYPASSRLS role are both required; `lexireview` cannot enforce RLS on its own.
- A2: schema migrations (Alembic) — DONE (PR #8 schema, PR #9 migration/RLS tests). Migration 001 creates users/documents/analysis_jobs/analysis_results/audit_log, applies FORCE RLS, introduces the app_user non-superuser role (APP_DATABASE_URL).
- A4: document storage — DONE (PR #14, backend/app/storage.py: put_document/fetch_document, CONTRACTS.md §4 v1.2). Ships with the app_user_session context manager in db.py, shared by the worker. Landed with a CI fix (1ba93fd): ci.yml wasn't setting APP_DATABASE_URL, so RLS-dependent tests were silently running under the owner/BYPASSRLS role — db.py's fallback now fails hard instead of warning when CI=true. (Done out of order, ahead of A3.)
- A5: migration 002 — DONE (PR #18). `documents.is_synthetic` (fail-closed default FALSE, CONTRACTS v1.3 §1a) and the `document_summaries` table (CONTRACTS v1.1 §2b, FORCE RLS, INSERT+SELECT only, same immutability pattern as migration 001). Closes the `is_synthetic` open item from §5.
- A6: vantage-point test fix — DONE (PR #19). `conftest.py`'s Postgres fixtures now resolve host/port/db from `TEST_DATABASE_URL` → `DATABASE_URL` → `localhost`, so plain `docker compose exec api pytest -q` works with no manual override in any environment.
- A3: auth (register/login/logout/delete-account) — PR #25 OPEN, awaiting Nikhil's review (a review comment already left, see §5). Upload endpoint wired to preflight.py not yet started.
- B1: Celery job plumbing (pass-by-ID, states per the contract, retries/backoff, DLQ) — DONE (backend/app/models.py, jobs.py, worker.py; PR #4).
- B2: extraction (pypdf/python-docx, per-job random temp dirs) + [BLOCK_n] anchors — DONE (PR #7). LLM client — DONE, and no longer just a stub: PR #17 implemented the real anthropic branch of `LLMClient.analyze()` (forced `record_findings` tool use against `FINDING_JSON_SCHEMA`, temperature 0, 180s timeout, SDK-native retries). `gemini_free` remains a stub. Still needs a real `ANTHROPIC_API_KEY` (Console open item, §5) before it can actually run — until then it raises `ProviderNotConfiguredError`.
- B3: quote verifier — DONE (PR #12). A retro-review on 2026-07-17 found the inconsistency-span split had no cardinality check; took two rounds to close (A's own review feedback each time): a plain `assert` was rejected (bandit B101 — stripped under `-O`), then raising `ValueError` was also rejected (A: a span-count mismatch is a normal unverified outcome per constitution rule 6, not an error to propagate). Landed via PR #15 as `verification="unverified"` directly, no exception.
- B4: PII gateway — DONE (PR #13). Pattern-based redaction only (email/phone/PAN/Aadhaar/SSN); names/addresses deliberately deferred (see DECISION_LOG.md).
- B5: analysis_pipeline.py — DONE, merged as PR #20. Composes extract → pseudonymise → anchor → LLMClient.analyze → verify into `run_analysis()`. Rebuilt on a clean branch off main rather than the original `b/analysis-pipeline` branch's stale merge commits — same logic, no interface changes needed.
- B6: wire `_execute_analysis` in worker.py — DONE, merged as PR #23 (the original #21 was closed unmerged after main moved and redone cleanly as #23). One query resolves both `file_type` and `is_synthetic` off the documents row; fetches bytes via `storage.fetch_document`; persists findings as `AnalysisResult` rows. Anthropic errors surviving the SDK's own retry budget (`APIConnectionError`/`APITimeoutError`/`InternalServerError`/`RateLimitError`/`OverloadedError`) are wrapped as `TransientAnalysisError` for the job-level retry/DLQ layer; guardrail refusals and `fetch_document`'s typed errors are left unwrapped (terminal by design).
- B7: `GET /jobs/{id}` / `GET /jobs/{id}/findings` API endpoints (CONTRACTS.md §3) — DONE, merged as PR #24. Findings ordering: verified first then unverified, each group sorted by severity. Both routes 404 identically for a nonexistent job_id and one that exists but isn't owned by the caller (RLS anti-enumeration, same pattern as `storage.fetch_document`). Ships with `app/deps.py`'s `get_current_user_id`, an explicit auth placeholder (raw `X-User-Id` header) — see the #25 review note in §5 for why swapping it for A's real auth dependency will touch route bodies. Document summary generation (payload shape for `document_summaries`, CONTRACTS.md §2b) and the PRD's AI-6 entailment second pass remain deferred, not yet specced by either person.
- **B-side is code-complete end to end pending only a Console key** (§5). A-side: auth (#25) is in review; upload endpoint (wired to preflight.py) is next, not started.

## 8. How to keep this file useful
Update PROJECT_STATUS.md in the same PR whenever a step closes or an open item resolves. It is the orientation file for any fresh Claude session; stale status is worse than no status.
