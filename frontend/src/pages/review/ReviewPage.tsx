import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { AppShell } from "@/components/layout/AppShell";
import { DocumentViewer } from "@/components/review/DocumentViewer";
import { FilterChips, type DecisionFilter } from "@/components/review/FilterChips";
import { FindingCard } from "@/components/review/FindingCard";
import { ReviewProgress } from "@/components/review/ReviewProgress";
import { SummaryHeader } from "@/components/review/SummaryHeader";
import { Disclaimer } from "@/components/shared/Disclaimer";
import { useDecisions } from "@/hooks/useDecisions";
import { useDocumentSummaryQuery, useJobFindingsQuery, useJobQuery } from "@/hooks/queries";
import { isAuthError } from "@/hooks/useAuth";
import { filterFindings } from "@/lib/filterFindings";

export function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  // Threaded through from the upload redirect's navigate(..., {state}) --
  // GET /jobs/{id}'s response shape is locked by CONTRACTS.md §3(a) and
  // doesn't include doc_id, so this is the only way this page can know
  // which document to ask for a summary/file. A direct visit/refresh loses
  // it; the summary block and document viewer just don't render in that
  // case (same "optional, skip silently" treatment as a 404).
  const docId = (location.state as { docId?: string } | null)?.docId ?? null;

  const [filter, setFilter] = useState<DecisionFilter>("all");
  const { decisions, setDecision } = useDecisions(jobId);

  const jobQuery = useJobQuery(jobId);
  const job = jobQuery.data;
  const jobSucceeded = job?.state === "succeeded";

  const findingsQuery = useJobFindingsQuery(jobId, jobSucceeded);
  const summaryQuery = useDocumentSummaryQuery(docId, jobSucceeded);

  const authRedirect = [jobQuery.error, findingsQuery.error].some((err) => {
    if (isAuthError(err)) {
      navigate("/login", { replace: true });
      return true;
    }
    return false;
  });

  const findings = findingsQuery.data ?? null;

  const filteredFindings = useMemo(
    () => filterFindings(findings, decisions, filter),
    [findings, decisions, filter],
  );

  if (authRedirect) return null;

  return (
    <AppShell title={`Reviewing job ${jobId}`}>
      <div className="flex h-full flex-col">
        <Disclaimer />
        <div className="flex min-h-0 flex-1">
          <section className="hidden w-1/2 border-r md:block">
            <DocumentViewer docId={docId} />
          </section>
          <section className="flex w-full flex-col overflow-y-auto p-4 md:w-1/2">
            {jobQuery.isError && (
              <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                Could not load this job.
              </p>
            )}

            {!jobQuery.isError && (!job || job.state === "queued" || job.state === "running") && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Analyzing document...
              </div>
            )}

            {!jobQuery.isError && job?.state === "failed" && (
              <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                Analysis failed
                {job.error ? `: ${job.error.category} — ${job.error.message}` : "."}
              </p>
            )}

            {jobSucceeded && findings === null && (
              <p className="text-sm text-muted-foreground">Loading results...</p>
            )}

            {jobSucceeded && findings !== null && (
              <>
                {summaryQuery.data && <SummaryHeader summary={summaryQuery.data} />}
                <ReviewProgress findings={findings} decisions={decisions} />
                <div className="mb-3">
                  <FilterChips value={filter} onChange={setFilter} />
                </div>
                <div className="flex flex-col gap-3">
                  {filteredFindings.length === 0 && (
                    <p className="text-sm text-muted-foreground">No findings match this filter.</p>
                  )}
                  {filteredFindings.map(({ finding, index }) => (
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
              </>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}
