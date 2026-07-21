import { useNavigate } from "react-router-dom";
import { logout } from "../lib/api";

export function AppHeader({ title }: { title: string }) {
  const navigate = useNavigate();

  async function handleLogout() {
    // Best-effort: the cookie is cleared server-side either way, and there's
    // nothing meaningful to show the user if this particular call fails.
    try {
      await logout();
    } finally {
      navigate("/login", { replace: true });
    }
  }

  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
      <h1 className="text-sm text-slate-500">{title}</h1>
      <button
        type="button"
        onClick={handleLogout}
        className="text-xs text-slate-500 underline hover:text-slate-700"
      >
        Log out
      </button>
    </header>
  );
}
