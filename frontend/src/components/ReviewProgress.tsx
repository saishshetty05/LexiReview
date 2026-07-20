import type { Decision } from "../types/decision";
import type { Finding } from "../types/finding";

interface ReviewProgressProps {
  findings: Finding[];
  decisions: Record<number, Decision>;
}

export function ReviewProgress({ findings, decisions }: ReviewProgressProps) {
  const reviewedCount = findings.reduce((count, _finding, index) => {
    const decision = decisions[index] ?? "pending";
    return decision === "pending" ? count : count + 1;
  }, 0);

  const unreviewedUnverifiedCount = findings.reduce((count, finding, index) => {
    const decision = decisions[index] ?? "pending";
    const isUnreviewedUnverified = finding.verification === "unverified" && decision === "pending";
    return isUnreviewedUnverified ? count + 1 : count;
  }, 0);

  return (
    <div className="mb-3">
      <p className="text-sm font-medium text-slate-700">
        {reviewedCount} of {findings.length} reviewed
      </p>
      {unreviewedUnverifiedCount > 0 && (
        <p className="text-xs text-amber-700">
          {unreviewedUnverifiedCount} unverified finding{unreviewedUnverifiedCount === 1 ? "" : "s"}{" "}
          still need{unreviewedUnverifiedCount === 1 ? "s" : ""} your attention
        </p>
      )}
    </div>
  );
}
