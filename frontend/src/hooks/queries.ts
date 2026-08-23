// Query/mutation hooks wrapping lib/api.ts's real request functions. This
// is a caching/loading-state layer on top of the existing fetch wrapper --
// api.ts's functions and ApiError taxonomy are unchanged; nothing here
// re-implements request logic.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Decision } from "@/types/decision";
import type { Finding, Severity } from "@/types/finding";
import {
  type JobStatus,
  deleteAccount,
  getDocumentFile,
  getDocumentSummary,
  getDocuments,
  getJob,
  getJobFindings,
  login,
  logout,
  mfaChallenge,
  mfaDisable,
  mfaSetup,
  mfaStatus,
  mfaVerifySetup,
  putFindingDecision,
  register,
  uploadDocument,
} from "@/lib/api";

export function useLoginMutation() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => login(email, password),
  });
}

export function useRegisterMutation() {
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => register(email, password),
  });
}

export function useLogoutMutation() {
  return useMutation({ mutationFn: logout });
}

export function useDeleteAccountMutation() {
  return useMutation({ mutationFn: deleteAccount });
}

export function useUploadMutation() {
  return useMutation({ mutationFn: (file: File) => uploadDocument(file) });
}

// Self-polling per CONTRACTS.md §3(a): refetchInterval stops itself once the
// job reaches a terminal state, same behavior as the old hand-rolled
// setTimeout loop, now expressed declaratively.
export function useJobQuery(jobId: string | undefined) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data as JobStatus | undefined;
      if (!data || data.state === "queued" || data.state === "running") return 3000;
      return false;
    },
  });
}

export function useJobFindingsQuery(jobId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["job-findings", jobId],
    queryFn: () => getJobFindings(jobId as string),
    enabled: !!jobId && enabled,
  });
}

export function useDocumentSummaryQuery(docId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["document-summary", docId],
    queryFn: () => getDocumentSummary(docId as string),
    enabled: !!docId && enabled,
    retry: false, // 404/absent summary is a normal, expected outcome, not a transient failure to retry
  });
}

export function useDocumentFileQuery(docId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["document-file", docId],
    queryFn: () => getDocumentFile(docId as string),
    enabled: !!docId && enabled,
    staleTime: Infinity, // immutable per document version -- no reason to refetch
  });
}

// CONTRACTS.md §7 (v1.7): optimistically updates the cached findings list
// (the same ["job-findings", jobId] cache useJobFindingsQuery reads) so the
// UI reflects a decision instantly, then rolls back on failure -- findings
// carry their own `decision` field now, so there is no separate decisions
// cache to keep in sync.
export function useSetFindingDecisionMutation(jobId: string | undefined) {
  const queryClient = useQueryClient();
  const queryKey = ["job-findings", jobId];

  return useMutation({
    mutationFn: ({
      findingId,
      decision,
      severityOverride,
    }: {
      findingId: string;
      decision: Decision;
      severityOverride?: Severity | null;
    }) =>
      severityOverride === undefined
        ? putFindingDecision(jobId as string, findingId, decision)
        : putFindingDecision(jobId as string, findingId, decision, severityOverride),
    onMutate: async ({ findingId, decision, severityOverride }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<Finding[]>(queryKey);
      queryClient.setQueryData<Finding[]>(queryKey, (old) =>
        old?.map((f) =>
          f.finding_id === findingId
            ? { ...f, decision, ...(severityOverride !== undefined && { severity_override: severityOverride }) }
            : f,
        ),
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
    },
  });
}

export function useLogoutAndInvalidate() {
  const queryClient = useQueryClient();
  const mutation = useLogoutMutation();
  return {
    ...mutation,
    mutateAsync: async () => {
      try {
        await mutation.mutateAsync();
      } finally {
        queryClient.clear();
      }
    },
  };
}

export function useDocumentsQuery() {
  return useQuery({
    queryKey: ["documents"],
    queryFn: () => getDocuments(),
  });
}

// ── MFA mutations ────────────────────────────────────────────────────────────

export function useMFASetupMutation() {
  return useMutation({ mutationFn: mfaSetup });
}

export function useMFAVerifySetupMutation() {
  return useMutation({ mutationFn: mfaVerifySetup });
}

export function useMFADisableMutation() {
  return useMutation({ mutationFn: mfaDisable });
}

export function useMFAChallengeMutation() {
  return useMutation({ mutationFn: mfaChallenge });
}

export function useMFAStatusQuery() {
  return useQuery({
    queryKey: ["mfa-status"],
    queryFn: mfaStatus,
  });
}
