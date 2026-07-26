import type { DecisionFilter } from "@/components/review/FilterChips";
import type { Finding } from "@/types/finding";

// CONTRACTS.md §7 (v1.7): decision now travels directly on each Finding, so
// no separate decisions map is needed to filter by it.
export function filterFindings(findings: Finding[] | null, filter: DecisionFilter): Finding[] {
  if (!findings) return [];
  if (filter === "all") return findings;
  return findings.filter((finding) => finding.decision === filter);
}
