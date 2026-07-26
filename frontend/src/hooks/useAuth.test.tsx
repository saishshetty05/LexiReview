import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { AuthProvider, isAuthError, useAuth } from "@/hooks/useAuth";

const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>;

beforeEach(() => {
  localStorage.clear();
});

describe("useAuth", () => {
  it("throws when used outside an AuthProvider", () => {
    // Swallow the expected React error-boundary console.error noise.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAuth())).toThrow(
      "useAuth must be used within an AuthProvider",
    );
    spy.mockRestore();
  });

  it("starts logged out with no email when localStorage is empty", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.isKnownLoggedIn).toBe(false);
    expect(result.current.email).toBeNull();
  });

  it("markLoggedIn sets both state and localStorage", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });

    act(() => {
      result.current.markLoggedIn("alice@example.com");
    });

    expect(result.current.isKnownLoggedIn).toBe(true);
    expect(result.current.email).toBe("alice@example.com");
    expect(localStorage.getItem("lexireview-known-logged-in")).toBe("true");
    expect(localStorage.getItem("lexireview-known-email")).toBe("alice@example.com");
  });

  it("clearSession clears both state and localStorage", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });

    act(() => {
      result.current.markLoggedIn("alice@example.com");
    });
    act(() => {
      result.current.clearSession();
    });

    expect(result.current.isKnownLoggedIn).toBe(false);
    expect(result.current.email).toBeNull();
    expect(localStorage.getItem("lexireview-known-logged-in")).toBeNull();
    expect(localStorage.getItem("lexireview-known-email")).toBeNull();
  });

  it("picks up a prior session already recorded in localStorage on mount", () => {
    localStorage.setItem("lexireview-known-logged-in", "true");
    localStorage.setItem("lexireview-known-email", "bob@example.com");

    const { result } = renderHook(() => useAuth(), { wrapper });

    expect(result.current.isKnownLoggedIn).toBe(true);
    expect(result.current.email).toBe("bob@example.com");
  });
});

describe("isAuthError", () => {
  it("is true for missing_token", () => {
    expect(isAuthError(new ApiError(401, "missing_token", "no token"))).toBe(true);
  });

  it("is true for invalid_token", () => {
    expect(isAuthError(new ApiError(401, "invalid_token", "bad token"))).toBe(true);
  });

  it("is false for other ApiError categories", () => {
    expect(isAuthError(new ApiError(409, "duplicate_document", "already exists"))).toBe(false);
  });

  it("is false for a non-ApiError value", () => {
    expect(isAuthError(new Error("network down"))).toBe(false);
    expect(isAuthError("not even an error")).toBe(false);
    expect(isAuthError(null)).toBe(false);
  });
});
