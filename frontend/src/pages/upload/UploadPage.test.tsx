import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/hooks/useAuth";
import { ThemeProvider } from "@/hooks/useTheme";
import { ApiError } from "@/lib/api";
import { UploadPage } from "@/pages/upload/UploadPage";

const { uploadDocumentMock } = vi.hoisted(() => ({ uploadDocumentMock: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, uploadDocument: uploadDocumentMock };
});

function renderUploadPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/upload"]}>
          <AuthProvider>
            <UploadPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function pickFile() {
  const file = new File(["contract text"], "lease.pdf", { type: "application/pdf" });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("UploadPage quota handling (CONTRACTS.md §11, v1.17)", () => {
  it("shows a non-alarming quota banner and disables submit on 429 quota_exceeded", async () => {
    uploadDocumentMock.mockRejectedValue(
      new ApiError(429, "quota_exceeded", "Monthly analysis quota exceeded.", {
        limit: 200,
        used: 200,
        resets_at: "2026-10-01T00:00:00+00:00",
      }),
    );
    renderUploadPage();

    pickFile();
    fireEvent.click(screen.getByRole("button", { name: "Analyze document" }));

    // Date order ("Oct 1" vs "1 Oct") is locale-dependent (toLocaleDateString,
    // same characteristic DashboardPage.tsx's formatDate already has) --
    // assert on the day-count clause instead of a specific date string shape.
    await screen.findByText(/used all 200 of your 200 analyses this month/);
    expect(screen.getByText(/resets on/)).toBeInTheDocument();
    expect(screen.getByText(/in \d+ days/)).toBeInTheDocument();

    const submitButton = screen.getByRole("button", { name: "Analyze document" });
    expect(submitButton).toBeDisabled();
  });

  it("does not show the quota banner for a non-quota error (regression)", async () => {
    uploadDocumentMock.mockRejectedValue(
      new ApiError(409, "duplicate_document", "A document with this content has already been uploaded.", {
        doc_id: "doc-1",
        job_id: "job-1",
      }),
    );
    renderUploadPage();

    pickFile();
    fireEvent.click(screen.getByRole("button", { name: "Analyze document" }));

    await screen.findByText("A document with this content has already been uploaded.");
    expect(screen.queryByText(/analyses this month/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyze document" })).not.toBeDisabled();
  });

  it("navigates to the review page on a successful upload (regression)", async () => {
    uploadDocumentMock.mockResolvedValue({ doc_id: "doc-1", job_id: "job-1", state: "queued" });
    renderUploadPage();

    pickFile();
    fireEvent.click(screen.getByRole("button", { name: "Analyze document" }));

    await waitFor(() => expect(uploadDocumentMock).toHaveBeenCalled());
  });
});
