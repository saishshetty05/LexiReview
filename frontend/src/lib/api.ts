// Thin fetch wrapper for the real backend (CONTRACTS.md). Every request goes
// through the Vite dev proxy (vite.config.ts) as same-origin, credentials:
// 'include' on every call so the HttpOnly auth cookie rides along -- the
// token itself is never read or stored client-side, by design (SEC-6).
import type { Decision } from "../types/decision";
import type { Finding, Severity } from "../types/finding";
import type { DocumentSummary } from "../types/summary";

export interface DocumentListItem {
  doc_id: string;
  original_filename: string;
  file_type: "pdf" | "docx";
  page_count: number | null;
  size_bytes: number;
  created_at: string;
  latest_job: {
    job_id: string;
    state: "queued" | "running" | "succeeded" | "failed";
    created_at: string;
    finished_at: string | null;
  } | null;
}

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
  mfa_already_enabled: "Two-factor authentication is already enabled.",
  mfa_not_enabled: "Two-factor authentication is not enabled.",
  mfa_not_configured: "Two-factor authentication is not configured.",
  invalid_mfa_code: "Invalid authentication code. Please try again.",
  mfa_rate_limited: "Too many failed attempts. Please wait a few minutes and try again.",
  account_suspended: "This account has been suspended.",
  admin_required: "You don't have permission to view this page.",
  // CONTRACTS.md §11 (v1.17): fallback only -- upload_document always sends
  // its own message, and UploadPage builds a richer banner from
  // limit/used/resets_at directly, but every category still gets an entry
  // here (the exact gap #70's review caught for MFA).
  quota_exceeded: "Monthly analysis quota exceeded.",
  user_not_found: "That user no longer exists.",
  cannot_self_suspend: "You can't suspend your own account.",
};

// A 401 in one of these categories means the access token itself is
// unusable (missing or expired/invalid) -- worth one silent retry through
// /auth/refresh (CONTRACTS.md §9) before surfacing the error. Any other
// category (bad credentials, a wrong MFA code, MFA already enabled, ...) is
// a real answer to the request itself, not a stale session, and must not
// trigger a refresh attempt.
const SESSION_EXPIRED_CATEGORIES = new Set(["missing_token", "invalid_token"]);

// Paths exempt from the retry above even when the category matches:
// /auth/mfa/challenge's invalid_token means the short-lived mfa_pending
// token expired mid-2FA-flow, not that an established session went stale --
// there's no access-token session yet at that point to refresh. (There's no
// /auth/refresh entry here: refreshAccessToken() below calls a raw fetch,
// never request(), so recursion into this function isn't reachable in the
// first place.)
const NO_REFRESH_RETRY_PATHS = new Set(["/auth/mfa/challenge"]);

let refreshInFlight: Promise<boolean> | null = null;

