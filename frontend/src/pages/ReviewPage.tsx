import { useState } from "react";
import { useParams } from "react-router-dom";
import { Disclaimer } from "../components/Disclaimer";
import { DocumentViewerPlaceholder } from "../components/DocumentViewerPlaceholder";
import { FindingCard } from "../components/FindingCard";
import { ReviewProgress } from "../components/ReviewProgress";
import findingsFixture from "../fixtures/findings.json";
import type { Decision } from "../types/decision";
import type { Finding } from "../types/finding";

// Fixture data only in this PR -- already pre-sorted verified-then-unverified
// per CONTRACTS.md §3(a), same as a real GET /jobs/{id}/findings response
// would be. No client-side re-sort here.
const findings = findingsFixture as Finding[];

export function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();

  // Local state only -- no API, no persistence, refresh clears decisions.
  // Keyed by array index: the fixture Finding[] has no id field (and
  // shouldn't gain one, per finding.ts's "mirrors CONTRACTS.md §2 exactly"),
  // and this slice never re-sorts or filters the underlying array, so index
  // is a stable key. Persistence (a decisions table + reviewer identity,
  // PRD FR-13) is a later PR.
  const [decisions, setDecisions] = useState<Record<number, Decision>>({});

  function setDecision(index: number, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [index]: decision }));
  }

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
          <ReviewProgress findings={findings} decisions={decisions} />
          <div className="flex flex-col gap-3">
            {findings.map((finding, index) => (
              <FindingCard
                key={index}
                finding={finding}
                decision={decisions[index] ?? "pending"}
                onAccept={() => setDecision(index, "accepted")}
                onDismiss={() => setDecision(index, "dismissed")}
                onUndo={() => setDecision(index, "pending")}
              />
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
