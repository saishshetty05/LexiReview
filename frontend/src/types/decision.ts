// CONTRACTS.md §7 (v1.7): persisted server-side in the `decisions` table,
// one per (reviewer, finding). Travels on Finding.decision (finding.ts),
// set via PUT /jobs/{id}/findings/{finding_id}/decision.
export type Decision = "pending" | "accepted" | "dismissed";
