import { Link } from "react-router-dom";
import { FileScan } from "lucide-react";

import { ThemeToggle } from "@/components/layout/ThemeToggle";

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-muted/30">
      <header className="flex items-center justify-between p-4">
        <Link to="/" className="flex items-center gap-2 text-sm font-semibold">
          <FileScan className="h-5 w-5 text-primary" />
          LexiReview
        </Link>
        <ThemeToggle />
      </header>
      <main className="flex flex-1 items-center justify-center p-4">{children}</main>
    </div>
  );
}
