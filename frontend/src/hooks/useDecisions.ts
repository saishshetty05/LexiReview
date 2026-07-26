import { useSetFindingDecisionMutation } from "@/hooks/queries";
import type { Decision } from "@/types/decision";

// CONTRACTS.md §7 (v1.7): decisions are now persisted server-side and
// travel on each Finding (finding.decision, from GET /jobs/{id}/findings) --
// this hook no longer owns any decision state itself (no more localStorage,
// no more a per-jobId Record<index, Decision>), just the write side.
export function useDecisions(jobId: string | undefined) {
  const mutation = useSetFindingDecisionMutation(jobId);

  function setDecision(findingId: string, decision: Decision) {
    mutation.mutate({ findingId, decision });
  }

  return { setDecision };
}
