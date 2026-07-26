import { Progress } from "@/components/ui/progress";
import type { Finding } from "@/types/finding";

interface ReviewProgressProps {
  findings: Finding[];
}

export function ReviewProgress({ findings }: ReviewProgressProps) {
  const reviewedCount = findings.reduce((count, finding) => {
    return finding.decision === "pending" ? count : count + 1;
  }, 0);

  const unreviewedUnverifiedCount = findings.reduce((count, finding) => {
    const isUnreviewedUnverified =
      finding.verification === "unverified" && finding.decision === "pending";
    return isUnreviewedUnverified ? count + 1 : count;
  }, 0);

  const percent = findings.length === 0 ? 0 : (reviewedCount / findings.length) * 100;

  return (
    <div className="mb-3 flex flex-col gap-1.5">
      <div className="flex items-center justify-between text-sm font-medium">
        <span>
          {reviewedCount} of {findings.length} reviewed
        </span>
      </div>
      <Progress value={percent} />
      {unreviewedUnverifiedCount > 0 && (
        <p className="text-xs text-severity-medium">
          {unreviewedUnverifiedCount} unverified finding{unreviewedUnverifiedCount === 1 ? "" : "s"}{" "}
          still need{unreviewedUnverifiedCount === 1 ? "s" : ""} your attention
        </p>
      )}
    </div>
  );
}
