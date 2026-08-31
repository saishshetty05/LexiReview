import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getDocumentFile, getJob, login, logout, putFindingDecision } from "@/lib/api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("logout() revokes server-side before the client-side clear", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls /auth/refresh/revoke first, then /auth/logout, and returns the logout body", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { status: "logged_out" })) // revoke
      .mockResolvedValueOnce(jsonResponse(200, { status: "logged_out" })); // logout

    const result = await logout();

    expect(result).toEqual({ status: "logged_out" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/auth/refresh/revoke");
    expect(fetchMock.mock.calls[1][0]).toBe("/auth/logout");
  });

  it("still completes the client-side clear when revoke fails with a network error (fetch rejects)", async () => {
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch")) // revoke network error
      .mockResolvedValueOnce(jsonResponse(200, { status: "logged_out" })); // logout

    const result = await logout();

    // Best-effort revoke must never block the logout clear.
    expect(result).toEqual({ status: "logged_out" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("still completes the client-side clear when revoke returns a 5xx (request() throws an ApiError)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(500, { detail: { category: "http_500" } })) // revoke error
      .mockResolvedValueOnce(jsonResponse(200, { status: "logged_out" })); // logout

    const result = await logout();

    expect(result).toEqual({ status: "logged_out" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("request() refresh-retry (CONTRACTS.md §9)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("retries once through /auth/refresh on a missing_token 401, then succeeds", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "missing_token" } }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // /auth/refresh
      .mockResolvedValueOnce(
        jsonResponse(200, {
          job_id: "j1",
          state: "queued",
          retry_count: 0,
          created_at: "t",
          started_at: null,
          finished_at: null,
          error: null,
        }),
      );

    const result = await getJob("j1");

    expect(result.job_id).toBe("j1");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe("/auth/refresh");
    expect(fetchMock.mock.calls[2][0]).toBe("/jobs/j1");
  });

  it("does not retry a second time and surfaces the original error if refresh itself fails", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } })); // /auth/refresh fails

    await expect(getJob("j1")).rejects.toMatchObject({ category: "invalid_token" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not attempt a refresh for a non-session error category", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { detail: { category: "invalid_credentials" } }),
    );

    await expect(login("a@b.com", "password")).rejects.toMatchObject({
      category: "invalid_credentials",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("dedups concurrent refresh attempts behind one in-flight /auth/refresh call", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } })) // request A
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } })) // request B
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // shared /auth/refresh
      .mockResolvedValueOnce(
        jsonResponse(200, {
          job_id: "a",
          state: "queued",
          retry_count: 0,
          created_at: "t",
          started_at: null,
          finished_at: null,
          error: null,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          job_id: "b",
          state: "queued",
          retry_count: 0,
          created_at: "t",
          started_at: null,
          finished_at: null,
          error: null,
        }),
      );

    const [a, b] = await Promise.all([getJob("a"), getJob("b")]);

    expect(a.job_id).toBe("a");
    expect(b.job_id).toBe("b");
    expect(fetchMock).toHaveBeenCalledTimes(5);
    const refreshCalls = fetchMock.mock.calls.filter((c) => c[0] === "/auth/refresh");
    expect(refreshCalls).toHaveLength(1);
  });

  it("skips the retry for /auth/mfa/challenge's own invalid_token (mfa_pending token expiry, not a stale session)", async () => {
    const { mfaChallenge } = await import("@/lib/api");
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } }));

    await expect(mfaChallenge({ mfa_token: "stale", code: "123456" })).rejects.toMatchObject({
      category: "invalid_token",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("getDocumentFile retries through refresh on a 401 for the binary fetch path too", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // /auth/refresh
      .mockResolvedValueOnce(
        new Response("bytes", {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
      );

    const result = await getDocumentFile("doc1");

    expect(result.contentType).toBe("application/pdf");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("preserves the original method and body on the retried request (PUT with a JSON body)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // /auth/refresh
      .mockResolvedValueOnce(
        jsonResponse(200, { finding_id: "f1", decision: "accepted", severity_override: null }),
      );

    const result = await putFindingDecision("j1", "f1", "accepted");

    expect(result.decision).toBe("accepted");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const [firstCall, , retryCall] = fetchMock.mock.calls;
    expect(retryCall[0]).toBe(firstCall[0]);
    expect(retryCall[1]).toMatchObject({ method: "PUT", body: firstCall[1].body });
  });

  it("throws a plain ApiError (no infinite loop) when even the retried request 401s again", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } }))
      .mockResolvedValueOnce(jsonResponse(200, { status: "ok" })) // /auth/refresh succeeds
      .mockResolvedValueOnce(jsonResponse(401, { detail: { category: "invalid_token" } })); // retried request still 401s

    await expect(getJob("j1")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});