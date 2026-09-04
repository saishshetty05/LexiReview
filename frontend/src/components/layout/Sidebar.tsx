import { NavLink } from "react-router-dom";
import { FileScan, LayoutDashboard, Settings, ShieldCheck, Upload } from "lucide-react";

import { cn } from "@/lib/utils";
import { useCurrentUserQuery } from "@/hooks/queries";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/settings", label: "Settings", icon: Settings },
];

const ADMIN_NAV_ITEM = { to: "/admin/users", label: "Admin", icon: ShieldCheck };

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  // Best-effort: a non-admin never sees the link (avoids an inviting dead
  // end), but AdminRoute is the real gate -- this check alone would not stop
  // direct navigation to /admin/users.
  const { isKnownLoggedIn } = useAuth();
  const currentUserQuery = useCurrentUserQuery(isKnownLoggedIn);
  const items = currentUserQuery.data?.is_admin ? [...NAV_ITEMS, ADMIN_NAV_ITEM] : NAV_ITEMS;

  return (
    <nav className="flex flex-1 flex-col gap-1 px-3 py-4">
      {items.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            )
          }
        >
          <Icon className="h-4 w-4" />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function SidebarBrand() {
  return (
    <div className="flex h-14 items-center gap-2 border-b px-4">
      <FileScan className="h-5 w-5 text-primary" />
      <span className="text-sm font-semibold">LexiReview</span>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r bg-card md:flex">
      <SidebarBrand />
      <SidebarNav />
    </aside>
  );
}
