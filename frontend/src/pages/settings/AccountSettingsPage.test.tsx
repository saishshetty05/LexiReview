import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/hooks/useAuth";
import { ThemeProvider } from "@/hooks/useTheme";
import { AccountSettingsPage } from "@/pages/settings/AccountSettingsPage";

const { mfaStatusMock, mfaSetupMock, mfaVerifySetupMock, mfaDisableMock, toastSuccessMock, toastErrorMock } =
  vi.hoisted(() => ({
    mfaStatusMock: vi.fn(),
    mfaSetupMock: vi.fn(),
    mfaVerifySetupMock: vi.fn(),
    mfaDisableMock: vi.fn(),
    toastSuccessMock: vi.fn(),
    toastErrorMock: vi.fn(),
  }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    mfaStatus: mfaStatusMock,
    mfaSetup: mfaSetupMock,
    mfaVerifySetup: mfaVerifySetupMock,
    mfaDisable: mfaDisableMock,
  };
});

vi.mock("sonner", () => ({ toast: { success: toastSuccessMock, error: toastErrorMock } }));

function renderSettingsPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/settings"]}>
          <AuthProvider>
            <AccountSettingsPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

// mfaState's useState default is already "disabled" -- the same text these
// helpers wait for renders before the mfaStatus query resolves, so text
// alone can't distinguish "still loading" from "settled." Awaiting the
// exact promise the component's query is consuming (inside act()) is what
// actually guarantees the mfaState-sync effect has run before a test
// starts interacting -- otherwise a later-resolving query can clobber
// state a test already drove forward (e.g. reset setup_qr back to
// disabled), which is exactly the race this caused during development.
async function settleMfaStatusQuery() {
  const result = mfaStatusMock.mock.results.at(-1);
  if (result) await act(async () => { await result.value; });
}

async function renderDisabled() {
  mfaStatusMock.mockResolvedValue({ mfa_enabled: false, mfa_configured: false });
  renderSettingsPage();
  await settleMfaStatusQuery();
  await screen.findByText("Two-factor authentication is not enabled.");
}

async function renderEnabled() {
  mfaStatusMock.mockResolvedValue({ mfa_enabled: true, mfa_configured: true });
  renderSettingsPage();
  await settleMfaStatusQuery();
  await screen.findByText("Two-factor authentication is enabled");
}

// Drives from the disabled state through Enable -> QR/secret -> Next, into
// the setup_verify code-entry step -- shared by tests that start there.
async function enterSetupVerifyStep() {
  mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
  await renderDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));
  await screen.findByAltText("MFA QR code");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByLabelText("Authentication code");
}

beforeEach(() => {
  mfaStatusMock.mockReset();
  mfaSetupMock.mockReset();
  mfaVerifySetupMock.mockReset();
  mfaDisableMock.mockReset();
  toastSuccessMock.mockReset();
  toastErrorMock.mockReset();
});

describe("AccountSettingsPage MFA card", () => {
  it("shows the disabled state once MFA status loads as disabled", async () => {
    await renderDisabled();

    expect(screen.getByRole("button", { name: "Enable two-factor authentication" })).toBeInTheDocument();
  });

  it("shows the enabled state (with a disable form) once MFA status loads as enabled", async () => {
    await renderEnabled();

    expect(
      screen.getByRole("button", { name: "Disable two-factor authentication" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Enable two-factor authentication" })).not.toBeInTheDocument();
  });

  it("starting setup shows the QR code and manual-entry secret", async () => {
    mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
    await renderDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));

    const qr = await screen.findByAltText("MFA QR code");
    expect(qr).toHaveAttribute("src", "data:image/png;base64,qrbase64data");
    // "SECRETXYZ" is a text node next to a <strong> label, not its own
    // element -- assert on the shared container instead of exact text.
    expect(screen.getByText(/Secret \(manual entry\)/).closest("div")).toHaveTextContent("SECRETXYZ");
  });

  it("does not clobber an in-progress setup flow if the status query resolves after the click", async () => {
    // Regression test for a real race, not a contrived one: the mfaStatus
    // GET fires on mount and is still in flight (neither mock is manually
    // sequenced -- both just resolve on their own timing) when the user
    // clicks Enable. If that status fetch's resolution lands after the
    // setup mutation's, its effect must not clobber mfaState back to
    // "disabled" mid-setup (PROJECT_STATUS.md §5 / DECISION_LOG.md,
    // 2026-08-23) -- confirmed via direct tracing that this ordering (click
    // resolves before the still-pending initial status fetch) is exactly
    // what happens without waiting for renderDisabled()'s settle step first.
    mfaStatusMock.mockResolvedValue({ mfa_enabled: false, mfa_configured: false });
    mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
    renderSettingsPage();
    await screen.findByText("Two-factor authentication is not enabled.");

    fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));
    await waitFor(() => expect(mfaSetupMock).toHaveBeenCalled());
    // Let both the setup mutation's and the status query's own resolutions
    // (and the effects they trigger) fully settle before asserting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(screen.getByAltText("MFA QR code")).toBeInTheDocument();
    expect(screen.queryByText("Two-factor authentication is not enabled.")).not.toBeInTheDocument();
  });

  it("Cancel during the QR step returns to the disabled state", async () => {
    mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
    await renderDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));
    await screen.findByAltText("MFA QR code");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByText("Two-factor authentication is not enabled.")).toBeInTheDocument();
  });

  it("Next during the QR step moves to the verify-code step", async () => {
    await enterSetupVerifyStep();

    expect(screen.getByText("Enter the 6-digit code from your authenticator app to verify setup.")).toBeInTheDocument();
  });

  it("keeps the verify-and-enable button disabled until a full 6-digit code is entered", async () => {
    await enterSetupVerifyStep();

    const submitButton = screen.getByRole("button", { name: "Verify and enable" });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    expect(submitButton).toBeEnabled();
  });

  it("a valid verify-setup code enables MFA and shows a success toast", async () => {
    mfaVerifySetupMock.mockResolvedValue({ status: "mfa_enabled" });
    await enterSetupVerifyStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and enable" }));

    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Two-factor authentication enabled"));
    expect(mfaVerifySetupMock).toHaveBeenCalledWith({ code: "123456" }, expect.anything());
    expect(screen.getByText("Two-factor authentication is enabled")).toBeInTheDocument();
  });

  it("an invalid verify-setup code shows an error toast and stays on the verify step", async () => {
    mfaVerifySetupMock.mockRejectedValue(new Error("Invalid authentication code. Please try again."));
    await enterSetupVerifyStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and enable" }));

    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Invalid authentication code. Please try again."),
    );
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
  });

  it("a valid disable code disables MFA, shows a success toast, and reverts to the disabled state", async () => {
    mfaDisableMock.mockResolvedValue({ status: "mfa_disabled" });
    await renderEnabled();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Disable two-factor authentication" }));

    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Two-factor authentication disabled"));
    expect(mfaDisableMock).toHaveBeenCalledWith({ code: "654321" }, expect.anything());
    expect(screen.getByText("Two-factor authentication is not enabled.")).toBeInTheDocument();
  });

  it("an invalid disable code shows an error toast and stays enabled", async () => {
    mfaDisableMock.mockRejectedValue(new Error("Invalid authentication code. Please try again."));
    await renderEnabled();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Disable two-factor authentication" }));

    await waitFor(() =>
      expect(toastErrorMock).toHaveBeenCalledWith("Invalid authentication code. Please try again."),
    );
    expect(screen.getByRole("button", { name: "Disable two-factor authentication" })).toBeInTheDocument();
  });
});
