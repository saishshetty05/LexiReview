import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CategoryBadge } from "@/components/shared/CategoryBadge";
import { SeverityBadge } from "@/components/shared/SeverityBadge";
import { cn } from "@/lib/utils";
import type { Decision } from "@/types/decision";
import type { Finding } from "@/types/finding";

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

  // Constitution rule 6: an unverified finding must never be accepted
  // without an explicit, focus-managed confirmation step -- this behavior
  // is preserved exactly from the pre-redesign FindingCard, restyled only.
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
    <Card
      className={cn(
        "transition-opacity",
        isAccepted && "border-l-4 border-l-severity-low",
        isUnverified && !isAccepted && "border-l-4 border-l-severity-medium bg-severity-medium/5",
        isDismissed && "opacity-50",
      )}
    >
      <CardContent className="p-4">
        {isUnverified && (
          <Badge variant="severity-medium" className="mb-3">
            Unverified — quote could not be matched to the document
          </Badge>
        )}

        <div className="mb-2 flex flex-wrap items-center gap-2">
          <CategoryBadge category={finding.category} />
          <SeverityBadge severity={finding.severity} />
          {finding.block_ids.map((blockId) => (
            <span
              key={blockId}
              className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
            >
              {blockId}
            </span>
          ))}

          <div className="ml-auto flex items-center gap-2">
            {isAccepted && (
              <>
                <Badge variant="secondary">Accepted</Badge>
                <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={onUndo}>
                  Undo
                </Button>
              </>
            )}
            {isDismissed && (
              <>
                <Badge variant="outline">Dismissed</Badge>
                <Button variant="link" size="sm" className="h-auto p-0 text-xs" onClick={onUndo}>
                  Undo
                </Button>
              </>
            )}
            {decision === "pending" && !confirmingAccept && (
              <>
                <Button variant="outline" size="sm" onClick={handleAcceptClick}>
                  Accept
                </Button>
                <Button variant="ghost" size="sm" onClick={onDismiss}>
                  Dismiss
                </Button>
              </>
            )}
          </div>
        </div>

        {confirmingAccept && (
          <div className="mb-3 rounded-md border border-severity-medium/40 bg-severity-medium/10 p-3 text-sm">
            <p className="mb-2">
              This finding is unverified — accepting means overriding the automated check. Are you
              sure?
            </p>
            <div className="flex gap-2">
              <Button ref={confirmButtonRef} size="sm" onClick={handleConfirm}>
                Confirm
              </Button>
              <Button variant="outline" size="sm" onClick={() => setConfirmingAccept(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {finding.evidence_quote && (
          <blockquote className="mb-2 border-l-2 pl-3 text-sm italic text-muted-foreground">
            “{finding.evidence_quote}”
          </blockquote>
        )}

        <p className="text-sm">{finding.explanation}</p>
      </CardContent>
    </Card>
  );
}
