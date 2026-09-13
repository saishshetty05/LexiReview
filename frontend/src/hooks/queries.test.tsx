import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useLoginMutation, useMFAChallengeMutation } from "@/hooks/queries";

const { loginMock, mfaChallengeMock } = vi.hoisted(() => ({
  loginMock: vi.fn(),
  mfaChallengeMock: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, login: loginMock, mfaChallenge: mfaChallengeMock };
});

// Regression test for the same-tab identity-switch bug: signing into a
// second account in one browser tab (e.g. registering a new user right
// after being logged in as an admin) left the previous user's cached
// current-user/admin-users/etc. query results in place, since nothing told
// TanStack Query the identity behind those cache entries had changed --
// AdminUsersPage would then read the *old* user's is_admin/user_id for up
// to staleTime (60s). useLoginMutation/useMFAChallengeMutation now clear the
// whole cache on a successful login.
describe("useLoginMutation", () => {
  it("clears every cached query on successful login", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Simulate stale data left behind by a previous session in this tab.
    queryClient.setQueryData(["current-user"], { user_id: "old-admin", email: "admin@example.invalid", is_admin: true });
    queryClient.setQueryData(["admin-users"], [{ user_id: "old-admin", email: "admin@example.invalid" }]);
    loginMock.mockResolvedValue({ user_id: "new-user" });

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useLoginMutation(), { wrapper });

    result.current.mutate({ email: "new@example.invalid", password: "password123" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryData(["current-user"])).toBeUndefined();
    expect(queryClient.getQueryData(["admin-users"])).toBeUndefined();
  });
});

describe("useMFAChallengeMutation", () => {
  it("clears every cached query on successful MFA-completed login", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["current-user"], { user_id: "old-admin", email: "admin@example.invalid", is_admin: true });
    mfaChallengeMock.mockResolvedValue({ user_id: "new-user" });

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useMFAChallengeMutation(), { wrapper });

    result.current.mutate({ mfa_token: "token", code: "123456" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryData(["current-user"])).toBeUndefined();
  });
});
