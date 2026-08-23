// Mirrors CONTRACTS.md §2 (Findings JSON) exactly -- field names, casing,
// and allowed values are contract-locked and require both people's sign-off
// to change.
import type { Decision } from "@/types/decision";

export type FindingCategory =
  | "termination"
  | "liability"
  | "indemnity"
  | "payment"
  | "confidentiality"
  | "auto_renewal"
  | "governing_law"
  | "penalties"
  | "intellectual_property"
  | "arbitration"
  | "non_compete"
  | "force_majeure"
  | "inconsistency"
  | "missing_clause";

export type Severity = "high" | "medium" | "low" | "info";

export type Verification = "verified" | "unverified";

export type Confidence = "standard" | "needs_review";

export interface Finding {
  // CONTRACTS.md §7 (v1.7) envelope fields -- not part of the LLM-authored
  // §2 schema itself, added by GET /jobs/{id}/findings alongside it.
  finding_id: string;
  decision: Decision;
  category: FindingCategory;
  severity: Severity;
  /**
   * CONTRACTS.md §7a (v1.8): the reviewer's correction to `severity`, or
   * `null` if none has been set. `severity` itself is always the AI's
   * original judgment -- never mutated by an override.
   */
  severity_override: Severity | null;
  /**
   * CONTRACTS.md §2: EXACTLY 2 for category="inconsistency", exactly 1
   * otherwise, EMPTY for category="missing_clause" (there is nothing to
   * point at -- the finding is about absence). Do not "helpfully" backfill
   * this for missing_clause findings; an empty array is the correct,
   * contract-defined shape, not missing data.
   */
  block_ids: string[];
  /**
   * CONTRACTS.md §2: verbatim text from the document. For inconsistencies,
   * the two conflicting spans joined with " [...] ". For missing_clause:
   * always the empty string -- again, contract-defined, not missing data.
   */
  evidence_quote: string;
  explanation: string;
  verification: Verification;
  confidence: Confidence;
}
