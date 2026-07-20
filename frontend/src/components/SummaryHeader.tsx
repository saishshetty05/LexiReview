import type { DocumentSummary } from "../types/summary";

const RISK_LABELS: { key: keyof DocumentSummary["risk_snapshot"]; style: string }[] = [
  { key: "high", style: "bg-red-100 text-red-800 border-red-300" },
  { key: "medium", style: "bg-amber-100 text-amber-800 border-amber-300" },
  { key: "low", style: "bg-blue-100 text-blue-800 border-blue-300" },
  { key: "info", style: "bg-gray-100 text-gray-700 border-gray-300" },
];

export function SummaryHeader({ summary }: { summary: DocumentSummary }) {
  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <p className="mb-3 text-sm text-slate-800">{summary.overview}</p>

      <div className="mb-3 flex flex-wrap gap-2">
        {RISK_LABELS.map(({ key, style }) => (
          <span
            key={key}
            className={`rounded-full border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${style}`}
          >
            {summary.risk_snapshot[key]} {key}
          </span>
        ))}
      </div>

      {summary.key_terms.length > 0 && (
        <dl className="grid grid-cols-1 gap-1 text-xs text-slate-600 sm:grid-cols-2">
          {summary.key_terms.map((term) => (
            <div key={term.label}>
              <dt className="inline font-semibold text-slate-700">{term.label}: </dt>
              <dd className="inline">{term.detail}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
