// There is still no GET /auth/me on the backend, so this can't be a real
// session check -- it's a thin, centralized version of the pattern that was
// previously copy-pasted per page: track "known logged in" client-side,
// set true on a successful login/register, cleared on logout, and let any
// component that gets a missing_token/invalid_token ApiError call
// clearSession() + redirect. This does not replace the backend's own
// enforcement (every protected route still 401s for real) -- it just gives
// the UI a single source of truth to decide what to render before the
// first request comes back, instead of every page guessing independently.
//
// email is stored the same way, for the same reason: POST /auth/login only
// ever returns {user_id}, never email, so AccountSettingsPage has no other
// source for it. What's stored is exactly what the user typed into the
// login/signup form -- accurate as long as it stays true, and there's no
// email-change endpoint that could make it go stale.
import { createContext, useContext, useState } from "react";

import { ApiError } from "@/lib/api";

interface AuthContextValue {
  isKnownLoggedIn: boolean;
  email: string | null;
  markLoggedIn: (email: string) => void;
  clearSession: () => void;
}

const LOGGED_IN_KEY = "lexireview-known-logged-in";
const EMAIL_KEY = "lexireview-known-email";
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isKnownLoggedIn, setIsKnownLoggedIn] = useState(
    () => localStorage.getItem(LOGGED_IN_KEY) === "true",
  );
  const [email, setEmail] = useState<string | null>(() => localStorage.getItem(EMAIL_KEY));

  function markLoggedIn(nextEmail: string) {
    localStorage.setItem(LOGGED_IN_KEY, "true");
    localStorage.setItem(EMAIL_KEY, nextEmail);
    setIsKnownLoggedIn(true);
    setEmail(nextEmail);
  }

  function clearSession() {
    localStorage.removeItem(LOGGED_IN_KEY);
    localStorage.removeItem(EMAIL_KEY);
    setIsKnownLoggedIn(false);
    setEmail(null);
  }

  return (
    <AuthContext.Provider value={{ isKnownLoggedIn, email, markLoggedIn, clearSession }}>
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
