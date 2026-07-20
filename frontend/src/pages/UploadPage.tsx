import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppHeader } from "../components/AppHeader";
import { ApiError, uploadDocument } from "../lib/api";

export function UploadPage() {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set only for the duplicate_document case, so the error banner can link
  // straight to the existing job instead of just refusing the upload.
  const [existingJobId, setExistingJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError(null);
    setExistingJobId(null);
    setSubmitting(true);
    try {
      const result = await uploadDocument(file);
      navigate(`/review/${result.job_id}`, { state: { docId: result.doc_id }, replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.category === "missing_token" || err.category === "invalid_token") {
          navigate("/login", { replace: true });
          return;
        }
        setError(err.message);
        if (err.category === "duplicate_document" && typeof err.detail?.job_id === "string") {
          setExistingJobId(err.detail.job_id);
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-screen flex-col">
      <AppHeader title="Upload a document" />
      <main className="flex flex-1 items-center justify-center">
        <form
          onSubmit={handleSubmit}
          className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          {error && (
            <p className="mb-3 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
              {error}
              {existingJobId && (
                <>
                  {" "}
                  <button
                    type="button"
                    onClick={() => navigate(`/review/${existingJobId}`)}
                    className="underline"
                  >
                    View its analysis
                  </button>
                </>
              )}
            </p>
          )}

          <label className="mb-4 block text-sm">
            <span className="mb-1 block text-slate-600">Choose a PDF or DOCX file</span>
            <input
              type="file"
              accept=".pdf,.docx"
              required
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="w-full text-sm"
            />
          </label>

          <button
            type="submit"
            disabled={submitting || !file}
            className="w-full rounded bg-slate-800 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
          >
            {submitting ? "Uploading..." : "Submit"}
          </button>
        </form>
      </main>
    </div>
  );
}
