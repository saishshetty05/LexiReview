import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useDecisions } from "@/hooks/useDecisions";
import type { Finding } from "@/types/finding";

const { putFindingDecisionMock } = vi.hoisted(() => ({ putFindingDecisionMock: vi.fn() }));
vi.mock("@/lib/api", () => ({ putFindingDecision: putFindingDecisionMock }));

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: "finding-1",
    decision: "pending",
    category: "missing_clause",
    severity: "medium",
    severity_override: null,
    block_ids: [],
    evidence_quote: "",
    explanation: "test finding",
    verification: "verified",
    confidence: "standard",
    ...overrides,
  };
}

function makeWrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  putFindingDecisionMock.mockReset();
});

describe("useDecisions", () => {
  it("optimistically updates the cached findings list before the request resolves", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const findings = [makeFinding({ finding_id: "f1" }), makeFinding({ finding_id: "f2" })];
    queryClient.setQueryData(["job-findings", "job-1"], findings);
    let resolveRequest!: (value: { finding_id: string; decision: string }) => void;
    putFindingDecisionMock.mockReturnValue(new Promise((resolve) => (resolveRequest = resolve)));

    const { result } = renderHook(() => useDecisions("job-1"), {
      wrapper: makeWrapper(queryClient),
    });

    act(() => {
      result.current.setDecision("f1", "accepted");
    });

    // Optimistic update lands before the mocked request resolves -- onMutate
    // itself is async (awaits cancelQueries), so this needs waitFor rather
    // than a synchronous assertion right after act().
    await waitFor(() => {
      const cached = queryClient.getQueryData<Finding[]>(["job-findings", "job-1"]);
      expect(cached?.find((f) => f.finding_id === "f1")?.decision).toBe("accepted");
    });
    expect(
      queryClient
        .getQueryData<Finding[]>(["job-findings", "job-1"])
        ?.find((f) => f.finding_id === "f2")?.decision,
    ).toBe("pending");

    resolveRequest({ finding_id: "f1", decision: "accepted" });
    await waitFor(() => expect(putFindingDecisionMock).toHaveBeenCalledTimes(1));
    expect(putFindingDecisionMock).toHaveBeenCalledWith("job-1", "f1", "accepted");
  });

  it("rolls back the optimistic update if the request fails", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const findings = [makeFinding({ finding_id: "f1", decision: "pending" })];
    queryClient.setQueryData(["job-findings", "job-1"], findings);
    putFindingDecisionMock.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useDecisions("job-1"), {
      wrapper: makeWrapper(queryClient),
    });

    act(() => {
      result.current.setDecision("f1", "dismissed");
    });

    // Rolled back to "pending" once the mutation errors.
    await waitFor(() => {
      const cached = queryClient.getQueryData<Finding[]>(["job-findings", "job-1"]);
      expect(cached?.find((f) => f.finding_id === "f1")?.decision).toBe("pending");
    });
  });

  it("undo (setting back to pending) issues the same PUT, not a delete", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["job-findings", "job-1"], [makeFinding({ finding_id: "f1" })]);
    putFindingDecisionMock.mockResolvedValue({ finding_id: "f1", decision: "pending" });

    const { result } = renderHook(() => useDecisions("job-1"), {
      wrapper: makeWrapper(queryClient),
    });

    act(() => {
      result.current.setDecision("f1", "pending");
    });

    await waitFor(() =>
      expect(putFindingDecisionMock).toHaveBeenCalledWith("job-1", "f1", "pending"),
    );
  });

  it("passes severityOverride through to the request and the optimistic cache update", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(
      ["job-findings", "job-1"],
      [makeFinding({ finding_id: "f1", decision: "pending", severity_override: null })],
    );
    putFindingDecisionMock.mockResolvedValue({ finding_id: "f1", decision: "pending", severity_override: "high" });

    const { result } = renderHook(() => useDecisions("job-1"), {
      wrapper: makeWrapper(queryClient),
    });

    act(() => {
      result.current.setDecision("f1", "pending", "high");
    });

    await waitFor(() => {
      const cached = queryClient.getQueryData<Finding[]>(["job-findings", "job-1"]);
      expect(cached?.find((f) => f.finding_id === "f1")?.severity_override).toBe("high");
    });
    expect(putFindingDecisionMock).toHaveBeenCalledWith("job-1", "f1", "pending", "high");
  });

  it("omitting severityOverride (accept/dismiss/undo) leaves any existing override untouched", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(
      ["job-findings", "job-1"],
      [makeFinding({ finding_id: "f1", decision: "pending", severity_override: "high" })],
    );
    putFindingDecisionMock.mockResolvedValue({ finding_id: "f1", decision: "accepted" });

    const { result } = renderHook(() => useDecisions("job-1"), {
      wrapper: makeWrapper(queryClient),
    });

    act(() => {
      result.current.setDecision("f1", "accepted");
    });

    await waitFor(() => {
      const cached = queryClient.getQueryData<Finding[]>(["job-findings", "job-1"]);
      expect(cached?.find((f) => f.finding_id === "f1")?.decision).toBe("accepted");
    });
    expect(
      queryClient.getQueryData<Finding[]>(["job-findings", "job-1"])?.find((f) => f.finding_id === "f1")
        ?.severity_override,
    ).toBe("high");
    expect(putFindingDecisionMock).toHaveBeenCalledWith("job-1", "f1", "accepted");
  });
});
