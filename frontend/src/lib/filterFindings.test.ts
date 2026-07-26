import { describe, expect, it } from "vitest";

import { filterFindings } from "@/lib/filterFindings";
import type { Finding } from "@/types/finding";

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    category: "missing_clause",
    severity: "medium",
    block_ids: [],
    evidence_quote: "",
    explanation: "test finding",
    verification: "verified",
    confidence: "standard",
    ...overrides,
  };
}

describe("filterFindings", () => {
  it("returns an empty array when findings is null", () => {
    expect(filterFindings(null, {}, "all")).toEqual([]);
  });

  it("returns every finding, indexed, for filter='all' regardless of decisions", () => {
    const findings = [makeFinding(), makeFinding(), makeFinding()];
    const decisions = { 0: "accepted", 1: "dismissed" } as const;

    const result = filterFindings(findings, decisions, "all");

    expect(result).toEqual([
      { finding: findings[0], index: 0 },
      { finding: findings[1], index: 1 },
      { finding: findings[2], index: 2 },
    ]);
  });

  it("treats a finding with no decision entry as 'pending'", () => {
    const findings = [makeFinding(), makeFinding()];
    // index 0 has an explicit decision, index 1 has none
    const decisions = { 0: "accepted" } as const;

    const result = filterFindings(findings, decisions, "pending");

    expect(result).toEqual([{ finding: findings[1], index: 1 }]);
  });

  it("filters to only 'accepted' findings, preserving original index", () => {
    const findings = [makeFinding(), makeFinding(), makeFinding()];
    const decisions = { 0: "accepted", 1: "dismissed", 2: "accepted" } as const;

    const result = filterFindings(findings, decisions, "accepted");

    expect(result.map((r) => r.index)).toEqual([0, 2]);
  });

  it("filters to only 'dismissed' findings", () => {
    const findings = [makeFinding(), makeFinding()];
    const decisions = { 0: "dismissed", 1: "accepted" } as const;

    const result = filterFindings(findings, decisions, "dismissed");

    expect(result.map((r) => r.index)).toEqual([0]);
  });

  it("returns an empty array when no finding matches the filter", () => {
    const findings = [makeFinding(), makeFinding()];
    const decisions = { 0: "accepted", 1: "accepted" } as const;

    expect(filterFindings(findings, decisions, "dismissed")).toEqual([]);
  });
});
