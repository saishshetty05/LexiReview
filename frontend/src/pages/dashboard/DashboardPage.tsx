import { useNavigate } from "react-router-dom";
import { FileText, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AppShell } from "@/components/layout/AppShell";
import { useDocumentsQuery } from "@/hooks/queries";
import { isAuthError } from "@/hooks/useAuth";
import type { DocumentListItem } from "@/lib/api";
import type { Severity } from "@/types/finding";

// CLAUDE.md rule 7: the UI never says a document is "safe" -- this is the
// one allowed phrase, matching llm_client.py's REQUIRED_NO_ISSUES_PHRASE
// (also reused verbatim for an empty risk_snapshot in analysis_pipeline.py).
const NO_ISSUES_PHRASE = "no issues detected by automated review";

const SEVERITY_ORDER: Severity[] = ["high", "medium", "low", "info"];

const SEVERITY_BADGE_CLASSES: Record<Severity, string> = {
  high: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  medium: "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  low: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
  info: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
};

function SeverityCountBadges({ counts }: { counts: NonNullable<DocumentListItem["finding_counts"]> }) {
  const totalFindings = SEVERITY_ORDER.reduce(
    (sum, severity) => sum + counts[severity].verified + counts[severity].unverified,
    0
  );

  if (totalFindings === 0) {
    return <p className="text-xs text-green-600 dark:text-green-400">{NO_ISSUES_PHRASE}</p>;
  }

  return (
    <div className="flex flex-wrap gap-1.5" data-testid="severity-count-badges">
      {SEVERITY_ORDER.map((severity) => {
        const { verified, unverified } = counts[severity];
        const total = verified + unverified;
        if (total === 0) return null;
        return (
          <span
            key={severity}
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_BADGE_CLASSES[severity]}`}
          >
            {total} {severity[0].toUpperCase() + severity.slice(1)}
            {unverified > 0 && ` (${unverified} unverified)`}
          </span>
        );
      })}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(isoString: string): string {
  return new Date(isoString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function JobStateBadge({ state }: { state: string }) {
  const colors: Record<string, string> = {
    queued: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    running: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
    succeeded: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    failed: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${colors[state] || "bg-gray-100 text-gray-800"}`}>
      {state}
    </span>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const documentsQuery = useDocumentsQuery();

  if (documentsQuery.isError && isAuthError(documentsQuery.error)) {
    navigate("/login", { replace: true });
    return null;
  }

  const documents = documentsQuery.data ?? [];

  return (
    <AppShell title="Dashboard">
      <div className="flex h-full flex-col p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Recent Documents</h1>
            <p className="text-sm text-muted-foreground">
              Your last 5 uploaded contracts and their analysis status
            </p>
          </div>
          <Button onClick={() => navigate("/upload")}>
            <Upload className="mr-2 h-4 w-4" />
            Upload new
          </Button>
        </div>

        {documentsQuery.isLoading && (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        )}

        {documentsQuery.isError && (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-destructive">Failed to load documents. Please try again.</p>
          </div>
        )}

        {!documentsQuery.isLoading && !documentsQuery.isError && documents.length === 0 && (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
              <FileText className="h-8 w-8 text-muted-foreground" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">No documents yet</h2>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Upload a contract to get started. LexiReview will check it for risky clauses,
                missing terms, and internal inconsistencies.
              </p>
            </div>
            <Button onClick={() => navigate("/upload")}>
              <Upload className="mr-2 h-4 w-4" />
              Upload a document
            </Button>
          </div>
        )}

        {!documentsQuery.isLoading && !documentsQuery.isError && documents.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {documents.map((doc) => (
              <Card
                key={doc.doc_id}
                className="cursor-pointer transition-shadow hover:shadow-md"
                onClick={() => {
                  if (doc.latest_job) {
                    navigate(`/review/${doc.latest_job.job_id}`, { state: { docId: doc.doc_id } });
                  }
                }}
              >
                <CardHeader className="pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="line-clamp-1 text-base" title={doc.original_filename}>
                      {doc.original_filename}
                    </CardTitle>
                    {doc.latest_job && <JobStateBadge state={doc.latest_job.state} />}
                  </div>
                  <CardDescription className="text-xs">
                    {formatDate(doc.created_at)}
                    {doc.page_count !== null && ` • ${doc.page_count} pages`}
                    {" • "}
                    {formatBytes(doc.size_bytes)}
                  </CardDescription>
                </CardHeader>
                <CardContent className="pb-4 pt-0">
                  {!doc.latest_job && (
                    <p className="text-xs text-muted-foreground">No analysis started</p>
                  )}
                  {doc.latest_job?.state === "queued" && (
                    <p className="text-xs text-muted-foreground">Waiting to be analyzed...</p>
                  )}
                  {doc.latest_job?.state === "running" && (
                    <p className="text-xs text-muted-foreground">Analysis in progress...</p>
                  )}
                  {doc.latest_job?.state === "failed" && (
                    <p className="text-xs text-destructive">Analysis failed</p>
                  )}
                  {doc.latest_job?.state === "succeeded" && doc.finding_counts && (
                    <SeverityCountBadges counts={doc.finding_counts} />
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
