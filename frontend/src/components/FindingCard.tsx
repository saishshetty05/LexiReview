import type { Finding } from "../types/finding";
import { CategoryBadge } from "./CategoryBadge";
import { SeverityBadge } from "./SeverityBadge";

export function FindingCard({ finding }: { finding: Finding }) {
  const isUnverified = finding.verification === "unverified";

  return (
    <article
      className={`rounded-lg border p-4 shadow-sm ${
        isUnverified
          ? "border-amber-300 border-l-4 border-l-amber-500 bg-amber-50"
          : "border-slate-200 bg-white"
      }`}
    >
      {isUnverified && (
        <p className="mb-3 inline-block rounded bg-amber-500 px-2 py-1 text-xs font-bold uppercase tracking-wide text-white">
          Unverified — quote could not be matched to the source
        </p>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <CategoryBadge category={finding.category} />
        <SeverityBadge severity={finding.severity} />
        {finding.block_ids.map((blockId) => (
          <span
            key={blockId}
            className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-600"
          >
            {blockId}
          </span>
        ))}
      </div>

      {finding.evidence_quote && (
        <blockquote className="mb-2 border-l-2 border-slate-300 pl-3 text-sm italic text-slate-600">
          “{finding.evidence_quote}”
        </blockquote>
      )}

      <p className="text-sm text-slate-800">{finding.explanation}</p>
    </article>
  );
}
