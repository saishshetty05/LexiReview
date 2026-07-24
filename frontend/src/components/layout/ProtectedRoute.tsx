import { Navigate } from "react-router-dom";

import { useAuth } from "@/hooks/useAuth";

// Optimistic client-side gate only -- there's still no GET /auth/me, so this
// cannot verify the cookie is actually valid. It just avoids flashing
// protected content when we already know (from this browser, this device)
// that no login has happened. Every page underneath still gets a real 401
// from its own first request if the cookie is missing/expired, and reacts
// via isAuthError()/clearSession() the same as before.
export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isKnownLoggedIn } = useAuth();
  if (!isKnownLoggedIn) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
