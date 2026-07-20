// UI-only local review state -- NOT part of the CONTRACTS.md §2 Findings
// shape (see finding.ts). No persistence in this slice: a decisions table
// + reviewer identity (PRD FR-13) is a later PR.
export type Decision = "pending" | "accepted" | "dismissed";
