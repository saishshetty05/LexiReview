import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronsUpDown, LogOut, Menu, Settings, User } from "lucide-react";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { SidebarBrand, SidebarNav } from "@/components/layout/Sidebar";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { useAuth } from "@/hooks/useAuth";
import { useLogoutAndInvalidate } from "@/hooks/queries";

export function Topbar({ title }: { title: string }) {
  const navigate = useNavigate();
  const { clearSession } = useAuth();
  const logout = useLogoutAndInvalidate();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  async function handleLogout() {
    try {
      await logout.mutateAsync();
    } catch {
      // Best-effort, same as before -- the cookie is cleared server-side
      // either way, and there's nothing actionable to show the user here.
    } finally {
      clearSession();
      navigate("/login", { replace: true });
    }
  }

  return (
    <header className="flex h-14 items-center gap-3 border-b bg-background px-4">
      <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileNavOpen(true)}>
          <Menu />
        </Button>
        <SheetContent side="left" className="w-60 p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SidebarBrand />
          <SidebarNav onNavigate={() => setMobileNavOpen(false)} />
        </SheetContent>
      </Sheet>

      <h1 className="flex-1 truncate text-sm font-medium text-foreground">{title}</h1>

      {/* Org-switcher stub -- no real org/tenant concept exists yet (this
          phase's UI-only scope); this is a placeholder for the future
          multi-tenant work, not wired to anything real. */}
      <Button
        variant="outline"
        size="sm"
        className="hidden gap-2 sm:flex"
        onClick={() => toast("Team workspaces are coming soon.")}
      >
        Personal workspace
        <ChevronsUpDown className="h-3.5 w-3.5 opacity-60" />
      </Button>

      <ThemeToggle />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="rounded-full">
            <Avatar className="h-8 w-8">
              <AvatarFallback>
                <User className="h-4 w-4" />
              </AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>My account</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate("/settings")}>
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleLogout}>
            <LogOut className="mr-2 h-4 w-4" />
            Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
