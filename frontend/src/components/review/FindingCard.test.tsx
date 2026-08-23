import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FindingCard } from "@/components/review/FindingCard";
import type { Finding } from "@/types/finding";

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: "finding-1",
    decision: "pending",
    category: "liability",
    severity: "high",
    severity_override: null,
    block_ids: ["BLOCK_1"],
    evidence_quote: "the quote text",
    explanation: "why this matters",
    verification: "verified",
    confidence: "standard",
    ...overrides,
  };
}

describe("FindingCard", () => {
  it("a verified finding accepts immediately, no confirmation step", () => {
    const onAccept = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "verified" })}
        decision="pending"
        onAccept={onAccept}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/overriding the automated check/i)).not.toBeInTheDocument();
  });

  it("rule 6: an unverified finding shows a confirmation step instead of accepting immediately", () => {
    const onAccept = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={onAccept}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.getByText(/overriding the automated check/i)).toBeInTheDocument();
  });

  it("rule 6: confirming the unverified-accept dialog calls onAccept and closes it", () => {
    const onAccept = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={onAccept}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/overriding the automated check/i)).not.toBeInTheDocument();
  });

  it("rule 6: cancelling the unverified-accept dialog does not call onAccept", () => {
    const onAccept = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={onAccept}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.queryByText(/overriding the automated check/i)).not.toBeInTheDocument();
  });

  it("rule 6: pressing Escape dismisses the confirmation dialog without accepting", () => {
    const onAccept = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={onAccept}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByText(/overriding the automated check/i)).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(onAccept).not.toHaveBeenCalled();
    expect(screen.queryByText(/overriding the automated check/i)).not.toBeInTheDocument();
  });

  it("rule 6: the Confirm button receives focus when the dialog opens", () => {
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();
  });

  it("dismiss never requires confirmation, verified or not", () => {
    const onDismiss = vi.fn();
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={vi.fn()}
        onDismiss={onDismiss}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("shows an Accepted badge and Undo for an accepted finding", () => {
    const onUndo = vi.fn();
    render(
      <FindingCard
        finding={makeFinding()}
        decision="accepted"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={onUndo}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Accepted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("shows a Dismissed badge and Undo for a dismissed finding", () => {
    const onUndo = vi.fn();
    render(
      <FindingCard
        finding={makeFinding()}
        decision="dismissed"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={onUndo}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Dismissed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("shows the unverified badge for an unverified finding regardless of decision", () => {
    render(
      <FindingCard
        finding={makeFinding({ verification: "unverified" })}
        decision="pending"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/unverified/i)).toBeInTheDocument();
  });

  it("shows the neutral 'Override severity' control when no override is set", () => {
    render(
      <FindingCard
        finding={makeFinding({ severity_override: null })}
        decision="pending"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Override severity")).toBeInTheDocument();
  });

  it("shows the reviewer's override distinctly from the AI's original severity", () => {
    render(
      <FindingCard
        finding={makeFinding({ severity: "high", severity_override: "low" })}
        decision="pending"
        onAccept={vi.fn()}
        onDismiss={vi.fn()}
        onUndo={vi.fn()}
        onSeverityOverrideChange={vi.fn()}
      />,
    );

    // AI's original call stays visible on its own badge...
    expect(screen.getByText("High")).toBeInTheDocument();
    // ...alongside the reviewer's override, not merged into it.
    expect(screen.getByText("Your override: Low")).toBeInTheDocument();
  });
});
