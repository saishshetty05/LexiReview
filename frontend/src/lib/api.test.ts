import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { logout } from "@/lib/api";

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
