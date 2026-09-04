import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, Upload as UploadIcon, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AppShell } from "@/components/layout/AppShell";
import { useUploadMutation } from "@/hooks/queries";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];

interface QuotaInfo {
  limit: number;
  used: number;
  resetsAt: string;
}

// CONTRACTS.md §11 (v1.17): "clear in-UI feedback", non-alarming -- a plain
// day count, not a countdown or urgency framing.
function daysUntil(isoString: string): number {
  const diffMs = new Date(isoString).getTime() - Date.now();
  return Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
}

function formatResetDate(isoString: string): string {
  return new Date(isoString).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function UploadPage() {
  const navigate = useNavigate();
  const uploadMutation = useUploadMutation();
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set only for the duplicate_document case, so the error banner can link
  // straight to the existing job instead of just refusing the upload.
  const [existingJobId, setExistingJobId] = useState<string | null>(null);
  // CONTRACTS.md §11 (v1.17): quota_exceeded is a distinct, non-alarming
  // state per the contract's own wording -- kept separate from `error`
  // (the generic destructive banner) rather than folded into it.
  const [quotaInfo, setQuotaInfo] = useState<QuotaInfo | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function pickFile(candidate: File | undefined | null) {
    if (!candidate) return;
    const hasValidExtension = ACCEPTED_EXTENSIONS.some((ext) =>
      candidate.name.toLowerCase().endsWith(ext),
    );
    if (!hasValidExtension) {
      setError("Please choose a PDF or DOCX file.");
      return;
    }
    setError(null);
    setExistingJobId(null);
    setFile(candidate);
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragActive(false);
    pickFile(event.dataTransfer.files?.[0]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError(null);
    setExistingJobId(null);
    try {
      const result = await uploadMutation.mutateAsync(file);
      navigate(`/review/${result.job_id}`, { state: { docId: result.doc_id }, replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.category === "missing_token" || err.category === "invalid_token") {
          navigate("/login", { replace: true });
          return;
        }
        if (
          err.category === "quota_exceeded" &&
          typeof err.detail?.limit === "number" &&
          typeof err.detail?.used === "number" &&
          typeof err.detail?.resets_at === "string"
        ) {
          setQuotaInfo({ limit: err.detail.limit, used: err.detail.used, resetsAt: err.detail.resets_at });
          return;
        }
        setError(err.message);
        if (err.category === "duplicate_document" && typeof err.detail?.job_id === "string") {
          setExistingJobId(err.detail.job_id);
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    }
  }

  return (
    <AppShell title="Upload a document">
      <div className="flex h-full items-center justify-center p-6">
        <Card className="w-full max-w-lg">
          <CardHeader>
            <CardTitle>Upload a contract</CardTitle>
            <CardDescription>PDF or DOCX, up to 100 pages.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              {quotaInfo && (
                <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                  You've used all {quotaInfo.used} of your {quotaInfo.limit} analyses this month.
                  Your quota resets on {formatResetDate(quotaInfo.resetsAt)} (
                  {daysUntil(quotaInfo.resetsAt) === 0 ? "today" : `in ${daysUntil(quotaInfo.resetsAt)} days`}
                  ).
                </p>
              )}

              {error && (
                <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">
                  {error}
                  {existingJobId && (
                    <>
                      {" "}
                      <button
                        type="button"
                        onClick={() => navigate(`/review/${existingJobId}`)}
                        className="underline underline-offset-2"
                      >
                        View its analysis
                      </button>
                    </>
                  )}
                </p>
              )}

              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                className={cn(
                  "flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed p-10 text-center transition-colors",
                  dragActive ? "border-primary bg-accent" : "border-border hover:bg-accent/50",
                )}
              >
                {file ? (
                  <>
                    <FileText className="h-8 w-8 text-primary" />
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {file.name}
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setFile(null);
                        }}
                        className="rounded-full p-0.5 hover:bg-muted"
                        aria-label="Remove file"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <UploadIcon className="h-8 w-8 text-muted-foreground" />
                    <p className="text-sm text-muted-foreground">
                      Drag and drop a file here, or click to browse
                    </p>
                  </>
                )}
                <input
                  ref={inputRef}
                  type="file"
                  accept=".pdf,.docx"
                  className="hidden"
                  onChange={(event) => pickFile(event.target.files?.[0])}
                />
              </div>

              <Button
                type="submit"
                disabled={uploadMutation.isPending || !file || quotaInfo !== null}
                className="w-full"
              >
                {uploadMutation.isPending ? "Uploading..." : "Analyze document"}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
