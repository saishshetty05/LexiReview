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

  it("does not clobber the enabled state when the mount-time status fetch resolves with stale data after verify-setup succeeds", async () => {
    // Regression test for the gap left after the first fix (PROJECT_STATUS.md
    // §5 / DECISION_LOG.md, 2026-08-23): the sync effect's guard only bailed
    // out for prev === "setup_qr"/"setup_verify", not "enabled" -- so if the
    // mount-time GET /auth/mfa/status is still in flight (carrying
    // pre-verification mfa_enabled: false) when verify-setup succeeds and
    // sets mfaState("enabled"), its late resolution fell through the guard
    // and clobbered the UI straight back to "disabled" right after the user
    // turned MFA on. Fixed by cancelling + invalidating the mfa-status query
    // in the verify-setup mutation's onSuccess so that superseded fetch's
    // result is discarded rather than applied.
    let resolveMountStatus: (value: { mfa_enabled: boolean; mfa_configured: boolean }) => void = () => {};
    mfaStatusMock.mockImplementationOnce(
      () => new Promise((resolve) => { resolveMountStatus = resolve; }),
    );
    // The real mfa-status value once verify-setup actually lands, used by
    // the refetch the verify-setup mutation's own invalidateQueries
    // triggers -- distinct from the mockImplementationOnce above so that
    // call resolves the mount fetch specifically, not whichever call
    // happens to run last.
    mfaStatusMock.mockResolvedValue({ mfa_enabled: true, mfa_configured: true });
    mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
    mfaVerifySetupMock.mockResolvedValue({ status: "mfa_enabled" });
    renderSettingsPage();
    await waitFor(() => expect(mfaStatusMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));
    await screen.findByAltText("MFA QR code");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.change(await screen.findByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and enable" }));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Two-factor authentication enabled"));
    expect(screen.getByText("Two-factor authentication is enabled")).toBeInTheDocument();
    // The verify-setup mutation's own invalidateQueries fires a fresh,
    // correct refetch (landing true) -- this is call #2.
    await waitFor(() => expect(mfaStatusMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Two-factor authentication is enabled")).toBeInTheDocument();

    // The mount-time fetch -- which should have been cancelled by then --
    // finally resolves with the stale pre-verification data. Its result
    // must be discarded, not applied on top of the already-correct
    // "enabled" state.
    await act(async () => {
      resolveMountStatus({ mfa_enabled: false, mfa_configured: false });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByText("Two-factor authentication is enabled")).toBeInTheDocument();
    expect(screen.queryByText("Two-factor authentication is not enabled.")).not.toBeInTheDocument();
  });

  it("does not clobber the disabled state when a fetch left in flight by verify-setup's own refetch resolves late after disable-mfa succeeds", async () => {
    // Reverse direction, chained one hop further: verify-setup's own
    // cancel+invalidate (tested above) fires a refetch that itself stays
    // in flight -- slow network, same as the original mount-time case --
    // right up until the user submits disable. The disable mutation's
    // cancelQueries must discard THAT fetch's stale "still enabled" result
    // too, not just the original mount-time one.
    let resolveMountStatus: (value: { mfa_enabled: boolean; mfa_configured: boolean }) => void = () => {};
    mfaStatusMock.mockImplementationOnce(
      () => new Promise((resolve) => { resolveMountStatus = resolve; }),
    );
    let resolvePostVerifyStatus: (value: { mfa_enabled: boolean; mfa_configured: boolean }) => void = () => {};
    mfaStatusMock.mockImplementationOnce(
      () => new Promise((resolve) => { resolvePostVerifyStatus = resolve; }),
    );
    // The real mfa-status value once disable actually lands, used by the
    // refetch the disable mutation's own invalidateQueries triggers.
    mfaStatusMock.mockResolvedValue({ mfa_enabled: false, mfa_configured: false });
    mfaSetupMock.mockResolvedValue({ qr_code_base64: "qrbase64data", totp_secret: "SECRETXYZ" });
    mfaVerifySetupMock.mockResolvedValue({ status: "mfa_enabled" });
    mfaDisableMock.mockResolvedValue({ status: "mfa_disabled" });
    renderSettingsPage();
    await waitFor(() => expect(mfaStatusMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Enable two-factor authentication" }));
    await screen.findByAltText("MFA QR code");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.change(await screen.findByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and enable" }));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Two-factor authentication enabled"));
    // verify-setup's own invalidateQueries fires the second (still-pending) fetch.
    await waitFor(() => expect(mfaStatusMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Two-factor authentication is enabled")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "654321" } });
    fireEvent.click(screen.getByRole("button", { name: "Disable two-factor authentication" }));
    await waitFor(() => expect(toastSuccessMock).toHaveBeenCalledWith("Two-factor authentication disabled"));
    expect(screen.getByText("Two-factor authentication is not enabled.")).toBeInTheDocument();
    // disable's own invalidateQueries fires a fresh, correct refetch (landing false).
    await waitFor(() => expect(mfaStatusMock).toHaveBeenCalledTimes(3));
    expect(screen.getByText("Two-factor authentication is not enabled.")).toBeInTheDocument();

    // The fetch verify-setup's invalidateQueries triggered -- which should
    // have been cancelled by the disable mutation by now -- finally
    // resolves with stale "still enabled" data. Its result must be
    // discarded, not applied on top of the already-correct "disabled" state.
    await act(async () => {
      resolvePostVerifyStatus({ mfa_enabled: true, mfa_configured: true });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByText("Two-factor authentication is not enabled.")).toBeInTheDocument();
    expect(screen.queryByText("Two-factor authentication is enabled")).not.toBeInTheDocument();

    // The original mount-time fetch, superseded twice over by now, is inert too.
    resolveMountStatus({ mfa_enabled: false, mfa_configured: false });
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
