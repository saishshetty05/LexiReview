import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/hooks/useAuth";
import { ThemeProvider } from "@/hooks/useTheme";
import { ApiError } from "@/lib/api";
import { LoginPage } from "@/pages/auth/LoginPage";

const { loginMock, mfaChallengeMock, navigateMock, toastErrorMock } = vi.hoisted(() => ({
  loginMock: vi.fn(),
  mfaChallengeMock: vi.fn(),
  navigateMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, login: loginMock, mfaChallenge: mfaChallengeMock };
});

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("sonner", () => ({ toast: { error: toastErrorMock, success: vi.fn() } }));

function renderLoginPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/login"]}>
          <AuthProvider>
            <LoginPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function submitCredentials(email = "alice@example.com", password = "hunter2222") {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
}

// Drives the page from a blank credentials form into the MFA step -- shared
// by every test below that needs to start there.
async function enterMfaStep() {
  loginMock.mockResolvedValue({ user_id: "", mfa_required: true, mfa_token: "pending-tok" });
  renderLoginPage();
  submitCredentials();
  await screen.findByLabelText("Authentication code");
}

beforeEach(() => {
  localStorage.clear();
  loginMock.mockReset();
  mfaChallengeMock.mockReset();
  navigateMock.mockReset();
  toastErrorMock.mockReset();
});

describe("LoginPage", () => {
  it("renders the credentials form initially, with no MFA code field", () => {
    renderLoginPage();

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authentication code")).not.toBeInTheDocument();
  });

  it("logs in directly and navigates to the dashboard when MFA is not required", async () => {
    loginMock.mockResolvedValue({ user_id: "u1" });
    renderLoginPage();

    submitCredentials("alice@example.com", "hunter2222");

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/dashboard", { replace: true }));
    expect(loginMock).toHaveBeenCalledWith("alice@example.com", "hunter2222");
    expect(localStorage.getItem("lexireview-known-logged-in")).toBe("true");
    expect(localStorage.getItem("lexireview-known-email")).toBe("alice@example.com");
  });

  it("shows the ApiError message when login fails, and does not navigate", async () => {
    loginMock.mockRejectedValue(new ApiError(401, "invalid_credentials", "Incorrect email or password."));
    renderLoginPage();

    submitCredentials();

    expect(await screen.findByText("Incorrect email or password.")).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
    expect(localStorage.getItem("lexireview-known-logged-in")).toBeNull();
  });

  it("transitions to the MFA challenge step when login returns mfa_required", async () => {
    await enterMfaStep();

    expect(screen.getByText("Two-factor authentication")).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    // Not logged in yet -- MFA hasn't been verified.
    expect(navigateMock).not.toHaveBeenCalled();
    expect(localStorage.getItem("lexireview-known-logged-in")).toBeNull();
  });

  it("sanitizes the MFA code input to digits only, capped at 6", async () => {
    await enterMfaStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "12a3bc4599" } });

    expect(screen.getByLabelText("Authentication code")).toHaveValue("123459");
  });

  it("keeps the verify button disabled until a full 6-digit code is entered", async () => {
    await enterMfaStep();

    const submitButton = screen.getByRole("button", { name: "Verify and log in" });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "12345" } });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    expect(submitButton).toBeEnabled();
  });

  it("submits a valid MFA code and navigates to the dashboard", async () => {
    mfaChallengeMock.mockResolvedValue({ user_id: "u1" });
    await enterMfaStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and log in" }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/dashboard", { replace: true }));
    expect(mfaChallengeMock).toHaveBeenCalledWith({ mfa_token: "pending-tok", code: "123456" }, expect.anything());
    expect(localStorage.getItem("lexireview-known-logged-in")).toBe("true");
  });

  it("pressing Enter with a complete code submits the MFA form", async () => {
    mfaChallengeMock.mockResolvedValue({ user_id: "u1" });
    await enterMfaStep();

    const codeInput = screen.getByLabelText("Authentication code");
    fireEvent.change(codeInput, { target: { value: "123456" } });
    fireEvent.keyDown(codeInput, { key: "Enter" });

    await waitFor(() => expect(mfaChallengeMock).toHaveBeenCalledWith({ mfa_token: "pending-tok", code: "123456" }, expect.anything()));
  });

  it("pressing Enter with an incomplete code does not submit", async () => {
    await enterMfaStep();

    const codeInput = screen.getByLabelText("Authentication code");
    fireEvent.change(codeInput, { target: { value: "123" } });
    fireEvent.keyDown(codeInput, { key: "Enter" });

    expect(mfaChallengeMock).not.toHaveBeenCalled();
  });

  it("shows a generic invalid-code error on a 401 and stays on the MFA step", async () => {
    mfaChallengeMock.mockRejectedValue(new ApiError(401, "invalid_mfa_code", "Invalid authentication code."));
    await enterMfaStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and log in" }));

    expect(await screen.findByText("Invalid code. Please try again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("on a 429 rate limit, toasts and resets back to the credentials step", async () => {
    mfaChallengeMock.mockRejectedValue(new ApiError(429, "mfa_rate_limited", "Too many failed attempts."));
    await enterMfaStep();

    fireEvent.change(screen.getByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and log in" }));

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledWith("Too many attempts. Please log in again."));
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authentication code")).not.toBeInTheDocument();
  });

  it("the Back to login button returns to the credentials step without submitting", async () => {
    await enterMfaStep();

    fireEvent.click(screen.getByRole("button", { name: "Back to login" }));

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authentication code")).not.toBeInTheDocument();
    expect(mfaChallengeMock).not.toHaveBeenCalled();
  });
});
