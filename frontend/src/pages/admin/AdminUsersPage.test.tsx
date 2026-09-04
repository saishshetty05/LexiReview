import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/hooks/useAuth";
import { ThemeProvider } from "@/hooks/useTheme";
import { AdminUsersPage } from "@/pages/admin/AdminUsersPage";

const { getCurrentUserMock, adminListUsersMock, adminSetUserActiveMock, toastSuccessMock, toastErrorMock } =
  vi.hoisted(() => ({
    getCurrentUserMock: vi.fn(),
    adminListUsersMock: vi.fn(),
    adminSetUserActiveMock: vi.fn(),
    toastSuccessMock: vi.fn(),
    toastErrorMock: vi.fn(),
  }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getCurrentUser: getCurrentUserMock,
    adminListUsers: adminListUsersMock,
    adminSetUserActive: adminSetUserActiveMock,
  };
});

vi.mock("sonner", () => ({ toast: { success: toastSuccessMock, error: toastErrorMock } }));

const SELF = { user_id: "admin-1", email: "admin@example.invalid", is_admin: true };
const OTHER_USER = {
  user_id: "user-2",
  email: "alice@example.invalid",
  is_admin: false,
  active: true,
  created_at: "2026-09-01T00:00:00+00:00",
};
const SELF_ROW = { user_id: "admin-1", email: SELF.email, is_admin: true, active: true, created_at: "2026-09-01T00:00:00+00:00" };

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/admin/users"]}>
          <AuthProvider>
            <AdminUsersPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("AdminUsersPage", () => {
  it("lists users and disables Suspend for the caller's own row", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    adminListUsersMock.mockResolvedValue([SELF_ROW, OTHER_USER]);
    renderPage();

    await screen.findByText("alice@example.invalid");
    const rows = screen.getAllByRole("row");
    const selfRow = rows.find((r) => r.textContent?.includes("admin@example.invalid"));
    const selfSuspendButton = selfRow?.querySelector("button");
    expect(selfSuspendButton).toBeDisabled();
  });

  it("suspends another user after confirming the dialog", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    adminListUsersMock.mockResolvedValue([SELF_ROW, OTHER_USER]);
    adminSetUserActiveMock.mockResolvedValue({ user_id: "user-2", active: false });
    renderPage();

    await screen.findByText("alice@example.invalid");
    const suspendButtons = screen.getAllByRole("button", { name: "Suspend" });
    fireEvent.click(suspendButtons.find((b) => !b.hasAttribute("disabled"))!);

    await screen.findByText(/Suspend alice@example.invalid\?/);
    fireEvent.click(screen.getByRole("button", { name: "Yes, suspend" }));

    await waitFor(() => expect(adminSetUserActiveMock).toHaveBeenCalledWith("user-2", false));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalled());
  });

  it("reactivates a suspended user without a confirmation dialog", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    adminListUsersMock.mockResolvedValue([SELF_ROW, { ...OTHER_USER, active: false }]);
    adminSetUserActiveMock.mockResolvedValue({ user_id: "user-2", active: true });
    renderPage();

    await screen.findByText("alice@example.invalid");
    fireEvent.click(screen.getByRole("button", { name: "Reactivate" }));

    await waitFor(() => expect(adminSetUserActiveMock).toHaveBeenCalledWith("user-2", true));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalled());
  });
});
