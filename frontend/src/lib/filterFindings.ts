import type { DecisionFilter } from "@/components/review/FilterChips";
import type { Decision } from "@/types/decision";
import type { Finding } from "@/types/finding";

export interface IndexedFinding {
  finding: Finding;
  index: number;
}

export function filterFindings(
  findings: Finding[] | null,
  decisions: Record<number, Decision>,
  filter: DecisionFilter,
): IndexedFinding[] {
  if (!findings) return [];
  return findings
    .map((finding, index) => ({ finding, index }))
    .filter(({ index }) => {
      if (filter === "all") return true;
      return (decisions[index] ?? "pending") === filter;
    });
}
