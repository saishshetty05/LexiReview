import { useCallback, useEffect, useState } from "react";

import type { Decision } from "@/types/decision";

// Upgrade from the old pure in-memory useState (lost on refresh) to
// localStorage, keyed by jobId. Still a client-side-only stopgap -- there is
// no `decisions` table or reviewer-identity concept on the backend yet
// (PROJECT_STATUS.md §5 / PRD FR-13), so this does not survive a different
// browser/device and is not shared between reviewers. It's a real usability
// improvement (a refresh no longer silently discards review progress)
// without pretending to be more durable than it is.
function storageKey(jobId: string): string {
  return `lexireview-decisions-${jobId}`;
}

function readStoredDecisions(jobId: string): Record<number, Decision> {
  try {
    const raw = localStorage.getItem(storageKey(jobId));
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function useDecisions(jobId: string | undefined) {
  const [decisions, setDecisions] = useState<Record<number, Decision>>(() =>
    jobId ? readStoredDecisions(jobId) : {},
  );

  useEffect(() => {
    setDecisions(jobId ? readStoredDecisions(jobId) : {});
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    localStorage.setItem(storageKey(jobId), JSON.stringify(decisions));
  }, [jobId, decisions]);

  const setDecision = useCallback((index: number, decision: Decision) => {
    setDecisions((prev) => ({ ...prev, [index]: decision }));
  }, []);

  return { decisions, setDecision };
}
