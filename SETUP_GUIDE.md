# LexiReview — Complete Setup Guide for Person A and Person B

This guide takes you from two laptops with nothing installed to a working, shared development environment where both of you can build LexiReview in parallel. Follow it in order. Steps are marked **[A]** (Person A only), **[B]** (Person B only), or **[BOTH]** (each person does this on their own machine).

**Roles for this guide:** Person A owns the *ingestion side* (auth, upload, preflight gate, storage, database schema + RLS). Person B owns the *analysis side* (worker, extraction, block anchors, LLM client, verification). Person A is also the "repo owner" for setup purposes — this is an admin role, not a seniority ranking.

---

## Phase 0 — Prerequisites (do this first)

### Step 0.1 [BOTH] — Install the base tools

Install these on each laptop, in this order:

1. **Git** — https://git-scm.com/downloads. Verify: `git --version`
2. **Docker Desktop** — https://www.docker.com/products/docker-desktop/. Start it and let it finish initializing. Verify: `docker --version` and `docker compose version`. On Windows, enable the WSL 2 backend when the installer asks.
3. **Python 3.12+** — https://www.python.org/downloads/ (only needed for running tests/tools outside Docker; the app itself runs inside containers). Verify: `python3 --version`
4. **Node.js 18+** — https://nodejs.org (needed by Claude Code). Verify: `node --version`
5. **Claude Code** — in a terminal: `npm install -g @anthropic-ai/claude-code`, then run `claude` and log in **with your own Claude Pro account** when the browser opens. Each of you uses your own account — you get two separate usage pools. Verify: `claude --version`. (Docs: https://docs.claude.com/en/docs/claude-code/overview)

> Why Docker matters here: the entire stack (Postgres, Redis, MinIO, the API, the worker) runs in containers, so both of you get byte-identical environments. "Works on my machine" is eliminated on day one.

### Step 0.2 [BOTH] — Create the accounts

1. **GitHub account** (if you don't have one): https://github.com
2. **Google AI Studio key** (free dev-tier LLM): go to https://aistudio.google.com, sign in, create an API key. This is your **development** key — free tier, synthetic documents only (Google may use free-tier data for training, which is why our rules ban real documents on it). Each person creates their **own** key.
3. **Claude Console (API) workspace** — needed by ~week 9 for the pilot, not day one. When the time comes: one of you creates it at https://console.anthropic.com, adds a small credit balance, sets a spend alert, creates **two** API keys (one named per person), and shares access. Never share a single key.

### Step 0.3 [BOTH] — Agree on three things before touching code

Talk for ten minutes and write the answers down (they go in the decision log later):

1. Confirm the A/B role split above (or swap it — just be explicit).
2. Daily sync time (10 minutes, video or chat — "what I did, what I'm doing, what's blocking me").
3. PR rule: **no code reaches `main` without the other person's approval.** This mirrors the product's own human-in-the-loop principle: Claude writes code, the non-author human reviews it.

---

## Phase 1 — [A] Create and configure the GitHub repository

### Step 1.1 — Create the repo

1. GitHub → New repository → name: `lexireview` → **Private** → do NOT initialize with a README (we're pushing a skeleton) → Create.
2. Settings → Collaborators → Add people → invite Person B (they must accept the email invite).

### Step 1.2 — Protect the main branch

Settings → Branches → Add branch ruleset (or "Add rule" on older UI):

- Branch name pattern: `main`
- ✅ Require a pull request before merging
- ✅ Require approvals: **1**
- ✅ Require status checks to pass before merging → after the first CI run, select the `ci` check
- ✅ Do not allow bypassing the above settings (yes, this applies to you too — that's the point)

This makes the review rule *mechanical* instead of a promise.

### Step 1.3 — Push the starter skeleton

Unzip the starter files (`lexireview-starter.zip`) into a folder named `lexireview`, then:

```bash
cd lexireview
git init
git add .
git commit -m "chore: initial project skeleton (compose, CLAUDE.md, CI, preflight gate)"
git branch -M main
git remote add origin git@github.com:<A-username>/lexireview.git   # or the HTTPS URL
git push -u origin main
```

> If `git push` fails with authentication errors: set up either SSH keys (GitHub → Settings → SSH keys) or use `gh auth login` with the GitHub CLI. Do this once and it never bothers you again.

### Step 1.4 — Copy the project documents into the repo

Put the PRD and decision log where both of you (and both of your Claude Code instances) can see them:

```bash
mkdir -p docs
# copy PRD_LexiReview_v2_4_1_FINAL.docx into docs/
# create docs/DECISION_LOG.md and paste the decision log content into it
git add docs && git commit -m "docs: add PRD v2.4.1 and decision log" && git push
```

From now on, **the repo is the single source of truth** — any design change happens in a PR that updates both code and docs.

---

## Phase 2 — [BOTH] Clone and bring the stack up

### Step 2.1 — Clone

```bash
git clone git@github.com:<A-username>/lexireview.git
cd lexireview
```

### Step 2.2 — Create your local environment file

```bash
cp .env.example .env
```

Open `.env` in an editor and fill in:

- `GEMINI_API_KEY` — your **own** Google AI Studio key from Step 0.2
- Leave `SYNTHETIC_ONLY=true` alone. The LLM client refuses to run otherwise while on a free-tier provider — this is the PRD's data-protection rule turned into code.
- The Postgres/MinIO passwords in `.env.example` are fine for local dev (they never leave your machine).

**Rule: `.env` is in `.gitignore` and must NEVER be committed.** If a key ever lands in Git history, treat it as leaked: revoke it at the provider immediately and create a new one.

### Step 2.3 — Start everything

```bash
docker compose up --build
```

First run takes a few minutes (image downloads). You should end up with five services running: `postgres`, `redis`, `minio`, `api`, `worker`.

### Step 2.4 — Verify each service

Open a second terminal:

1. **API alive:** `curl http://localhost:8000/health` → `{"status":"ok"}`
2. **API docs render:** open http://localhost:8000/docs in a browser (FastAPI's auto-generated Swagger UI).
3. **MinIO console:** open http://localhost:9001, log in with the MinIO credentials from `.env`. You should see the console (empty for now).
4. **Postgres reachable:** `docker compose exec postgres psql -U lexireview -c "select version();"`
5. **Worker alive:** in the `docker compose` logs, the worker should print `celery@... ready.`

If all five pass, your environment is done. When both of you reach this point, you have identical stacks.

> Common problems: **port already in use** → something else on your machine uses 5432/6379/8000/9000; either stop it or change the left-hand port in `docker-compose.yml` (e.g. `"15432:5432"`). **Docker out of memory (Windows/Mac)** → Docker Desktop → Settings → Resources → give it 4 GB+.

---

## Phase 3 — [BOTH] Set up Claude Code for this repo

### Step 3.1 — First session

```bash
cd lexireview
claude
```

Claude Code automatically reads the committed `CLAUDE.md` at the repo root — that file is the project constitution (stack, non-negotiable rules, conventions, commands). Because it's committed, **both of your Claude instances wake up with identical context.** Read `CLAUDE.md` yourself once, top to bottom — it's short and it's the contract your AI pair-programmer follows.

### Step 3.2 — Sanity-check the setup with Claude

Ask Claude Code: *"Read CLAUDE.md and the docs folder, then summarize the architecture and my role's first tasks."* If the answer matches the PRD, your context is wired correctly.

### Step 3.3 — Usage discipline (Pro plan realities)

- Your Pro usage is **shared between claude.ai chat and Claude Code** in a rolling 5-hour session plus a weekly cap. Do heavy agentic coding in focused blocks; check `/usage` inside Claude Code when planning a long one.
- Never paste API keys or `.env` contents into any chat.

---

## Phase 4 — [BOTH] The working rhythm (how you avoid stepping on each other)

### Branching and PRs

1. Never commit to `main` directly (the branch protection blocks it anyway).
2. Branch naming: `a/<task>` or `b/<task>` — e.g. `a/preflight-endpoint`, `b/block-anchors`. The prefix instantly shows whose lane a branch is in.
3. Keep PRs **small** (target < 400 changed lines). Small PRs get reviewed in minutes; big ones rot.
4. PR flow: push branch → open PR → the **other** person reviews within one working day → address comments → squash-merge → delete branch.
5. Reviewer checklist (also in CLAUDE.md): Does it violate any non-negotiable (user_id filter on every query, no document text in logs, LLM client rules)? Are there tests? Does CI pass? Would I be able to maintain this?

### The interface contract (agree in week 1, then work in parallel)

Your two halves meet at exactly two artifacts. Define them together, put them in `docs/CONTRACTS.md`, and after that you can build independently for weeks:

1. **The `analysis_jobs` table** — columns, states (`queued → running → succeeded/failed`), who writes what.
2. **The findings JSON schema** — the exact structure the worker produces and the API serves.

Any change to either one requires both people's sign-off in the PR.

### The decision log lives on

Every non-obvious choice ("we picked pypdf over pdfplumber because…", "RLS works with the pooler in session mode, validated on <date>") gets one line in `docs/DECISION_LOG.md`, in the same PR as the change. This file has been the project's most valuable artifact so far — it becomes the spine of your internship report.

---

## Phase 5 — Week-1 task lists (your first real work)

### [A] Ingestion side — in this order

1. **RLS spike (highest risk first):** in Postgres, create a test table with a `user_id` column, enable Row-Level Security with a policy on `current_setting('app.user_id')`, and prove that `SET LOCAL app.user_id = ...` works through the app's connection pool. Record the result in the decision log — the PRD explicitly requires this validated in week 1.
2. **Schema v1:** `users`, `documents` (with `sha256`, `version`, immutability), `analysis_jobs`, `audit_log` (content-free: event_type, user_id, doc_id, timestamp, ip). Use Alembic migrations from the start.
3. **Auth:** register/login endpoints issuing JWT in HttpOnly/Secure/SameSite=Strict cookies; change-password; delete-account with cascading hard delete.
4. **Upload endpoint:** receives file → runs `preflight.py` (already in the skeleton) → on accept: store encrypted original in MinIO under a UUID path, create `documents` row + `analysis_jobs` row, return `job_id` in < 2 s → on reject: clear error message.

### [B] Analysis side — in this order

1. **Job plumbing:** a Celery task that picks up an `analysis_jobs` row by ID (pass-by-ID only — never raw text through Redis), flips states, retries with backoff, and lands failures in a dead-letter queue.
2. **Extraction:** PDF (pypdf/pdfplumber) and DOCX (python-docx) → plain text with structure, inside a per-job random temp directory that is always purged (`finally:`).
3. **Block anchors:** split extracted text into paragraphs/clauses, assign `[BLOCK_1] … [BLOCK_n]`, store blocks with their character offsets (the review UI needs offsets later to highlight).
4. **LLM client stub:** provider-agnostic (`LLM_PROVIDER` env var), temperature 0, JSON-schema-constrained output, 180 s timeout, refuses to run if `SYNTHETIC_ONLY=true` is violated or the pseudonymised flag is missing. Wire it to Gemini free tier first; the Anthropic API is a config change later.

### [BOTH] End-of-week-1 definition of done

- `docker compose up` gives both of you a green stack
- CI (lint + tests + Bandit) is green on `main`
- RLS spike result recorded in the decision log
- A user can register, log in, upload a synthetic PDF, and see a `job_id` — and the worker picks the job up and marks it running
- `docs/CONTRACTS.md` exists and both of you have signed off on it

---

## Phase 6 — Rules that never bend (copy of the CLAUDE.md non-negotiables)

1. Every database query is filtered by `user_id`. No exceptions, ever.
2. No document text, quotes, findings, or party-name filenames in any log line.
3. The LLM client never sends un-pseudonymised text, and never sends real documents on a free-tier provider.
4. Findings without a verified quote are never displayed as verified.
5. `.env` and any file containing a credential never enter Git.
6. All code reaching `main` was reviewed by the person who didn't write it.

---

## Quick reference — the whole setup in 12 commands

```bash
# [BOTH] prerequisites installed (git, docker, node, claude code), accounts created
# [A] creates private repo + branch protection + invites B, then:
git init && git add . && git commit -m "skeleton" && git branch -M main
git remote add origin <repo-url> && git push -u origin main
# [BOTH]
git clone <repo-url> && cd lexireview
cp .env.example .env        # fill in your own GEMINI_API_KEY
docker compose up --build   # postgres+redis+minio+api+worker
curl http://localhost:8000/health
claude                      # reads CLAUDE.md automatically
# daily loop:
git checkout -b a/<task>    # or b/<task>
# ...work with Claude Code, commit...
git push -u origin a/<task> # open PR, other person reviews, squash-merge
```

Good luck — and remember the project's own philosophy applies to you too: the AI does the heavy lifting, the human signs off.
