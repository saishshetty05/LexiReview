import { Navigate } from "react-router-dom";

import { useAuth } from "@/hooks/useAuth";

// Optimistic client-side gate only. GET /auth/me exists now (CONTRACTS.md
// §3(c)), but this route deliberately doesn't call it -- it just avoids
// flashing protected content when we already know (from this browser, this
// device) that no login has happened, without adding a request to every
// protected page's mount. Every page underneath still gets a real 401 from
// its own first request if the cookie is missing/expired, and reacts via
// isAuthError()/clearSession() the same as before. AdminRoute is the one
// place that does need the real check, since is_admin can't be guessed
// client-side the way "some login happened" can.
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isKnownLoggedIn } = useAuth();
  if (!isKnownLoggedIn) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
