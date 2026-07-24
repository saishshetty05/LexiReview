import { useNavigate } from "react-router-dom";
import { FileText, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AppShell } from "@/components/layout/AppShell";

// No GET /documents (or any list-my-documents-or-jobs) endpoint exists yet
// -- only single-resource GET /jobs/{id} and GET /documents/{id}/summary.
// Rather than fabricate fake "recent documents" rows (which would actively
// mislead a real user into thinking real history was lost or shown), this
// page always renders the empty state honestly until a real list endpoint
// exists. See docs/PROJECT_STATUS.md §5 -- deferred deliberately, not an
// oversight.
export function DashboardPage() {
  const navigate = useNavigate();

  return (
    <AppShell title="Dashboard">
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted">
          <FileText className="h-8 w-8 text-muted-foreground" />
        </div>
        <div>
          <h2 className="text-lg font-semibold">No documents yet</h2>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            Upload a contract to get started. LexiReview will check it for risky clauses, missing
            terms, and internal inconsistencies.
          </p>
        </div>
        <Button onClick={() => navigate("/upload")}>
          <Upload />
          Upload a document
        </Button>
      </div>
    </AppShell>
  );
}
