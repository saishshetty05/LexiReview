import { useEffect, useRef, useState } from "react";
import type { Decision } from "../types/decision";
import type { Finding } from "../types/finding";
import { CategoryBadge } from "./CategoryBadge";
import { SeverityBadge } from "./SeverityBadge";

interface FindingCardProps {
  finding: Finding;
  decision: Decision;
  onAccept: () => void;
  onDismiss: () => void;
  onUndo: () => void;
}

export function FindingCard({ finding, decision, onAccept, onDismiss, onUndo }: FindingCardProps) {
  const isUnverified = finding.verification === "unverified";
  const [confirmingAccept, setConfirmingAccept] = useState(false);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!confirmingAccept) return;
    confirmButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setConfirmingAccept(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmingAccept]);

  function handleAcceptClick() {
    if (isUnverified) {
      setConfirmingAccept(true);
    } else {
      onAccept();
    }
  }

  function handleConfirm() {
    onAccept();
    setConfirmingAccept(false);
  }

  const isAccepted = decision === "accepted";
  const isDismissed = decision === "dismissed";

  return (
    <article
      className={`rounded-lg border p-4 shadow-sm transition-opacity ${
        isAccepted
          ? "border-green-300 border-l-4 border-l-green-500 bg-white"
          : isUnverified
            ? "border-amber-300 border-l-4 border-l-amber-500 bg-amber-50"
            : "border-slate-200 bg-white"
      } ${isDismissed ? "opacity-50" : ""}`}
    >
      {isUnverified && (
        <p className="mb-3 inline-block rounded bg-amber-500 px-2 py-1 text-xs font-bold uppercase tracking-wide text-white">
          Unverified — quote could not be matched to the document
        </p>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <CategoryBadge category={finding.category} />
        <SeverityBadge severity={finding.severity} />
        {finding.block_ids.map((blockId) => (
          <span
            key={blockId}
            className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-600"
          >
            {blockId}
          </span>
        ))}

        <div className="ml-auto flex items-center gap-2">
          {isAccepted && (
            <>
              <span className="rounded-full border border-green-300 bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-800">
                Accepted
              </span>
              <button
                type="button"
                onClick={onUndo}
                className="text-xs text-slate-500 underline hover:text-slate-700"
              >
                Undo
              </button>
            </>
          )}
          {isDismissed && (
            <>
              <span className="rounded-full border border-slate-300 bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700">
                Dismissed
              </span>
              <button
                type="button"
                onClick={onUndo}
                className="text-xs text-slate-500 underline hover:text-slate-700"
              >
                Undo
              </button>
            </>
          )}
          {decision === "pending" && !confirmingAccept && (
            <>
              <button
                type="button"
                onClick={handleAcceptClick}
                className="rounded border border-green-600 px-2 py-0.5 text-xs font-semibold text-green-700 hover:bg-green-50"
              >
                Accept
              </button>
              <button
                type="button"
                onClick={onDismiss}
                className="rounded border border-slate-400 px-2 py-0.5 text-xs font-semibold text-slate-600 hover:bg-slate-100"
              >
                Dismiss
              </button>
            </>
          )}
        </div>
      </div>

      {confirmingAccept && (
        <div className="mb-3 rounded border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="mb-2">
            This finding is unverified — accepting means overriding the automated check. Are you
            sure?
          </p>
          <div className="flex gap-2">
            <button
              ref={confirmButtonRef}
              type="button"
              onClick={handleConfirm}
              className="rounded bg-amber-600 px-2 py-1 text-xs font-semibold text-white hover:bg-amber-700"
            >
              Confirm
            </button>
            <button
              type="button"
              onClick={() => setConfirmingAccept(false)}
              className="rounded border border-amber-400 px-2 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {finding.evidence_quote && (
        <blockquote className="mb-2 border-l-2 border-slate-300 pl-3 text-sm italic text-slate-600">
          “{finding.evidence_quote}”
        </blockquote>
      )}

      <p className="text-sm text-slate-800">{finding.explanation}</p>
    </article>
  );
}
