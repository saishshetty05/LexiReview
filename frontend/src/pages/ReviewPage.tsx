import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { AppHeader } from "../components/AppHeader";
import { Disclaimer } from "../components/Disclaimer";
import { DocumentViewerPlaceholder } from "../components/DocumentViewerPlaceholder";
import { FindingCard } from "../components/FindingCard";
import { ReviewProgress } from "../components/ReviewProgress";
import { SummaryHeader } from "../components/SummaryHeader";
import {
  ApiError,
  type JobStatus,
  getDocumentSummary,
  getJob,
  getJobFindings,
} from "../lib/api";
import type { Decision } from "../types/decision";
import type { Finding } from "../types/finding";
import type { DocumentSummary } from "../types/summary";

const POLL_INTERVAL_MS = 3000;

export function ReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  // Threaded through from the upload redirect's navigate(..., {state}) --
  // GET /jobs/{id}'s response shape is locked by CONTRACTS.md §3(a) and
  // doesn't include doc_id, so this is the only way this page can know
  // which document to ask for a summary. A direct visit/refresh loses it;
  // the summary block just doesn't render in that case (same "optional,
  // skip silently" treatment as a 404).
  const docId = (location.state as { docId?: string } | null)?.docId ?? null;

  const [job, setJob] = useState<JobStatus | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [summary, setSummary] = useState<DocumentSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<number, Decision>>({});

  function setDecision(index: number, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [index]: decision }));
  }

  // Poll GET /jobs/{id} every 3s per CONTRACTS.md §3(a), self-scheduling so
  // it naturally stops once the job reaches a terminal state -- no interval
  // to remember to clear on success/failure, only on unmount.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      try {
        const status = await getJob(jobId as string);
        if (cancelled) return;
        setJob(status);
        if (status.state === "queued" || status.state === "running") {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && (err.category === "missing_token" || err.category === "invalid_token")) {
          navigate("/login", { replace: true });
          return;
        }
        setLoadError(err instanceof ApiError ? err.message : "Could not load this job.");
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, navigate]);

  // Once succeeded, fetch findings (required) and the summary (optional,
  // silently absent on 404 or any other failure -- it must never block or
  // degrade the findings the user actually came here for) exactly once.
  useEffect(() => {
    if (!jobId || job?.state !== "succeeded" || findings !== null) return;
    let cancelled = false;

    async function loadResults() {
      try {
        const results = await getJobFindings(jobId as string);
        if (!cancelled) setFindings(results);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : "Could not load findings.");
        }
      }
      if (docId) {
        try {
          const result = await getDocumentSummary(docId);
          if (!cancelled) setSummary(result);
        } catch {
          // Optional -- swallow any failure here too, not just the expected 404.
        }
      }
    }

    loadResults();
    return () => {
      cancelled = true;
    };
  }, [jobId, job?.state, findings, docId]);

  return (
    <div className="flex h-screen flex-col">
      <Disclaimer />
      <AppHeader title={`Reviewing job ${jobId}`} />
      <main className="flex min-h-0 flex-1">
        <section className="w-1/2 border-r border-slate-200 p-4">
          <DocumentViewerPlaceholder />
        </section>
        <section className="w-1/2 overflow-y-auto p-4">
          {loadError && (
            <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              {loadError}
            </p>
          )}

          {!loadError && (!job || job.state === "queued" || job.state === "running") && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <span
                className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600"
                aria-hidden="true"
              />
              Analyzing document...
            </div>
          )}

          {!loadError && job?.state === "failed" && (
            <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              Analysis failed
              {job.error ? `: ${job.error.category} — ${job.error.message}` : "."}
            </p>
          )}

          {!loadError && job?.state === "succeeded" && findings === null && (
            <p className="text-sm text-slate-500">Loading results...</p>
          )}

          {!loadError && job?.state === "succeeded" && findings !== null && (
            <>
              {summary && <SummaryHeader summary={summary} />}
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
            </>
          )}
        </section>
      </main>
    </div>
  );
}
