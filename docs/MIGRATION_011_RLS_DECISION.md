# Migration 011 — Outbox RLS / Access-Model Decision Memo

**Date:** 2026-09-01
**Author:** Person A (Saish)
**Status:** DRAFT — pending Person B sign-off (required per CLAUDE.md joint-contract rule)

---

## Context

CONTRACTS.md §1/§5 (v1.13, merged as PR #85) locked the outbox design: the API writes
`analysis_jobs` + `outbox` rows atomically; a relay (not the API) owns broker delivery.
The relay sweeps undelivered rows across **all users** (`delivered_at IS NULL`).

Every other table uses FORCE ROW LEVEL SECURITY with `current_setting('app.user_id')`
scoped policies. The outbox has **no `user_id` column** by locked contract (§1), so
user-scoped RLS is structurally inexpressible for a cross-user sweeper.

This memo documents the three alternatives considered for how the relay accesses the
outbox, and the rationale for the chosen design.

---

## Alternative 1: Dedicated `relay` role (CHOSEN)

**Design:** Create a new `relay` PostgreSQL role — non-superuser, non-BYPASSRLS, LOGIN.
Grant `SELECT, UPDATE` on `outbox` **only**. No RLS / no FORCE RLS on `outbox`
(grants-only access control). `app_user` gets `INSERT` on `outbox` only.

**Pros:**
- Least-privilege: a compromised relay reads delivery metadata (job_id, attempts,
  delivered_at) — never documents, findings, emails, or user PII.
- No BYPASSRLS on an always-on process: the relay role cannot accidentally or
  maliciously bypass RLS on any user-scoped table.
- Cross-user sweep works: no RLS on outbox means the relay can `SELECT` where
  `delivered_at IS NULL` across all users.
- Clear audit trail: relay connections are visible in `pg_stat_activity` as a
  distinct role, separate from app_user and the owner.

**Cons:**
- One more role to manage (password rotation, connection pooling).
- A deliberate exception to the FORCE-RLS-everywhere convention — requires loud
  comments in the migration to prevent a future dev from "fixing" it.

**Mitigations:**
- Password via `RELAY_USER_PASSWORD` env var (same pattern as `APP_USER_PASSWORD`).
- Outbox RLS exception is loudly commented in the migration file and in DECISION_LOG.
- Relay has no grants on any user table — a compromised relay cannot read documents,
  findings, emails, or user data.

---

## Alternative 2: Owner role (BYPASSRLS)

**Design:** The relay connects as the owner/superuser role (same as Alembic migrations),
which has BYPASSRLS and can read across all users.

**Pros:**
- Simplest: no new role, no new grants, no new connection string.
- Cross-user sweep works trivially (BYPASSRLS skips all RLS).

**Cons:**
- Expands the sacred exception: the owner role is currently used only for migrations
  and schema-level operations (see DECISION_LOG 2026-07-15 RLS spike). Using it for
  an always-on process (the relay) fundamentally changes its scope.
- A compromised relay has full database access: all tables, all rows, all DDL.
  This violates least-privilege.
- No audit distinction: relay activity is indistinguishable from migration/app activity
  in `pg_stat_activity`.

**Verdict:** Rejected — violates the principle that BYPASSRLS is reserved for
schema-level operations only, not runtime processes.

---

## Alternative 3: Per-user sweep by `app_user`

**Design:** Keep FORCE RLS on outbox with a `user_id` column; the API sweeps only its
own user's undelivered rows.

**Pros:**
- Consistent with every other table: FORCE RLS + `user_id` scoping.

**Cons:**
- **Contradicts the locked contract.** CONTRACTS.md §1 defines the outbox columns
  (id, job_id, payload_json, attempts, last_attempt_at, delivered_at, created_at) —
  no `user_id`. Changing this requires a contract amendment, which needs both people's
  sign-off and re-locks §1.
- The relay is supposed to be decoupled from the API. If `app_user` owns the sweep,
  the relay's role as an independent delivery guarantee is undermined.
- Per-user sweep means N queries for N users (or a complex scan), vs. one relay
  scanning a small ephemeral table.

**Verdict:** Rejected — contradicts the locked contract; undermines the relay's
independence; adds complexity to the API path.

---

## Decision

**Chosen: Alternative 1 — Dedicated `relay` role.**

The outbox is the one table where grants-only (no RLS) is the only workable access
control for a cross-user sweeper. The dedicated relay role preserves least-privilege:
a compromised relay cannot read user data, and its activity is auditable as a distinct
role.

---

## Sign-off

- [ ] Person A (Saish): Designed — _________ (date)
- [ ] Person B (Nikhil): Approved — _________ (date)
