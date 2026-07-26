import { describe, expect, it } from "vitest";
import { formatCategory } from "@/lib/severity";

describe("formatCategory", () => {
  it("title-cases a single-word category", () => {
    expect(formatCategory("payment")).toBe("Payment");
  });

  it("splits underscores into separate title-cased words", () => {
    expect(formatCategory("missing_clause")).toBe("Missing Clause");
    expect(formatCategory("intellectual_property")).toBe("Intellectual Property");
    expect(formatCategory("auto_renewal")).toBe("Auto Renewal");
  });
});
