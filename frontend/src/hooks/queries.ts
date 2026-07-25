// Query/mutation hooks wrapping lib/api.ts's real request functions. This
// is a caching/loading-state layer on top of the existing fetch wrapper --
// api.ts's functions and ApiError taxonomy are unchanged; nothing here
// re-implements request logic.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type JobStatus,
  deleteAccount,
  getDocumentFile,
  getDocumentSummary,
  getJob,
  getJobFindings,
  login,
  logout,
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
