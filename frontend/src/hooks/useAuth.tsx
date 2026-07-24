// There is still no GET /auth/me on the backend, so this can't be a real
// session check -- it's a thin, centralized version of the pattern that was
// previously copy-pasted per page: track "known logged in" client-side,
// set true on a successful login/register, cleared on logout, and let any
// component that gets a missing_token/invalid_token ApiError call
// clearSession() + redirect. This does not replace the backend's own
// enforcement (every protected route still 401s for real) -- it just gives
// the UI a single source of truth to decide what to render before the
// first request comes back, instead of every page guessing independently.
import { createContext, useContext, useState } from "react";

import { ApiError } from "@/lib/api";

interface AuthContextValue {
  isKnownLoggedIn: boolean;
  markLoggedIn: () => void;
  clearSession: () => void;
}

const STORAGE_KEY = "lexireview-known-logged-in";
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isKnownLoggedIn, setIsKnownLoggedIn] = useState(
    () => localStorage.getItem(STORAGE_KEY) === "true",
  );

  function markLoggedIn() {
    localStorage.setItem(STORAGE_KEY, "true");
    setIsKnownLoggedIn(true);
  }

  function clearSession() {
    localStorage.removeItem(STORAGE_KEY);
    setIsKnownLoggedIn(false);
  }

  return (
    <AuthContext.Provider value={{ isKnownLoggedIn, markLoggedIn, clearSession }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

export function isAuthError(err: unknown): err is ApiError {
  return err instanceof ApiError && (err.category === "missing_token" || err.category === "invalid_token");
}