// Exchanges the refresh_token cookie for a fresh access token + rotated
// refresh token. Concurrent 401s share one in-flight attempt instead of
// each firing their own -- rotation is single-use, so a second concurrent
// call would find the first call's token already consumed and, per the
// backend's reuse-detection design, revoke every outstanding refresh token
// for the user, forcing a full re-login.
function refreshAccessToken(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch("/auth/refresh", { method: "POST", credentials: "include" })
      .then((resp) => resp.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

async function parseErrorResponse(
  resp: Response,
): Promise<{ category: string; message: string; detailObj?: Record<string, unknown> }> {
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
  return { category, message, detailObj };
}

async function request<T>(path: string, options: RequestInit = {}, isRetry = false): Promise<T> {
  const resp = await fetch(path, { ...options, credentials: "include" });

  if (!resp.ok) {
    const { category, message, detailObj } = await parseErrorResponse(resp);

    if (
      !isRetry &&
      SESSION_EXPIRED_CATEGORIES.has(category) &&
      !NO_REFRESH_RETRY_PATHS.has(path) &&
      (await refreshAccessToken())
    ) {
      return request<T>(path, options, true);
    }
    throw new ApiError(resp.status, category, message, detailObj);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface LoginResult {
  user_id: string;
  // Present when MFA is enabled for the user. The mfa_pending_token is a
  // short-lived JWT (5 min) that must be exchanged via /auth/mfa/challenge
  // with a valid TOTP code to get the real access token cookie.
  mfa_required?: boolean;
  mfa_token?: string;
}

export function login(email: string, password: string): Promise<LoginResult> {
  return request<LoginResult>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export interface MFASetupResult {
  qr_code_base64: string; // base64 PNG
  totp_secret: string; // raw TOTP secret (base32) for manual entry
}

export function mfaSetup(): Promise<MFASetupResult> {
  return request<MFASetupResult>("/auth/mfa/setup", { method: "POST" });
}

export interface MFAVerifySetupRequest {
  code: string; // 6-digit TOTP
}

export function mfaVerifySetup(body: MFAVerifySetupRequest): Promise<{ status: string }> {
  return request("/auth/mfa/verify-setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface MFAStatusResult {
  mfa_enabled: boolean;
  mfa_configured: boolean;
}

export function mfaStatus(): Promise<MFAStatusResult> {
  return request<MFAStatusResult>("/auth/mfa/status");
}

export interface MFADisableRequest {
  code: string; // 6-digit TOTP
}

export function mfaDisable(body: MFADisableRequest): Promise<{ status: string }> {
  return request("/auth/mfa/disable", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface MFAChallengeRequest {
  mfa_token: string;
  code: string; // 6-digit TOTP
}

export function mfaChallenge(body: MFAChallengeRequest): Promise<LoginResult> {
  return request<LoginResult>("/auth/mfa/challenge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export interface RegisterResult {
  user_id: string;
  email: string;
}

export function register(email: string, password: string): Promise<RegisterResult> {
  return request<RegisterResult>("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export function deleteAccount(): Promise<{ status: string }> {
  return request("/auth/account", { method: "DELETE" });
}

export function logout(): Promise<{ status: string }> {
  // CONTRACTS.md §9 v1.12: /auth/refresh/revoke does the whole logout job in
  // one round trip -- it revokes the refresh token server-side (a no-op when
  // the cookie is already gone) and clears both cookies via delete_cookie.
  // There is no separate /auth/logout call: the refresh_token cookie is
  // path-scoped to /auth/refresh, so RFC 6265 never attaches it to
  // /auth/logout anyway, and revoke already performs the client-side clear.
  // The endpoint is idempotent (a missing/already-revoked cookie is still a
  // 200), so a stale or missing token can never block logout -- only a
  // genuine transport/5xx failure propagates (DECISION_LOG 2026-08-30;
  // CONTRACTS.md §9).
  return request("/auth/refresh/revoke", { method: "POST" });
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

export interface DecisionResult {
  finding_id: string;
  decision: Decision;
  severity_override: Severity | null;
}

// CONTRACTS.md §7 (v1.7) / §7a (v1.8): upserts the caller's decision for one
// finding. severityOverride is tri-state on the wire: omitted here (the
// default) leaves any existing override untouched, `null` clears it, a
// value sets it -- distinguished by whether the argument was passed at all,
// same "absent vs. sent as null" split the backend makes via
// model_fields_set (see put_finding_decision in main.py).
export function putFindingDecision(
  jobId: string,
  findingId: string,
  decision: Decision,
  severityOverride?: Severity | null,
): Promise<DecisionResult> {
  const body: { decision: Decision; severity_override?: Severity | null } = { decision };
  if (severityOverride !== undefined) body.severity_override = severityOverride;
  return request<DecisionResult>(`/jobs/${jobId}/findings/${findingId}/decision`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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

// GET /documents/{id}/file -- backs the real DocumentViewer. Not wrapped by
// request<T>() since the response is a binary body, not JSON: same
// credentials/error-shape handling, but resolves to a Blob (+ the
// Content-Type the backend set from documents.file_type) instead of parsed
// JSON.
export interface DocumentFile {
  blob: Blob;
  contentType: string;
}

async function fetchDocumentFile(docId: string, isRetry: boolean): Promise<DocumentFile> {
  const path = `/documents/${docId}/file`;
  const resp = await fetch(path, { credentials: "include" });
  if (!resp.ok) {
    const { category, message, detailObj } = await parseErrorResponse(resp);

    if (!isRetry && SESSION_EXPIRED_CATEGORIES.has(category) && (await refreshAccessToken())) {
      return fetchDocumentFile(docId, true);
    }
    throw new ApiError(resp.status, category, message, detailObj);
  }
  const blob = await resp.blob();
  return { blob, contentType: resp.headers.get("Content-Type") ?? "application/octet-stream" };
}

export function getDocumentFile(docId: string): Promise<DocumentFile> {
  return fetchDocumentFile(docId, false);
}

export function getDocuments(): Promise<DocumentListItem[]> {
  return request<DocumentListItem[]>("/documents");
}

// CONTRACTS.md §3(c) (v1.16): the caller's own identity, including is_admin
// -- backs the admin-nav-visibility check, since there was previously no
// server-truth endpoint for this at all.
export interface CurrentUser {
  user_id: string;
  email: string;
  is_admin: boolean;
}

export function getCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/me");
}

// CONTRACTS.md §10 (v1.14): admin-only user management.
export interface AdminUserRow {
  user_id: string;
  email: string;
  is_admin: boolean;
  active: boolean;
  created_at: string;
}

export function adminListUsers(): Promise<AdminUserRow[]> {
  return request<AdminUserRow[]>("/admin/users");
}

export function adminSetUserActive(userId: string, active: boolean): Promise<{ user_id: string; active: boolean }> {
  return request(`/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
}
