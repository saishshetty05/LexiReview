// Query/mutation hooks wrapping lib/api.ts's real request functions. This
// is a caching/loading-state layer on top of the existing fetch wrapper --
// api.ts's functions and ApiError taxonomy are unchanged; nothing here
// re-implements request logic.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Decision } from "@/types/decision";
import type { Finding, Severity } from "@/types/finding";
import {
  type AdminUserRow,
  type JobStatus,
  adminListUsers,
  adminSetUserActive,
  deleteAccount,
  getCurrentUser,
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

// Both mutations change server-side mfa_enabled. Cancelling the in-flight
// ["mfa-status"] fetch before invalidating stops a request that started
// before this mutation (and so still carries pre-mutation data) from
// landing in the cache after it -- otherwise that stale response overwrites
// the mfaState the component just set locally.
export function useMFAVerifySetupMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: mfaVerifySetup,
    onSuccess: async () => {
      await queryClient.cancelQueries({ queryKey: ["mfa-status"] });
      queryClient.invalidateQueries({ queryKey: ["mfa-status"] });
    },
  });
}

export function useMFADisableMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: mfaDisable,
    onSuccess: async () => {
      await queryClient.cancelQueries({ queryKey: ["mfa-status"] });
      queryClient.invalidateQueries({ queryKey: ["mfa-status"] });
    },
  });
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

// ── Current user / admin ────────────────────────────────────────────────────

export function useCurrentUserQuery(enabled = true) {
  return useQuery({
    queryKey: ["current-user"],
    queryFn: getCurrentUser,
    enabled,
    staleTime: 60_000, // is_admin/active changes are rare; avoid refetching on every nav
  });
}

export function useAdminUsersQuery(enabled: boolean) {
  return useQuery({
    queryKey: ["admin-users"],
    queryFn: adminListUsers,
    enabled,
  });
}

// Optimistic toggle, same shape as useSetFindingDecisionMutation: update the
// cached row immediately, roll back on failure. A failed mutation (e.g.
// cannot_self_suspend) surfaces via ApiError for the caller to toast.
// onSettled invalidates regardless of outcome -- defense-in-depth so the
// list eventually reflects server truth even if the optimistic write and
// the real result ever disagree, not just on the rollback path.
export function useSetUserActiveMutation() {
  const queryClient = useQueryClient();
  const queryKey = ["admin-users"];

  return useMutation({
    mutationFn: ({ userId, active }: { userId: string; active: boolean }) => adminSetUserActive(userId, active),
    onMutate: async ({ userId, active }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<AdminUserRow[]>(queryKey);
      queryClient.setQueryData<AdminUserRow[]>(queryKey, (old) =>
        old?.map((u) => (u.user_id === userId ? { ...u, active } : u)),
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });
}
