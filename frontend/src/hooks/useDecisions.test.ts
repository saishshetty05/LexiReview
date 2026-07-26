import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useDecisions } from "@/hooks/useDecisions";

beforeEach(() => {
  localStorage.clear();
});

describe("useDecisions", () => {
  it("starts empty for a job with no stored decisions", () => {
    const { result } = renderHook(() => useDecisions("job-1"));
    expect(result.current.decisions).toEqual({});
  });

  it("starts empty when jobId is undefined", () => {
    const { result } = renderHook(() => useDecisions(undefined));
    expect(result.current.decisions).toEqual({});
  });

  it("setDecision updates state and persists to localStorage under a job-scoped key", () => {
    const { result } = renderHook(() => useDecisions("job-1"));

    act(() => {
      result.current.setDecision(0, "accepted");
    });

    expect(result.current.decisions).toEqual({ 0: "accepted" });
    expect(JSON.parse(localStorage.getItem("lexireview-decisions-job-1")!)).toEqual({
      0: "accepted",
    });
  });

  it("loads previously persisted decisions for a job on mount", () => {
    localStorage.setItem("lexireview-decisions-job-1", JSON.stringify({ 2: "dismissed" }));

    const { result } = renderHook(() => useDecisions("job-1"));

    expect(result.current.decisions).toEqual({ 2: "dismissed" });
  });

  it("keeps decisions for different jobs isolated", () => {
    const { result, rerender } = renderHook(({ jobId }) => useDecisions(jobId), {
      initialProps: { jobId: "job-1" },
    });

    act(() => {
      result.current.setDecision(0, "accepted");
    });
    expect(result.current.decisions).toEqual({ 0: "accepted" });

    rerender({ jobId: "job-2" });
    expect(result.current.decisions).toEqual({});

    act(() => {
      result.current.setDecision(0, "dismissed");
    });
    expect(JSON.parse(localStorage.getItem("lexireview-decisions-job-2")!)).toEqual({
      0: "dismissed",
    });
    // job-1's own record is untouched by job-2's write
    expect(JSON.parse(localStorage.getItem("lexireview-decisions-job-1")!)).toEqual({
      0: "accepted",
    });
  });

  it("recovers from corrupted localStorage JSON instead of throwing", () => {
    localStorage.setItem("lexireview-decisions-job-1", "{not valid json");

    const { result } = renderHook(() => useDecisions("job-1"));

    expect(result.current.decisions).toEqual({});
  });

  it("undo (setting back to pending) is stored explicitly, not removed", () => {
    const { result } = renderHook(() => useDecisions("job-1"));

    act(() => {
      result.current.setDecision(0, "accepted");
    });
    act(() => {
      result.current.setDecision(0, "pending");
    });

    expect(result.current.decisions).toEqual({ 0: "pending" });
  });
});
