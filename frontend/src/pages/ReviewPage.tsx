import { useParams } from "react-router-dom";
import { Disclaimer } from "../components/Disclaimer";
import { DocumentViewerPlaceholder } from "../components/DocumentViewerPlaceholder";
import { FindingCard } from "../components/FindingCard";
import findingsFixture from "../fixtures/findings.json";
import type { Finding } from "../types/finding";

// Fixture data only in this PR -- already pre-sorted verified-then-unverified
// per CONTRACTS.md §3(a), same as a real GET /jobs/{id}/findings response
// would be. No client-side re-sort here.
const findings = findingsFixture as Finding[];

export function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();

  return (
    <div className="flex h-screen flex-col">
      <Disclaimer />
      <header className="border-b border-slate-200 bg-white px-4 py-3">
        <h1 className="text-sm text-slate-500">
          Reviewing job <span className="font-mono text-slate-700">{jobId}</span>
        </h1>
      </header>
      <main className="flex min-h-0 flex-1">
        <section className="w-1/2 border-r border-slate-200 p-4">
          <DocumentViewerPlaceholder />
        </section>
        <section className="w-1/2 overflow-y-auto p-4">
          <div className="flex flex-col gap-3">
            {findings.map((finding, index) => (
              <FindingCard key={index} finding={finding} />
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
