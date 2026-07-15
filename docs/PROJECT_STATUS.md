# PROJECT_STATUS.md — Technical Setup Handoff (as of 13 July 2026, end of Step 6)

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
- OPEN: Anthropic Console workspace + two named API keys + spend limit. Decision made: Claude API for dev AND pilot (Haiku-class for dev, Sonnet-class pinned for bench); free trial credits first, paid top-up ~week 8. BLOCKS: any LLM client work beyond a stub. Owner: B.
- OPEN: model strings row in the decision log still has placeholders.
- Constitution reminders that constrain upcoming work: SYNTHETIC_ONLY=true enforced in the LLM client while on trial/free tiers; PII gateway must be live before the first LLM call on any non-synthetic document; only doc_id/user_id transit the queue.

## 6. CURRENT STEP — Step 7: the contracts session (both humans, ~30–45 min)
Purpose: lock the interface where A's lane hands off to B's lane, in docs/CONTRACTS.md, via one PR (proposed branch: b/contracts-v1, authored by B, reviewed by A strictly for "is this what we agreed?"). After merge, changing CONTRACTS.md requires both approvals.

Decisions the humans must make (Claude: help draft, do not decide for them):

- `analysis_jobs` table — final columns: id uuid pk · user_id fk · doc_id fk · doc_version_hash text · state · retry_count int · error_reason text NULLABLE (metadata only, NEVER document content) · created_at · started_at · finished_at. State machine: queued -> running -> succeeded | failed. Decide: retry semantics (failed->queued vs running with retry_count++) and writer rules (proposal: API writes only the initial queued row; worker owns all transitions).
- Findings JSON — one finding object with: category (enum: the 12 FR-9a clause categories + "inconsistency"), severity (high|medium|low|info), block_ids (array; exactly 2 for inconsistency), evidence_quote (verbatim), explanation, verification (verified|unverified — unverified is FLAGGED, never dropped, per constitution rule 6), confidence (standard|needs_review). Storage proposal: analysis_results table, one row per finding, JSONB payload + extracted columns for filtering. Exercise: write the exact JSON for one fake lease with a rent inconsistency before finalizing the schema.
- Boundary behaviors: (a) API response shape while a job is running (polling); (b) how unverified findings are stored and rendered.

## 7. Immediately after Step 7 (Step 8 — lanes split, parallel work begins)
- A1: RLS spike (backend/spikes/rls_spike.py proving Row-Level Security with SET LOCAL app.user_id through a pooled SQLAlchemy connection; PASS/FAIL output; result closes the placeholder decision-log row). Then schema migrations (Alembic), auth, upload endpoint wired to preflight.py.
- B1: Celery job plumbing (pass-by-ID, states per the contract, retries/backoff, DLQ) — DONE (backend/app/models.py, jobs.py, worker.py; b/job-plumbing). Note: analysis_jobs exists only as a SQLAlchemy model, tested against SQLite — no Alembic migration yet, since migrations are A's lane; the table isn't in the real Postgres DB until that migration lands.
- B2: extraction (pypdf/python-docx) in per-job random temp dirs purged in finally, then [BLOCK_n] anchors, then the LLM client STUB (provider-agnostic, refuses un-pseudonymised or non-synthetic payloads — no real key needed yet).

## 8. How to keep this file useful
Update PROJECT_STATUS.md in the same PR whenever a step closes or an open item resolves. It is the orientation file for any fresh Claude session; stale status is worse than no status.
