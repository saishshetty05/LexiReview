import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/hooks/useAuth";
import { ThemeProvider } from "@/hooks/useTheme";
import type { DocumentListItem } from "@/lib/api";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";

const { getCurrentUserMock, getDocumentsMock } = vi.hoisted(() => ({
  getCurrentUserMock: vi.fn(),
  getDocumentsMock: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getCurrentUser: getCurrentUserMock,
    getDocuments: getDocumentsMock,
  };
});

const SELF = { user_id: "user-1", email: "user@example.invalid", is_admin: false };

function baseDoc(overrides: Partial<DocumentListItem>): DocumentListItem {
  return {
    doc_id: "doc-1",
    original_filename: "lease.pdf",
    file_type: "pdf",
    page_count: 3,
    size_bytes: 1024,
    created_at: "2026-09-20T00:00:00+00:00",
    latest_job: {
      job_id: "job-1",
      state: "succeeded",
      created_at: "2026-09-20T00:00:00+00:00",
      finished_at: "2026-09-20T00:01:00+00:00",
    },
    finding_counts: null,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dashboard"]}>
          <AuthProvider>
            <DashboardPage />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe("DashboardPage severity-count badges", () => {
  it("renders a badge per non-zero severity, with the unverified count called out", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    getDocumentsMock.mockResolvedValue([
      baseDoc({
        finding_counts: {
          high: { verified: 2, unverified: 1 },
          medium: { verified: 0, unverified: 1 },
          low: { verified: 0, unverified: 0 },
          info: { verified: 1, unverified: 0 },
        },
      }),
    ]);
    renderPage();

    expect(await screen.findByText(/3 High \(1 unverified\)/)).toBeInTheDocument();
    expect(screen.getByText(/1 Medium \(1 unverified\)/)).toBeInTheDocument();
    expect(screen.getByText(/1 Info/)).toBeInTheDocument();
    expect(screen.queryByText(/Low/)).not.toBeInTheDocument();
  });

  it("renders the required rule-7 phrase, never a checkmark, when all counts are zero", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    getDocumentsMock.mockResolvedValue([
      baseDoc({
        finding_counts: {
          high: { verified: 0, unverified: 0 },
          medium: { verified: 0, unverified: 0 },
          low: { verified: 0, unverified: 0 },
          info: { verified: 0, unverified: 0 },
        },
      }),
    ]);
    renderPage();

    expect(await screen.findByText("no issues detected by automated review")).toBeInTheDocument();
  });

  it("shows no severity badges when the job hasn't succeeded", async () => {
    getCurrentUserMock.mockResolvedValue(SELF);
    getDocumentsMock.mockResolvedValue([
      baseDoc({
        latest_job: { job_id: "job-1", state: "running", created_at: "2026-09-20T00:00:00+00:00", finished_at: null },
        finding_counts: null,
      }),
    ]);
    renderPage();

    await screen.findByText("Analysis in progress...");
    expect(screen.queryByText("no issues detected by automated review")).not.toBeInTheDocument();
    expect(screen.queryByText(/High|Medium|Low|Info/)).not.toBeInTheDocument();
  });
});
