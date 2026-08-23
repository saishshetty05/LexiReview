import { describe, expect, it } from "vitest";

import { filterFindings } from "@/lib/filterFindings";
import type { Finding } from "@/types/finding";

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: "finding-1",
    decision: "pending",
    category: "missing_clause",
    severity: "medium",
    severity_override: null,
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
    expect(filterFindings(null, "all")).toEqual([]);
  });

  it("returns every finding, unfiltered, for filter='all' regardless of decision", () => {
    const findings = [
      makeFinding({ finding_id: "1", decision: "accepted" }),
      makeFinding({ finding_id: "2", decision: "dismissed" }),
      makeFinding({ finding_id: "3", decision: "pending" }),
    ];

    expect(filterFindings(findings, "all")).toEqual(findings);
  });

  it("filters to only 'pending' findings", () => {
    const findings = [
      makeFinding({ finding_id: "1", decision: "accepted" }),
      makeFinding({ finding_id: "2", decision: "pending" }),
    ];

    expect(filterFindings(findings, "pending")).toEqual([findings[1]]);
  });

  it("filters to only 'accepted' findings", () => {
    const findings = [
      makeFinding({ finding_id: "1", decision: "accepted" }),
      makeFinding({ finding_id: "2", decision: "dismissed" }),
      makeFinding({ finding_id: "3", decision: "accepted" }),
    ];

    expect(filterFindings(findings, "accepted")).toEqual([findings[0], findings[2]]);
  });

  it("filters to only 'dismissed' findings", () => {
    const findings = [
      makeFinding({ finding_id: "1", decision: "dismissed" }),
      makeFinding({ finding_id: "2", decision: "accepted" }),
    ];

    expect(filterFindings(findings, "dismissed")).toEqual([findings[0]]);
  });

  it("returns an empty array when no finding matches the filter", () => {
    const findings = [
      makeFinding({ finding_id: "1", decision: "accepted" }),
      makeFinding({ finding_id: "2", decision: "accepted" }),
    ];

    expect(filterFindings(findings, "dismissed")).toEqual([]);
  });
});
