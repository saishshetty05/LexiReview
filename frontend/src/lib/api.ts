// Thin fetch wrapper for the real backend (CONTRACTS.md). Every request goes
// through the Vite dev proxy (vite.config.ts) as same-origin, credentials:
// 'include' on every call so the HttpOnly auth cookie rides along -- the
// token itself is never read or stored client-side, by design (SEC-6).
import type { Finding } from "../types/finding";
import type { DocumentSummary } from "../types/summary";

export class ApiError extends Error {
  status: number;
  category: string;
  // Raw `detail` object when the backend sent one (e.g. upload's 409
  // duplicate_document, which carries doc_id/job_id alongside category and
  // message) -- undefined for plain-string details and network failures.
  detail?: Record<string, unknown>;

  constructor(status: number, category: string, message: string, detail?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.category = category;
    this.detail = detail;
  }
}

// Covers error paths whose response body carries only a category, no
// message (app/deps.py's auth errors -- see _auth_error_response, which
// deliberately omits message) or a plain-string `detail` with no category
// at all (main.py's 404s on /jobs and /documents/*/summary). Every other
// error path (upload's 409/413/415/422/500) already sends its own message
// and is used as-is.
const CATEGORY_MESSAGES: Record<string, string> = {
  invalid_credentials: "Incorrect email or password.",
  rate_limited: "Too many attempts. Please wait a few minutes and try again.",
  invalid_email: "Please enter a valid email address.",
  invalid_password: "Password must be 8-72 characters.",
  missing_token: "You're not logged in.",
  invalid_token: "Your session has expired. Please log in again.",
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, { ...options, credentials: "include" });

  if (!resp.ok) {
    let category = `http_${resp.status}`;
    let message = `Request failed (${resp.status}).`;
    let detailObj: Record<string, unknown> | undefined;
    try {
      const body = await resp.json();
      const detail = body?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (detail && typeof detail === "object") {
        detailObj = detail;
        category = detail.category ?? category;
        message = detail.message ?? CATEGORY_MESSAGES[category] ?? message;
      }
    } catch {
      // Non-JSON or empty error body -- keep the generic fallback above.
    }
    throw new ApiError(resp.status, category, message, detailObj);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface LoginResult {
  user_id: string;
}

export function login(email: string, password: string): Promise<LoginResult> {
  return request<LoginResult>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<{ status: string }> {
  return request("/auth/logout", { method: "POST" });
}

export interface UploadResult {
  doc_id: string;
  job_id: string;
  state: string;
}

export function uploadDocument(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  // No Content-Type header -- the browser sets multipart/form-data with the
  // correct boundary itself; setting it manually breaks the boundary.
  return request<UploadResult>("/documents/upload", { method: "POST", body: form });
}

export interface JobStatus {
  job_id: string;
  state: "queued" | "running" | "succeeded" | "failed";
  retry_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { category: string; message: string } | null;
}

export function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}`);
}

export function getJobFindings(jobId: string): Promise<Finding[]> {
  return request<Finding[]>(`/jobs/${jobId}/findings`);
}

// Returns null on 404 rather than throwing -- CONTRACTS.md §2c: the summary
// is supplementary, and (as of this PR) nothing has ever written one yet, so
// a 404 here is the expected common case, not an error to surface.
export async function getDocumentSummary(docId: string): Promise<DocumentSummary | null> {
  try {
    return await request<DocumentSummary>(`/documents/${docId}/summary`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
