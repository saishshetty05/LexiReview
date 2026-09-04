import { Loader2 } from "lucide-react";
import { Navigate } from "react-router-dom";

import { useCurrentUserQuery } from "@/hooks/queries";
import { useAuth } from "@/hooks/useAuth";

// Real server-truth check (CONTRACTS.md §3(c)) -- unlike ProtectedRoute's
// client-side isKnownLoggedIn optimism, is_admin can't be guessed client-side
// (there's no local record of it), so this always waits on GET /auth/me
// before deciding what to render. A non-admin (or a request that 403s for
// any reason) is redirected to /dashboard rather than shown a bare error --
// this route only exists for admins in the first place.
export function AdminRoute({ children }: { children: React.ReactNode }) {
  const { isKnownLoggedIn } = useAuth();
  const currentUserQuery = useCurrentUserQuery(isKnownLoggedIn);

  if (!isKnownLoggedIn) return <Navigate to="/login" replace />;

  if (currentUserQuery.isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (currentUserQuery.isError || !currentUserQuery.data?.is_admin) {
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
}
